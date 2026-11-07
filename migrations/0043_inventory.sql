-- Additive schema. Existing variants.stock remains available quantity and is not rewritten.
CREATE TABLE IF NOT EXISTS stock_movements (
    id SERIAL PRIMARY KEY,
    variant_id INTEGER NOT NULL REFERENCES variants(id),
    order_id INTEGER REFERENCES orders(id),
    event VARCHAR(30) NOT NULL,
    available_delta INTEGER NOT NULL,
    reserved_delta INTEGER NOT NULL,
    reason VARCHAR(300) NOT NULL,
    request_key VARCHAR(80) UNIQUE,
    fingerprint VARCHAR(64),
    at VARCHAR(40) NOT NULL
);
CREATE TABLE IF NOT EXISTS stock_reservations (
    item_id INTEGER PRIMARY KEY REFERENCES order_items(id),
    order_id INTEGER NOT NULL REFERENCES orders(id),
    variant_id INTEGER NOT NULL REFERENCES variants(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    state VARCHAR(20) NOT NULL CHECK (state IN ('reserved','shipped','released')),
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_stock_movements_order_id ON stock_movements(order_id);
CREATE INDEX IF NOT EXISTS ix_stock_movements_variant_id ON stock_movements(variant_id);
CREATE INDEX IF NOT EXISTS ix_stock_reservations_order_id ON stock_reservations(order_id);
CREATE INDEX IF NOT EXISTS ix_stock_reservations_variant_id ON stock_reservations(variant_id);
