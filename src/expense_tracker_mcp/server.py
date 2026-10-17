
import asyncio
import json
import math
import os
import re
from contextlib import asynccontextmanager
from datetime import date as Date
from pathlib import Path
from typing import AsyncIterator

import asyncpg
from dotenv import load_dotenv
from fastmcp import FastMCP


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent.parent
CATEGORIES_PATH = BASE_DIR / "categories.json"

load_dotenv(PROJECT_DIR / ".env")

DB_SSL = os.environ.get("DB_SSL", "require").lower()

# Single-user mode: every Horizon-authorized caller shares this owner.
# This is NOT per-user identity derived from OAuth.
USER_ID = "personal-expense-owner"

MAX_AMOUNT = 1_000_000_000
MAX_TEXT_LENGTH = 1000

if DB_SSL not in {"disable", "require"}:
    raise RuntimeError("DB_SSL must be 'disable' or 'require'.")

# Horizon manages access to the hosted MCP endpoint.
# No StaticTokenVerifier or MCP_USERS_JSON is used here.
mcp = FastMCP("Expenses")

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()
_db_initialized = False
_db_init_lock = asyncio.Lock()


# ============================================================
# DATABASE
# ============================================================

async def get_pool() -> asyncpg.Pool:
    global _pool

    if _pool is not None and not _pool.is_closing():
        return _pool

    async with _pool_lock:
        if _pool is not None and not _pool.is_closing():
            return _pool

        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL must be configured before database access."
            )

        _pool = await asyncpg.create_pool(
            dsn=database_url,
            min_size=1,
            max_size=10,
            command_timeout=30,
            ssl="require" if DB_SSL == "require" else None,
        )
        return _pool


