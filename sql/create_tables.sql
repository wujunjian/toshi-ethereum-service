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

-- Apple push notification registrations
CREATE TABLE IF NOT EXISTS apn_registrations (
    apn_id VARCHAR,
    token_id VARCHAR,

    -- apn_id being the primary key assures only one token id is
    -- linked per device
    PRIMARY KEY(apn_id)
);

-- Google cloud messaging push notification registrations
CREATE TABLE IF NOT EXISTS gcm_registrations (
    gcm_id VARCHAR,
    token_id VARCHAR,

    -- gcm_id being the primary key assures only one token id is
    -- linked per device
    PRIMARY KEY(gcm_id)
);

CREATE TABLE IF NOT EXISTS last_blocknumber (
    blocknumber INTEGER
);

CREATE INDEX IF NOT EXISTS idx_transactions_from_address_confirmed ON transactions (from_address, confirmed NULLS FIRST);
CREATE INDEX IF NOT EXISTS idx_transactions_to_address_confirmed ON transactions (to_address, confirmed NULLS FIRST);
