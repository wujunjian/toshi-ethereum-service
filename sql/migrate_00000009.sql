CREATE TABLE IF NOT EXISTS erc20_transactions (
    transaction_id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    from_address VARCHAR NOT NULL,
    to_address VARCHAR NOT NULL,
    value VARCHAR NOT NULL,
);

CREATE TABLE IF NOT EXISTS erc20_balances (
    symbol VARCHAR,
    address VARCHAR,
    value VARCHAR NOT NULL,

    PRIMARY KEY(symbol, address)
);

CREATE INDEX IF NOT EXISTS idx_erc20_balance_address ON erc20_balances (address);
CREATE INDEX IF NOT EXISTS idx_erc20_balance_address_symbol ON erc20_balances (address, symbol ASC);
