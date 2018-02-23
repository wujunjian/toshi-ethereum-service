
ALTER TABLE token_transactions ADD COLUMN transaction_log_index INTEGER;
UPDATE token_transactions SET transaction_log_index = 0;
ALTER TABLE token_transactions ALTER COLUMN transaction_log_index SET NOT NULL;

ALTER TABLE token_transactions DROP CONSTRAINT token_transactions_pkey;
ALTER TABLE token_transactions ADD PRIMARY KEY (transaction_id, transaction_log_index);

ALTER TABLE token_transactions ADD COLUMN status VARCHAR;

-- clean up old data to make sure balances are refreshed
DELETE FROM token_balances;
DELETE FROM token_registrations;
