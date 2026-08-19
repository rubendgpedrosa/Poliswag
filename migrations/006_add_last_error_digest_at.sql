ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS last_error_digest_at DATETIME NULL;
