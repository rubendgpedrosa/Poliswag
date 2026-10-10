-- Shiny prediction (docs/superpowers/specs/2026-10-10-shiny-prediction-design.md).
--
-- One row per Discord user who sent a friend code with !shiny. The host
-- script scripts/shiny_linker turns `pending` into `linked` or `failed`;
-- the friend code is cleared once a row is linked.
-- player_id was VARCHAR(21) for the first hour of 2026-10-10; the live table
-- was widened by hand when a 24-character ID turned up.
CREATE TABLE IF NOT EXISTS shiny_link (
  discord_id   BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  discord_name VARCHAR(64) NOT NULL,
  friend_code  CHAR(12) NULL,
  trainer_name VARCHAR(32) NULL,
  player_id    VARCHAR(32) NULL,
  status       ENUM('pending','linked','failed') NOT NULL DEFAULT 'pending',
  fail_reason  VARCHAR(64) NULL,
  attempts     TINYINT UNSIGNED NOT NULL DEFAULT 0,
  requested_at DATETIME NOT NULL,
  resolved_at  DATETIME NULL,
  announced_at DATETIME NULL,
  KEY status_requested (status, requested_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One row per spawn a player was told about, so nobody hears of it twice;
-- `verdict` is the player's answer to "was it shiny?".
CREATE TABLE IF NOT EXISTS shiny_alert (
  discord_id   BIGINT UNSIGNED NOT NULL,
  encounter_id VARCHAR(25) NOT NULL,
  pokemon_id   SMALLINT UNSIGNED NOT NULL,
  form_id      SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  sent_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  verdict      ENUM('shiny','not_shiny') NULL,
  verdict_at   DATETIME NULL,
  PRIMARY KEY (discord_id, encounter_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Linker switch and bookkeeping, and the alert watermark (a golbat
-- first_seen_timestamp; NULL means never run, and the first tick only sets it).
ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS shiny_linker_enabled TINYINT(1) NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS shiny_linker_disabled_reason VARCHAR(128) NULL,
  ADD COLUMN IF NOT EXISTS shiny_linker_last_run_at DATETIME NULL,
  ADD COLUMN IF NOT EXISTS shiny_linker_alerted_version VARCHAR(16) NULL,
  ADD COLUMN IF NOT EXISTS shiny_alert_watermark INT UNSIGNED NULL;
