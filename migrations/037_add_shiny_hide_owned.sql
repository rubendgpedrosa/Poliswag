-- Shinies previstos: hide the ones the player already has on their own map.
-- Same statement as the site's db/pokedex/023_shiny_hide_owned.sql, so
-- whichever runs first adds the column. Re-runnable; nothing for the bot to
-- do with it beyond keeping the column when it replays the schema.
ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS shiny_hide_owned TINYINT(1) NOT NULL DEFAULT 0;
