-- Additive PostgreSQL schema for BeerShop CRM 1.0. Application defaults are ORM-managed.
-- The app's create_all bootstraps these same tables; rerunning this DDL is harmless.
CREATE TABLE IF NOT EXISTS crm_customers (
    id SERIAL PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    identity_key VARCHAR(64) NOT NULL,
    name VARCHAR(180) NOT NULL,
    email VARCHAR(254) NOT NULL,
    phone VARCHAR(50) NOT NULL,
    version INTEGER NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    UNIQUE (store_id, identity_key)
);
CREATE TABLE IF NOT EXISTS crm_managers (
    id SERIAL PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    name VARCHAR(100) NOT NULL,
    active BOOLEAN NOT NULL,
    UNIQUE (store_id, name)
);
CREATE TABLE IF NOT EXISTS crm_notes (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES crm_customers(id),
    order_id INTEGER REFERENCES orders(id),
    body VARCHAR(2000) NOT NULL,
    kind VARCHAR(30) NOT NULL,
    request_key VARCHAR(80) NOT NULL UNIQUE,
    created_at VARCHAR(40) NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_order_details (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id),
    customer_id INTEGER NOT NULL REFERENCES crm_customers(id),
    manager_id INTEGER REFERENCES crm_managers(id),
    priority VARCHAR(10) NOT NULL,
    version INTEGER NOT NULL,
    created_at VARCHAR(40)
);
CREATE INDEX IF NOT EXISTS ix_crm_customers_store_id ON crm_customers(store_id);
CREATE INDEX IF NOT EXISTS ix_crm_managers_store_id ON crm_managers(store_id);
CREATE INDEX IF NOT EXISTS ix_crm_notes_customer_id ON crm_notes(customer_id);
CREATE INDEX IF NOT EXISTS ix_crm_notes_order_id ON crm_notes(order_id);
CREATE INDEX IF NOT EXISTS ix_crm_order_details_customer_id ON crm_order_details(customer_id);
CREATE INDEX IF NOT EXISTS ix_crm_order_details_manager_id ON crm_order_details(manager_id);
