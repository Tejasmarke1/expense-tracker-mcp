from fastmcp import FastMCP
from contextlib import closing
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "expenses.db")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

mcp = FastMCP("Demo")

# @mcp.tool
# def add(a: int, b: int) -> int:
#     """Add two numbers."""
#     return a + b

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as c, c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT DEFAULT '',
                note TEXT DEFAULT ''
            )
        """)

init_db()

@mcp.tool()
def add_expenses(date, time, amount, category, subcategory, note):
    '''Add a new expense entry to the database.'''
    with closing(sqlite3.connect(DB_PATH)) as c, c:
        cur = c.execute(
                "INSERT INTO expenses(date, time, amount, category, subcategory, note) VALUES (?,?,?,?,?,?)",
            (date, time, amount, category, subcategory, note)
        )
        return {"status": "ok", "id": cur.lastrowid}
    


@mcp.tool()
def list_expenses(start_date: str, end_date: str):
    """List expenses within an inclusive date range, including their IDs."""

    with closing(sqlite3.connect(DB_PATH)) as c, c:
        c.row_factory = sqlite3.Row

        cur = c.execute(
            """
            SELECT id, date, time, amount, category, subcategory, note
            FROM expenses
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC, time DESC, id DESC
            """,
            (start_date, end_date)
        )

        return [dict(row) for row in cur.fetchall()]


@mcp.tool()
def summarize(start_date, end_date, category=None):
    '''Summarize expenses by category within an inclusive date range.'''
    with closing(sqlite3.connect(DB_PATH)) as c, c:
        query = (
            """
            SELECT category, SUM(amount) AS total_amount
            FROM expenses
            WHERE date BETWEEN ? AND ?
            """
        )
        params = [start_date, end_date]

        if category:
            query += " AND category = ?"
            params.append(category)

        query += " GROUP BY category ORDER BY category ASC"

        cur = c.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    
@mcp.resource("expense://categories", mime_type="application/json")
def categories():
    # Read fresh each time so you can edit the file without restarting
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return f.read()

@mcp.tool()
def edit_expense(
    expense_id: int,
    date: str = None,
    time: str = None,
    amount: float = None,
    category: str = None,
    subcategory: str = None,
    note: str = None
):
    """Update an existing expense by ID. Only provided fields are updated."""

    updates = {
        "date": date,
        "time": time,
        "amount": amount,
        "category": category,
        "subcategory": subcategory,
        "note": note
    }

    # Keep only fields the caller wants to update
    updates = {
        key: value
        for key, value in updates.items()
        if value is not None
    }

    if not updates:
        return {"status": "error", "message": "No fields provided to update."}

    with closing(sqlite3.connect(DB_PATH)) as c, c:
        cur = c.execute(
            "SELECT id FROM expenses WHERE id = ?",
            (expense_id,)
        )

        if cur.fetchone() is None:
            return {
                "status": "error",
                "message": f"Expense with ID {expense_id} not found."
            }

        set_clause = ", ".join(
            f"{column} = ?" for column in updates
        )

        values = list(updates.values()) + [expense_id]

        c.execute(
            f"UPDATE expenses SET {set_clause} WHERE id = ?",
            values
        )

        return {
            "status": "ok",
            "id": expense_id,
            "updated_fields": list(updates.keys())
        }

     
@mcp.tool()
def delete_expense(expense_id: int):
    """Delete an expense entry by its ID."""

    with closing(sqlite3.connect(DB_PATH)) as c, c:
        cur = c.execute(
            "DELETE FROM expenses WHERE id = ?",
            (expense_id,)
        )

        if cur.rowcount == 0:
            return {
                "status": "error",
                "message": f"Expense with ID {expense_id} not found."
            }

        return {
            "status": "ok",
            "message": f"Expense with ID {expense_id} deleted successfully."
        }

    

if __name__ == "__main__":
    mcp.run() 