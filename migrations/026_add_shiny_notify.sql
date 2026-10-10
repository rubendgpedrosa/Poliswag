-- Mirrors the site's db/pokedex/015_shiny_notify.sql; safe to replay at bot startup.
-- shiny_notify_all 1 is every predicted shiny, 0 only those the player lacks;
-- shiny_notify_hundo 1 still notifies an owned one at 15/15/15.
-- The site adds 1 to shiny_settings_revision on every save of the shiny box;
-- the bot copies it to shiny_applied_revision once it has applied the change.
-- shiny_relink 1: the Friend Code changed since the trainer was linked, so
-- the owner's next approval asks for the link again (the site's alone).
ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS shiny_notify_all TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS shiny_notify_hundo TINYINT(1) NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS shiny_settings_revision INT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS shiny_applied_revision INT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS shiny_relink TINYINT(1) NOT NULL DEFAULT 0;
