ALTER TABLE tokens RENAME COLUMN address TO contract_address;
ALTER TABLE tokens DROP CONSTRAINT tokens_pkey;
ALTER TABLE tokens ADD PRIMARY KEY (contract_address);

ALTER TABLE collectibles ADD COLUMN last_block INTEGER DEFAULT 0;
ALTER TABLE collectibles ADD COLUMN ready BOOLEAN DEFAULT FALSE;
ALTER TABLE collectibles ADD COLUMN url VARCHAR;
-- types
-- 721 - erc721 compatible token
-- 0 - special
-- 1 - erc721 like, but uses custom transfer event
ALTER TABLE collectibles ADD COLUMN type INTEGER DEFAULT 721;

CREATE TABLE IF NOT EXISTS collectible_transfer_events (
    collectible_transfer_event_id SERIAL PRIMARY KEY,
    contract_address VARCHAR,
    name VARCHAR DEFAULT 'Transfer',
    topic_hash VARCHAR DEFAULT '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
    arguments VARCHAR[] DEFAULT ARRAY['address','address','uint256'],
    indexed_arguments BOOLEAN[] DEFAULT ARRAY[FALSE, FALSE, FALSE],
    to_address_offset INTEGER DEFAULT 1,
    token_id_offset INTEGER DEFAULT 2
);
