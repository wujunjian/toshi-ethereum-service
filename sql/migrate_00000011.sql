CREATE TABLE IF NOT EXISTS collectibles (
    contract_address VARCHAR PRIMARY KEY,
    name VARCHAR,
    icon VARCHAR
);

CREATE TABLE IF NOT EXISTS collectible_tokens (
    contract_address VARCHAR,
    token_id VARCHAR,
    owner_address VARCHAR,
    name VARCHAR,
    image VARCHAR,
    description VARCHAR,
    misc VARCHAR,

    PRIMARY KEY(contract_address, token_id)
);
