-- Remembers that the silent-collector warning has already been sent, so a
-- bot restart during an outage does not re-send it, and so the recovery
-- message only goes out to someone who got the warning.
ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS last_tracking_alert_at DATETIME NULL;
