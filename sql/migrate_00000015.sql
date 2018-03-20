CREATE INDEX IF NOT EXISTS idx_transactions_status_v_created ON transactions (status NULLS FIRST, v NULLS LAST, created DESC);
