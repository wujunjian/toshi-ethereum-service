CREATE TABLE IF NOT EXISTS transactions (
    transaction_hash VARCHAR,

    from_address VARCHAR NOT NULL,
    to_address VARCHAR NOT NULL,

    value VARCHAR NOT NULL,
    estimated_gas_cost VARCHAR DEFAULT 0,

    created TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
    confirmed TIMESTAMP WITHOUT TIME ZONE,

    PRIMARY KEY(transaction_hash)
);

CREATE INDEX IF NOT EXISTS idx_transactions_from_address_confirmed ON transactions (from_address, confirmed NULLS FIRST);
CREATE INDEX IF NOT EXISTS idx_transactions_to_address_confirmed ON transactions (to_address, confirmed NULLS FIRST);
