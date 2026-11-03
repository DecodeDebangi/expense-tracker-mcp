CREATE TABLE income_types (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (length(trim(name)) > 0),
    is_regular INTEGER NOT NULL DEFAULT 0 CHECK (is_regular IN (0, 1)),
    created_at TEXT NOT NULL,
    archived_at TEXT
) STRICT;

INSERT INTO income_types (name, is_regular, created_at)
SELECT column1, column2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM (VALUES ('Salary', 1), ('Arrear', 0), ('Bonus', 0), ('Extra Income', 0));

CREATE TABLE income (
    id TEXT PRIMARY KEY,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
    received_on TEXT NOT NULL CHECK (received_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    income_type_id INTEGER NOT NULL REFERENCES income_types (id),
    source TEXT,
    period_start TEXT CHECK (period_start IS NULL OR period_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    period_end TEXT CHECK (period_end IS NULL OR period_end GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    note TEXT,
    client_request_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
) STRICT;

CREATE INDEX income_received_on ON income (received_on);
CREATE INDEX income_type_id ON income (income_type_id);

-- SQLite can't relax NOT NULL in place, so the table is rebuilt to make merchant optional.
CREATE TABLE expenses_rebuilt (
    id TEXT PRIMARY KEY,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
    occurred_on TEXT NOT NULL CHECK (occurred_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    merchant TEXT CHECK (merchant IS NULL OR length(trim(merchant)) > 0),
    category_id INTEGER NOT NULL REFERENCES categories (id),
    subcategory_id INTEGER REFERENCES subcategories (id),
    funded_by_income_id TEXT REFERENCES income (id),
    note TEXT,
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'import')),
    import_hash TEXT UNIQUE,
    client_request_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
) STRICT;

INSERT INTO expenses_rebuilt (
    id, amount_minor, currency, occurred_on, merchant, category_id, subcategory_id, note, source,
    import_hash, client_request_id, created_at, updated_at, deleted_at
)
SELECT
    id, amount_minor, currency, occurred_on, merchant, category_id, subcategory_id, note, source,
    import_hash, client_request_id, created_at, updated_at, deleted_at
FROM expenses;

DROP TABLE expenses;
ALTER TABLE expenses_rebuilt RENAME TO expenses;

CREATE INDEX expenses_occurred_on ON expenses (occurred_on);
CREATE INDEX expenses_category_id ON expenses (category_id);
CREATE INDEX expenses_subcategory_id ON expenses (subcategory_id);
CREATE INDEX expenses_funded_by_income_id ON expenses (funded_by_income_id);

CREATE TRIGGER expenses_subcategory_insert
BEFORE INSERT ON expenses
WHEN NEW.subcategory_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM subcategories WHERE id = NEW.subcategory_id AND category_id = NEW.category_id
)
BEGIN
    SELECT RAISE(ABORT, 'subcategory does not belong to the category');
END;

CREATE TRIGGER expenses_subcategory_update
BEFORE UPDATE OF category_id, subcategory_id ON expenses
WHEN NEW.subcategory_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM subcategories WHERE id = NEW.subcategory_id AND category_id = NEW.category_id
)
BEGIN
    SELECT RAISE(ABORT, 'subcategory does not belong to the category');
END;

INSERT OR IGNORE INTO categories (name, created_at)
VALUES ('Home', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO subcategories (category_id, name, created_at)
SELECT c.id, v.column1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM (VALUES ('Appliances'), ('Furniture'), ('Interiors'), ('Repairs')) AS v
JOIN categories c ON c.name = 'Home';
