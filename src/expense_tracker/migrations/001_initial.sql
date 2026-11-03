CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (length(trim(name)) > 0),
    created_at TEXT NOT NULL,
    archived_at TEXT
) STRICT;

CREATE TABLE expenses (
    id TEXT PRIMARY KEY,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
    occurred_on TEXT NOT NULL CHECK (occurred_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    merchant TEXT NOT NULL CHECK (length(trim(merchant)) > 0),
    category_id INTEGER NOT NULL REFERENCES categories (id),
    note TEXT,
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'import')),
    import_hash TEXT UNIQUE,
    client_request_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
) STRICT;

CREATE INDEX expenses_occurred_on ON expenses (occurred_on);
CREATE INDEX expenses_category_id ON expenses (category_id);

INSERT INTO categories (name, created_at)
SELECT column1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM (VALUES
    ('Education'), ('Entertainment'), ('Food'), ('Groceries'), ('Health'), ('Other'),
    ('Rent'), ('Shopping'), ('Subscriptions'), ('Transport'), ('Travel'), ('Utilities')
);
