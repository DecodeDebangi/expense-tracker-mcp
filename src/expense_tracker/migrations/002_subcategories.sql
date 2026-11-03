INSERT OR IGNORE INTO categories (name, created_at)
SELECT column1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM (VALUES ('Family Support'), ('Loans & EMIs'));

CREATE TABLE subcategories (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories (id),
    name TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(name)) > 0),
    created_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE (category_id, name)
) STRICT;

ALTER TABLE expenses ADD COLUMN subcategory_id INTEGER REFERENCES subcategories (id);
CREATE INDEX expenses_subcategory_id ON expenses (subcategory_id);

-- A subcategory must always belong to its expense's category, so totals can't disagree.
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

INSERT INTO subcategories (category_id, name, created_at)
SELECT c.id, v.column2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM (VALUES
    ('Education', 'Books'), ('Education', 'Courses'), ('Education', 'Exam Fees'),
    ('Education', 'Tuition'),
    ('Entertainment', 'Events'), ('Entertainment', 'Games'), ('Entertainment', 'Hobbies'),
    ('Entertainment', 'Movies'), ('Entertainment', 'Outings'),
    ('Family Support', 'Bills Paid'), ('Family Support', 'Gifts'),
    ('Family Support', 'Medical'), ('Family Support', 'Monthly Support'),
    ('Food', 'Cafes & Snacks'), ('Food', 'Dining Out'), ('Food', 'Food Delivery'),
    ('Food', 'Office Meals'),
    ('Groceries', 'Dairy'), ('Groceries', 'Fruits & Vegetables'), ('Groceries', 'Household Supplies'),
    ('Groceries', 'Staples'),
    ('Health', 'Doctor'), ('Health', 'Fitness'), ('Health', 'Health Insurance'),
    ('Health', 'Medical Tests'), ('Health', 'Medicines'),
    ('Loans & EMIs', 'Car Loan'), ('Loans & EMIs', 'Credit Card EMI'),
    ('Loans & EMIs', 'Education Loan'), ('Loans & EMIs', 'Home Loan'),
    ('Loans & EMIs', 'Personal Loan'), ('Loans & EMIs', 'Two-wheeler Loan'),
    ('Other', 'Bank Charges'), ('Other', 'Donations'), ('Other', 'Gifts'),
    ('Other', 'Life Insurance'), ('Other', 'Miscellaneous'),
    ('Rent', 'House Rent'), ('Rent', 'Parking'), ('Rent', 'Society Maintenance'),
    ('Shopping', 'Clothing'), ('Shopping', 'Electronics'), ('Shopping', 'Home & Kitchen'),
    ('Shopping', 'Personal Care'),
    ('Subscriptions', 'Memberships'), ('Subscriptions', 'Music'), ('Subscriptions', 'Software'),
    ('Subscriptions', 'Streaming'),
    ('Transport', 'Cab & Auto'), ('Transport', 'Fuel'), ('Transport', 'Metro & Bus'),
    ('Transport', 'Parking & Tolls'), ('Transport', 'Vehicle Insurance'),
    ('Transport', 'Vehicle Servicing'),
    ('Travel', 'Activities'), ('Travel', 'Flights'), ('Travel', 'Hotels'),
    ('Travel', 'Trains & Buses'),
    ('Utilities', 'Domestic Help'), ('Utilities', 'Electricity'), ('Utilities', 'Gas'),
    ('Utilities', 'Internet'), ('Utilities', 'Mobile'), ('Utilities', 'Water')
) AS v
JOIN categories c ON c.name = v.column1;
