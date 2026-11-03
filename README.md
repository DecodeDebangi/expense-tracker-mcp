# Expense Tracker MCP

A personal MCP server for logging and evaluating expenses through Claude. Totals come from SQL rather than Claude's own arithmetic. It's in early development; see [PLAN.md](PLAN.md).

Only code and fake test data live in this repo. Real expenses are stored in a local database outside it, and backups go in a `backups/` folder that git ignores.

## Setup

Requires macOS and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
.venv/bin/expense-tracker-admin init
.venv/bin/expense-tracker-admin install-schedule   # daily backup at 9pm and at login
```

Connect it to Claude Code:

```bash
claude mcp add --scope user expense-tracker -- "$PWD/.venv/bin/expense-tracker"
```

For Claude Desktop, add this to `claude_desktop_config.json` using the full path, then restart the app:

```json
{ "mcpServers": { "expense-tracker": { "command": "/full/path/to/.venv/bin/expense-tracker" } } }
```

## Backups

```bash
.venv/bin/expense-tracker-admin status
.venv/bin/expense-tracker-admin backup --force
.venv/bin/expense-tracker-admin restore backups/<file>.db --force
```

Restoring saves a copy of the current database first. Quit Claude before restoring.

Backups stay on this computer, so they won't survive losing it. Deleting the repo folder or running `git clean -fdx` deletes them too.
