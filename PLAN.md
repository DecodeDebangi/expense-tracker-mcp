# Expense Tracker: Implementation Plan

A personal MCP server for logging and evaluating expenses through Claude, built for years of use. It starts local on one Mac, and phone logging comes later (Phase 5).

## Principles

- **Code on GitHub, data never.** The real database lives outside the repo, and backups stay in an ignored folder that CI checks is never tracked.
- **SQL does the maths, Claude does the explaining.** Every figure comes from a query, never from Claude's own arithmetic.
- **Nothing is lost.** Deleting is reversible, backups run automatically, and schema changes go through migrations.
- **Tools don't know where data lives.** Tools talk to a storage layer, so moving to hosted storage later doesn't change them.

## Stack

| Part | Choice | Why |
|---|---|---|
| Language and packages | Python, `uv` | Already installed; a lockfile keeps it runnable years from now |
| MCP | Official `mcp` Python SDK, pinned | Currently 2.3.0; check its API docs at the start of Phase 0 |
| Storage | SQLite | Free, one file, needs no server |
| Transport | stdio now, Streamable HTTP in Phase 5 | Local needs no network or login |
| Clients | Claude Desktop, Claude Code | Both run on this Mac |
| Tests and CI | `pytest`, GitHub Actions | Runs on every push |

**Database location:** `~/Library/Application Support/expense-tracker/expenses.db`, overridable with `EXPENSE_DB_PATH`. Tests use temporary databases.

## Data model

**`expenses`**

| Column | Notes |
|---|---|
| `id` | UUID, so records move to hosted storage without ID clashes |
| `amount_minor` | Integer in paise or cents; must be positive |
| `currency` | ISO code such as `INR`; defaults to a configured currency |
| `occurred_on` | Local date of the expense, separate from when it was logged |
| `merchant` | Original text as entered |
| `category_id` | Required |
| `note` | Optional |
| `source` | `manual` or `import` |
| `import_hash` | Unique when present; stops statement rows importing twice |
| `client_request_id` | Unique when present; makes retried requests safe |
| `created_at`, `updated_at` | UTC timestamps |
| `deleted_at` | Soft delete; hidden from queries unless asked for |

**`categories`:** `id`, unique `name`, `archived_at`. Starts with a small default set. Renaming changes one row, and merging moves the expenses across.

**`budgets`** (Phase 3): `category_id`, `amount_minor`, `currency`, `effective_from`. Keeping the start date means changing a budget doesn't rewrite how past months are judged.

**`merchant_rules`** (Phase 3): maps a merchant pattern to a category, such as Swiggy to Food.

**Multiple currencies:** totals are grouped by currency and never converted. Conversion would need historical exchange rates, so it stays out unless it turns out to be needed.

## Phases

### Phase 0: Repo foundations

- `git init`, `uv` project and pinned dependencies.
- Before the first commit: `.gitignore` covering `*.db`, `*.db-*`, `*.sqlite*`, `exports/`, `backups/`, `statements/` and `.env`.
- CI runs the tests and fails if a database or export file is ever tracked.
- Fake seed data for tests and demos.
- Check the commit identity with `git config --show-origin user.email`, then create the GitHub repo.

**Done when:** CI passes on an empty test suite, and a planted `.db` file is both ignored by git and caught by the CI check when force-added.

### Phase 1: Core logging and backups

- Numbered SQL migrations, recorded in a `schema_version` table. The database is backed up automatically before any migration runs.
- Storage layer holding all the SQL, with a small MCP tool layer on top.
- Tools: `add_expense`, `list_expenses`, `update_expense`, `delete_expense`, `restore_expense`, `list_categories`, `manage_categories`, `summarize`, `backup_status`.
- `add_expense` warns about a likely duplicate (same amount, merchant and date) rather than silently saving it.
- Input checks with clear errors: amounts, dates, currency codes, unknown categories.
- Daily `launchd` backup using SQLite's own backup method, keeping the newest 30 plus one per month for 24 months, plus a tested restore command. Failures show a Mac notification and appear in `backup_status`.
- `export_csv`, writing outside the repo.

**Done when:** tests cover totals, rounding, soft delete and migrations, and a backup has actually been restored into a new database.

### Phase 2: Real use

- Connect it to Claude Desktop and Claude Code.
- Log real expenses for 1–2 weeks.
- Keep a `QUESTIONS.md` of what you wanted to ask and any friction. It uses general wording only, because it's committed.
- Fix what gets in the way.

**Done when:** logging an expense takes one sentence, and there's a real list of questions to shape Phase 3.

### Phase 3: Evaluation

Starting list, to be adjusted using `QUESTIONS.md`:

- `compare_periods`: this month against last, with changes by category.
- `trends`: month-by-month totals over time.
- `top_merchants`: where the money goes.
- `find_recurring`: subscriptions and repeat charges.
- `set_budget` and `budget_status`: spending against limits.
- `find_anomalies`: spending far outside your usual range.
- Merchant rules, so common merchants are categorised automatically.

**Done when:** each tool is tested against seed data with hand-calculated answers, and Claude answers the `QUESTIONS.md` list using tool figures.

### Phase 4: Import and public polish

- CSV import for your bank and card statements: preview first, then confirm, skipping duplicates through `import_hash`. Each bank gets its own small format definition.
- README with setup, tool reference, architecture, and a demo GIF recorded on fake data only.
- Licence and a tagged `v1.0`.

**Done when:** a real statement imports correctly, and importing it again adds nothing.

### Phase 5: Phone logging (long term)

Phones can't reach a server running on a Mac, so this phase hosts it.

1. **Choose hosting at $0**, comparing the free limits of SQLite-compatible options (such as Turso or Cloudflare D1) at the time. Compatible storage should keep the SQL changes small.
2. **Add a hosted storage backend** behind the existing storage layer, running the same test suite.
3. **Add Streamable HTTP transport** alongside stdio.
4. **Add login protection before any real data goes up:** OAuth with access limited to a single user. Check Claude's current remote connector requirements first.
5. **Move the data once** with a script that verifies row counts and totals by month.
6. **Keep one source of truth:** the hosted database becomes primary, the local one is retired, and there's no two-way sync. Backups then run against the hosted database.
7. **Add it as a custom connector** in claude.ai and confirm it works in the Claude mobile app.

**Done when:** an expense logged on the phone appears on the Mac straight away, and an unauthenticated request is refused.

## What's prepared now for Phase 5

- UUID IDs and UTC timestamps.
- SQL kept inside the storage layer.
- Tools that return data and don't assume a local filesystem. CSV export is the exception, and gets reworked in Phase 5.
- Retry-safe writes: an optional `client_request_id` on `add_expense`, so a dropped phone connection can't log an expense twice.

## Out of scope

- Bank account syncing
- A web or mobile app
- Multiple users
- Income tracking or investments
- Paid services of any kind

## Decisions

- [x] Default currency: INR
- [x] Backups: automatic daily copies to the repo's `backups/` folder, which git ignores, so they stay on the Mac only. These don't survive losing the Mac or deleting the repo folder; the user chose this over the setup that Google Drive needs (rclone now requires your own Google client ID).
- [x] GitHub repo: public
- [x] Database: SQLite until Phase 5
- [ ] Phase 5 hosting (decide when Phase 5 starts; Neon is also an option, though it would need some query changes)
