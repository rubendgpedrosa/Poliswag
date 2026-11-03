-- One row per member who asked for the MB Way support details, so the owner is
-- told who asked and whether it is a first ask or a habit. The payment details
-- themselves never enter this table: only who asked, when, and how often
-- (modules/mbway_requests.py). Re-runnable like every migration here.
CREATE TABLE IF NOT EXISTS mbway_requests (
  discord_id          BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  username            VARCHAR(64)     NULL,
  display_name        VARCHAR(64)     NULL,
  requests            INT UNSIGNED    NOT NULL DEFAULT 0,
  first_requested_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_requested_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
