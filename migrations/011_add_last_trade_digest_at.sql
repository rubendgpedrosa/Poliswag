-- Watermark for the 09:00 trade digest: both "what has already been posted"
-- and "when the last post went out", so a restart later the same morning does
-- not send a second one. NULL means the digest has never run; the first tick
-- sets it and posts nothing rather than announcing every list ever written.
ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS last_trade_digest_at DATETIME NULL;
