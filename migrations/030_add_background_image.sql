-- The event's own background image, shown behind the Pokémon on the map: a
-- file name from the game's location cards (PokeMiners/pogo_assets,
-- Images/LocationCards, e.g. sb_GoFest2024_radiance.png). Optional: a rule
-- without one is drawn on a plain landscape.
ALTER TABLE poliswag.background_prediction_rule
  ADD COLUMN IF NOT EXISTS image VARCHAR(160) NULL;
