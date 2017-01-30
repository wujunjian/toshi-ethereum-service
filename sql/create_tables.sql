CREATE TABLE IF NOT EXISTS transactions (
    transaction_hash VARCHAR,

    from_address VARCHAR NOT NULL,
    to_address VARCHAR NOT NULL,

    value VARCHAR NOT NULL,
    estimated_gas_cost VARCHAR DEFAULT 0,

    created TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
    confirmed TIMESTAMP WITHOUT TIME ZONE,

    -- optional token identifier for the sender
    sender_token_id VARCHAR,

    PRIMARY KEY(transaction_hash)
);

CREATE TABLE IF NOT EXISTS notification_registrations (
    token_id VARCHAR,
    eth_address VARCHAR,

    PRIMARY KEY(token_id, eth_address)
);

CREATE TABLE IF NOT EXISTS push_notification_registrations (
    service VARCHAR,
    registration_id VARCHAR,
    token_id VARCHAR,

    PRIMARY KEY(service, registration_id)
);

CREATE TABLE IF NOT EXISTS last_blocknumber (
    blocknumber INTEGER
);

CREATE INDEX IF NOT EXISTS idx_transactions_from_address_confirmed ON transactions (from_address, confirmed NULLS FIRST);
CREATE INDEX IF NOT EXISTS idx_transactions_to_address_confirmed ON transactions (to_address, confirmed NULLS FIRST);

UPDATE database_version SET version_number = 1;
