# Expense Tracker MCP

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for recording and reviewing personal expenses. It uses FastMCP and stores data in a local SQLite database.

## Requirements

- Python 3.14 or newer
- [uv](https://docs.astral.sh/uv/)

## Installation

From the repository root:

```powershell
uv sync
```

This creates or updates the project environment and installs the `fastmcp` dependency.

## Run the MCP server

The HTTP server is the authenticated implementation in `server.py`:

```powershell
uv run fastmcp run fastmcp.json
```

It listens at `http://127.0.0.1:8000/mcp` and loads `DATABASE_URL` and
`MCP_USERS_JSON` from `.env`. Every HTTP request must include a bearer token
whose value is one of the keys in `MCP_USERS_JSON`.

Do not test this endpoint by opening it directly in a browser. A browser visit
does not send the bearer token and correctly receives `401 Unauthorized`.

Configure an HTTP-capable MCP client with the endpoint and an authorization
header. Use the token value from `.env`; do not commit the token to client
configuration files:

```json
{
	"url": "http://127.0.0.1:8000/mcp",
	"headers": {
		"Authorization": "Bearer <token-from-MCP_USERS_JSON>"
	}
}
```

For a raw connectivity check, the request also needs MCP's negotiated `Accept`
header:

```powershell
curl.exe -H "Authorization: Bearer <token-from-MCP_USERS_JSON>" `
	-H "Accept: text/event-stream, application/json" `
	http://127.0.0.1:8000/mcp
```

The older SQLite demo can still be started with:

Start the server with:

```powershell
uv run python -m expense_tracker_mcp.main
```

The SQLite database is created at `src/expense_tracker_mcp/expenses.db` on first startup. The server also reads categories from `src/expense_tracker_mcp/categories.json`, so edits to that file are picked up without restarting the server.

### MCP client configuration

For an MCP client that supports stdio servers, use the equivalent command and arguments:

```json
{
	"command": "uv",
	"args": [
		"run",
		"python",
		"-m",
		"expense_tracker_mcp.main"
	],
	"cwd": "D:\\Data Science\\mcp\\expense-tracker-mcp"
}
```

Adjust the `cwd` value to the location of this checkout.

## Available MCP capabilities

### Tools

- `add_expenses(date, time, amount, category, subcategory, note)` adds an expense and returns its database ID.
- `list_expenses(start_date, end_date)` returns expenses whose dates fall within the inclusive range.
- `summarize(start_date, end_date, category=None)` groups totals by category for the inclusive range. Supplying `category` limits the result to one category.

Use ISO-formatted dates such as `2026-10-15` so SQLite's text-based date comparisons sort correctly.

### Resource

- `expense://categories` returns the contents of `categories.json` as `application/json`.

## Data and schema

The local `expenses` table contains:

| Column | Description |
| --- | --- |
| `id` | Auto-incrementing record ID |
| `date` | Expense date |
| `time` | Expense time |
| `amount` | Numeric expense amount |
| `category` | Top-level category |
| `subcategory` | Optional subcategory |
| `note` | Optional note |

The database is intentionally local and is not committed to version control. Back up `expenses.db` separately if the records are important.

## Current implementation notes

- The `expense-tracker-mcp` console entry point currently runs `expense_tracker_mcp.main()` from `__init__.py`, which prints a greeting. Use the module command above to start the MCP server.
- `add_expenses` accepts a `time` argument, but the current SQL insert does not write that value even though the database column is required. The tool should be corrected before relying on it for new records.