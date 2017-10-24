ALTER TABLE tokens DROP CONSTRAINT tokens_pkey;

ALTER TABLE tokens RENAME TO erc20_tokens;

ALTER TABLE erc20_tokens ADD PRIMARY KEY (address);

ALTER INDEX idx_tokens_address RENAME TO idx_erc20_tokens_address;

CREATE TABLE IF NOT EXISTS erc20_transactions (
    transaction_id BIGSERIAL PRIMARY KEY,
    erc20_address VARCHAR NOT NULL,
    from_address VARCHAR NOT NULL,
    to_address VARCHAR NOT NULL,
    value VARCHAR NOT NULL,
);

CREATE TABLE IF NOT EXISTS erc20_balances (
    erc20_address VARCHAR,
    eth_address VARCHAR,
    value VARCHAR NOT NULL,

    PRIMARY KEY(symbol, address)
);

CREATE TABLE IF NOT EXISTS erc20_registrations (
    eth_address VARCHAR PRIMARY KEY,
    last_queried TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS idx_erc20_balance_eth_address ON erc20_balances (eth_address);
CREATE INDEX IF NOT EXISTS idx_erc20_registrations_last_queried ON erc20_registrations (last_queried ASC);
