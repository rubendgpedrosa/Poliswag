-- Shinies previstos in the Pokédex (the site's
-- /root/docs-archive/PoGoLeiria/superpowers/specs/2026-10-10-shiny-map-in-pokedex-design.md). The same
-- statements are the site's db/pokedex/011_shiny.sql; whichever runs first
-- adds them.
--
-- shiny_map is the player's own switch, shiny_areas where they play (Leiria
-- alone by default), shiny_access the owner's yes-or-no per player. Its
-- default is yes because that is where the feature is going; when this
-- shipped (2026-10-10) every existing player was set to no by hand and the
-- owner to yes, and modules/trade_player_store.py writes a new profile with
-- shiny_settings.new_players_have_access (no row means no).
ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS shiny_map TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS shiny_areas SET('leiria','marinha') NOT NULL DEFAULT 'leiria',
  ADD COLUMN IF NOT EXISTS shiny_access TINYINT(1) NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS pogoleiria.shiny_settings (
  id TINYINT UNSIGNED NOT NULL DEFAULT 1 PRIMARY KEY,
  new_players_have_access TINYINT(1) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
