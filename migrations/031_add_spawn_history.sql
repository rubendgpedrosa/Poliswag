-- Raw scanner history, independent of trainer notification preferences.
CREATE TABLE IF NOT EXISTS poliswag.spawn_history (
  encounter_id VARCHAR(25) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
  first_seen_at BIGINT UNSIGNED NOT NULL,
  first_captured_at BIGINT UNSIGNED NOT NULL,
  last_captured_at BIGINT UNSIGNED NOT NULL,
  expire_at BIGINT UNSIGNED NULL,
  expire_verified TINYINT(1) NOT NULL,
  pokemon_id SMALLINT UNSIGNED NOT NULL,
  form_id SMALLINT UNSIGNED NOT NULL,
  costume_id SMALLINT UNSIGNED NOT NULL,
  gender TINYINT UNSIGNED NULL,
  lat DOUBLE NOT NULL,
  lon DOUBLE NOT NULL,
  seen_type VARCHAR(32) NULL,
  display_pokemon_id SMALLINT UNSIGNED NULL,
  is_ditto TINYINT(1) NOT NULL,
  atk_iv TINYINT UNSIGNED NULL,
  def_iv TINYINT UNSIGNED NULL,
  sta_iv TINYINT UNSIGNED NULL,
  cp SMALLINT UNSIGNED NULL,
  level TINYINT UNSIGNED NULL,
  weather TINYINT UNSIGNED NULL,
  shiny_eligible TINYINT(1) NOT NULL,
  shiny_odds SMALLINT UNSIGNED NOT NULL,
  odds_source VARCHAR(16) NOT NULL,
  prediction_config_at BIGINT UNSIGNED NOT NULL,
  INDEX first_seen_at (first_seen_at),
  CHECK (shiny_odds > 0)
);

CREATE TABLE IF NOT EXISTS poliswag.spawn_history_state (
  id TINYINT UNSIGNED PRIMARY KEY,
  started_at BIGINT UNSIGNED NOT NULL,
  last_capture_at BIGINT UNSIGNED NOT NULL,
  CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS poliswag.spawn_history_gap (
  started_at BIGINT UNSIGNED NOT NULL,
  ended_at BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (started_at, ended_at)
);
