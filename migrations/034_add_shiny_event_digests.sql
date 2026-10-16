-- Opt-in event summaries. Prepared for a later deployment; not applied live.
ALTER TABLE shiny_link
  ADD COLUMN IF NOT EXISTS event_alerts_enabled TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS event_pokemon_ids TEXT NULL,
  ADD COLUMN IF NOT EXISTS event_digest_at DATETIME NULL;

ALTER TABLE shiny_alert
  ADD COLUMN IF NOT EXISTS prediction_odds SMALLINT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS prediction_draw DOUBLE NULL,
  ADD COLUMN IF NOT EXISTS delivery_status ENUM('pending','sent','failed') NULL,
  ADD COLUMN IF NOT EXISTS delivered_at DATETIME NULL;
