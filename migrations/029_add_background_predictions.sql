-- Background eligibility and probabilities are explicit event rules, never
-- inferred from shiny counts. No global 4% rule is installed by this migration.
CREATE TABLE IF NOT EXISTS poliswag.background_prediction_rule (
  rule_id VARCHAR(96) NOT NULL PRIMARY KEY,
  label VARCHAR(160) NOT NULL,
  pokemon_id SMALLINT UNSIGNED NOT NULL,
  form_id SMALLINT UNSIGNED NOT NULL,
  costume_id SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  encounter_source VARCHAR(16) NOT NULL DEFAULT 'wild',
  probability DOUBLE NOT NULL,
  starts_at BIGINT UNSIGNED NOT NULL,
  ends_at BIGINT UNSIGNED NOT NULL,
  min_lat DOUBLE NOT NULL,
  max_lat DOUBLE NOT NULL,
  min_lon DOUBLE NOT NULL,
  max_lon DOUBLE NOT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 0,
  INDEX active_variant (enabled, pokemon_id, form_id, costume_id, ends_at),
  CHECK (probability > 0 AND probability <= 1),
  CHECK (starts_at < ends_at),
  CHECK (min_lat >= -90 AND max_lat <= 90 AND min_lat <= max_lat),
  CHECK (min_lon >= -180 AND max_lon <= 180 AND min_lon <= max_lon)
);

ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS shiny_notify_background TINYINT(1) NOT NULL DEFAULT 0;
