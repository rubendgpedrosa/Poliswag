-- Persist an attempt BEFORE contacting Discord. An uncertain send is recovered
-- from Discord history or explicitly retried by the player, never blindly resent.
CREATE TABLE IF NOT EXISTS pogoleiria.hundo_confirmation (
  discord_id BIGINT UNSIGNED NOT NULL,
  revision BIGINT UNSIGNED NOT NULL,
  channel_id BIGINT UNSIGNED NOT NULL,
  nonce VARCHAR(25) NOT NULL,
  status ENUM('sending','sent','refused','uncertain') NOT NULL DEFAULT 'sending',
  message_id BIGINT UNSIGNED NULL,
  started_at DATETIME(6) NOT NULL DEFAULT UTC_TIMESTAMP(6),
  PRIMARY KEY (discord_id, revision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS hundo_health_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hundo_health_state VARCHAR(24) NULL,
  ADD COLUMN IF NOT EXISTS hundo_health_at DATETIME(6) NULL;