async def init_db() -> None:
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                user_id TEXT NOT NULL,
                date DATE NOT NULL,
                time TEXT NOT NULL,
                amount DOUBLE PRECISION NOT NULL CHECK (amount > 0),
                category TEXT NOT NULL,
                subcategory TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            )
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_expenses_user_date
            ON expenses(user_id, date)
        """)


async def ensure_db() -> None:
    global _db_initialized

    if _db_initialized:
        return

    async with _db_init_lock:
        if not _db_initialized:
            await init_db()
            _db_initialized = True


@asynccontextmanager
async def get_db() -> AsyncIterator[asyncpg.Connection]:
    await ensure_db()
    pool = await get_pool()

    async with pool.acquire() as conn:
        yield conn


# ============================================================
# VALIDATION
# ============================================================

def validate_text(
    value: str,
    field: str,
    *,
    required: bool = True,
    max_length: int = MAX_TEXT_LENGTH,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string.")

    value = value.strip()

    if required and not value:
        raise ValueError(f"{field} cannot be empty.")

    if len(value) > max_length:
        raise ValueError(f"{field} cannot exceed {max_length} characters.")

    return value


def validate_date(value: str) -> Date:
    value = validate_text(value, "date", max_length=10)

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must use YYYY-MM-DD format.")

    try:
        return Date.fromisoformat(value)
    except ValueError:
        raise ValueError("Invalid calendar date.") from None


def validate_time(value: str) -> str:
    value = validate_text(value, "time", max_length=5)

    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("time must use HH:MM in 24-hour format.")

    return value


def validate_amount(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("amount must be a number.")

    if not math.isfinite(value):
        raise ValueError("amount must be finite.")

    if not 0 < value <= MAX_AMOUNT:
        raise ValueError(
            f"amount must be greater than 0 and at most {MAX_AMOUNT}."
        )

    return float(value)


def validate_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("expense_id must be a positive integer.")
    return value


def validate_date_range(start_date: Date, end_date: Date) -> None:
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date.")


def error_response(message: str) -> dict[str, object]:
    return {"status": "error", "message": message}


# ============================================================
# ADD EXPENSE
# ============================================================

@mcp.tool()
async def add_expenses(
    date: str,
    time: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = "",
) -> dict[str, object]:
    """Add an expense to the personal expense tracker."""
    try:
        expense_date = validate_date(date)
        expense_time = validate_time(time)
        amount = validate_amount(amount)
        category = validate_text(category, "category", max_length=100)
        subcategory = validate_text(subcategory, "subcategory", required=False)
        note = validate_text(note, "note", required=False)

        async with get_db() as conn:
            expense_id = await conn.fetchval("""
                INSERT INTO expenses (
                    user_id, date, time, amount,
                    category, subcategory, note
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
            """, USER_ID, expense_date, expense_time, amount,
                 category, subcategory, note)

        return {"status": "ok", "id": expense_id}

    except ValueError as exc:
        return error_response(str(exc))
    except (asyncpg.PostgresError, OSError):
        return error_response("Could not save the expense.")


# ============================================================
# LIST EXPENSES
# ============================================================

@mcp.tool()
async def list_expenses(
    start_date: str,
    end_date: str,
) -> list[dict[str, object]]:
    """List expenses within a date range."""
    start = validate_date(start_date)
    end = validate_date(end_date)
    validate_date_range(start, end)

    try:
        async with get_db() as conn:
            rows = await conn.fetch("""
                SELECT id, date::text AS date, time, amount,
                       category, subcategory, note
                FROM expenses
                WHERE user_id = $1 AND date BETWEEN $2 AND $3
                ORDER BY date DESC, time DESC, id DESC
            """, USER_ID, start, end)

        return [dict(row) for row in rows]

    except asyncpg.PostgresError:
        raise RuntimeError("Could not retrieve expenses.") from None


# ============================================================
# SUMMARIZE
# ============================================================

@mcp.tool()
async def summarize(
    start_date: str,
    end_date: str,
    category: str | None = None,
) -> list[dict[str, object]]:
    """Summarize spending by category within a date range."""
    start = validate_date(start_date)
    end = validate_date(end_date)
    validate_date_range(start, end)

    try:
        async with get_db() as conn:
            if category is None:
                rows = await conn.fetch("""
                    SELECT category,
                           ROUND(SUM(amount)::numeric, 2)::float8
                               AS total_amount
                    FROM expenses
                    WHERE user_id = $1 AND date BETWEEN $2 AND $3
                    GROUP BY category
                    ORDER BY category
                """, USER_ID, start, end)
            else:
                category = validate_text(
                    category, "category", max_length=100
                )
                rows = await conn.fetch("""
                    SELECT category,
                           ROUND(SUM(amount)::numeric, 2)::float8
                               AS total_amount
                    FROM expenses
                    WHERE user_id = $1
                      AND date BETWEEN $2 AND $3
                      AND category = $4
                    GROUP BY category
                    ORDER BY category
                """, USER_ID, start, end, category)

        return [dict(row) for row in rows]

    except asyncpg.PostgresError:
        raise RuntimeError("Could not summarize expenses.") from None


# ============================================================
# CATEGORIES RESOURCE
# ============================================================

@mcp.resource("expense://categories", mime_type="application/json")
async def categories() -> str:
    """Return configured expense categories."""
    try:
        with CATEGORIES_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return json.dumps(data, indent=2, ensure_ascii=False)

    except FileNotFoundError:
        raise RuntimeError("Categories configuration is missing.") from None
    except json.JSONDecodeError:
        raise RuntimeError("Categories configuration is invalid.") from None
    except OSError:
        raise RuntimeError("Could not read categories configuration.") from None


# ============================================================
# EDIT EXPENSE
# ============================================================

@mcp.tool()
async def edit_expense(
    expense_id: int,
    date: str | None = None,
    time: str | None = None,
    amount: float | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    note: str | None = None,
) -> dict[str, object]:
    """Update supplied fields of an expense."""
    try:
        expense_id = validate_id(expense_id)

        validators = {
            "date": validate_date,
            "time": validate_time,
            "amount": validate_amount,
            "category": lambda v: validate_text(
                v, "category", max_length=100
            ),
            "subcategory": lambda v: validate_text(
                v, "subcategory", required=False
            ),
            "note": lambda v: validate_text(v, "note", required=False),
        }

        supplied = {
            "date": date,
            "time": time,
            "amount": amount,
            "category": category,
            "subcategory": subcategory,
            "note": note,
        }

        updates = {
            key: validators[key](value)
            for key, value in supplied.items()
            if value is not None
        }

        if not updates:
            return error_response("Provide at least one field to update.")

        assignments = [
            f"{column} = ${index}"
            for index, column in enumerate(updates, start=3)
        ]
        params = [USER_ID, expense_id, *updates.values()]

        async with get_db() as conn:
            updated_id = await conn.fetchval(
                f"UPDATE expenses SET {', '.join(assignments)} "
                "WHERE user_id = $1 AND id = $2 RETURNING id",
                *params,
            )

        if updated_id is None:
            return error_response("Expense not found.")

        return {
            "status": "ok",
            "id": updated_id,
            "updated_fields": list(updates.keys()),
        }

    except ValueError as exc:
        return error_response(str(exc))
    except (asyncpg.PostgresError, OSError):
        return error_response("Could not update the expense.")


# ============================================================
# DELETE EXPENSE
# ============================================================

@mcp.tool()
async def delete_expense(expense_id: int) -> dict[str, object]:
    """Delete an expense by its ID."""
    try:
        expense_id = validate_id(expense_id)

        async with get_db() as conn:
            deleted_id = await conn.fetchval("""
                DELETE FROM expenses
                WHERE id = $1 AND user_id = $2
                RETURNING id
            """, expense_id, USER_ID)

        if deleted_id is None:
            return error_response("Expense not found.")

        return {
            "status": "ok",
            "id": deleted_id,
            "message": "Expense deleted successfully.",
        }

    except ValueError as exc:
        return error_response(str(exc))
    except (asyncpg.PostgresError, OSError):
        return error_response("Could not delete the expense.")


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
