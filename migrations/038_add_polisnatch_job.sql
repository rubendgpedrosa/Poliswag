-- One row per PoliSnatch job Poliswag submitted, so a restart can resume the
-- ones still queued/claimed and an outcome survives the Discord message it was
-- announced on. The job itself lives on the PoliSnatch server; this is only
-- Poliswag's ledger (modules/polisnatch_store.py). notified_at is set after
-- Discord accepted the outcome, so a crash cannot silently lose it.
CREATE TABLE IF NOT EXISTS polisnatch_job (
  job_id       VARCHAR(40)  NOT NULL PRIMARY KEY,
  kind         VARCHAR(8)   NOT NULL,
  state        VARCHAR(16)  NOT NULL DEFAULT 'queued',
  discord_id   BIGINT UNSIGNED NULL,
  channel_id   BIGINT UNSIGNED NULL,
  message_id   BIGINT UNSIGNED NULL,
  stop_key     VARCHAR(96)  NULL,
  lat          DOUBLE       NULL,
  lon          DOUBLE       NULL,
  nearest      TINYINT(1)   NULL,
  player_id    VARCHAR(64)  NULL,
  trainer_name VARCHAR(64)  NULL,
  verified     TINYINT(1)   NULL,
  fort_id      VARCHAR(128) NULL,
  fail_reason  VARCHAR(64)  NULL,
  submitted_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  notified_at  DATETIME     NULL,
  KEY state_idx (state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
