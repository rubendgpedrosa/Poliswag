-- Shared with the Pokédex's db/pokedex/012; safe to replay at bot startup.
ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS shiny_access_requested_at DATETIME(6) NULL;
