-- A lure lives about 30 minutes, and the map knows it is there: the worker
-- returns already_lured without spending anything. This table exists so the bot
-- can say so *before* queueing a job, and so a stop is not re-lured by accident.
CREATE TABLE IF NOT EXISTS lure_lock (
  stop_key    VARCHAR(96) NOT NULL PRIMARY KEY,
  fort_id     VARCHAR(96) NULL,
  lure_type   INT NULL,
  discord_id  BIGINT UNSIGNED NULL,
  locked_until DATETIME NULL,
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
