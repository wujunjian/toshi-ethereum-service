ALTER TABLE tokens ADD COLUMN format VARCHAR;

CREATE TABLE IF NOT EXISTS token_balances (
    contract_address VARCHAR,
    eth_address VARCHAR,
    value VARCHAR,

    PRIMARY KEY (contract_address, eth_address)
);

CREATE TABLE IF NOT EXISTS token_transactions (
    transaction_id BIGSERIAL PRIMARY KEY,
    contract_address VARCHAR NOT NULL,
    from_address VARCHAR NOT NULL,
    to_address VARCHAR NOT NULL,
    value VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS token_registrations (
    eth_address VARCHAR PRIMARY KEY,
    last_queried TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS idx_token_balance_eth_address ON token_balances (eth_address);
CREATE INDEX IF NOT EXISTS idx_token_registrations_last_queried ON token_registrations (last_queried ASC);
