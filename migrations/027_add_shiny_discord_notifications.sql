-- Discord alerts can be disabled while keeping the shiny map on.
-- The bot remembers the last applied preference to confirm each switch-off once.
ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS shiny_notify_discord TINYINT(1) NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS shiny_notify_discord_applied TINYINT(1) NOT NULL DEFAULT 1;
