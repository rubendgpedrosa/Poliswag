-- Shiny odds that set themselves (modules/shiny_odds.py).
--
-- A rate is 'manual' when someone set it and 'auto' when the bot read it off
-- the scanner's counts. Automatic rows are rewritten every tick; a manual
-- row wins over an automatic one for as long as it runs.
ALTER TABLE shiny_rate
  ADD COLUMN IF NOT EXISTS source ENUM('manual', 'auto') NOT NULL DEFAULT 'manual';

-- Every encounter the scanner has had with a species and form, and how many
-- were shiny: Golbat's pokemon_shiny_stats added up day by day, since it
-- keeps only a week. never_shiny marks the ones nobody should be promised.
CREATE TABLE IF NOT EXISTS shiny_species (
  pokemon_id  SMALLINT UNSIGNED NOT NULL,
  form_id     SMALLINT UNSIGNED NOT NULL,
  encounters  INT UNSIGNED NOT NULL DEFAULT 0,
  shinies     INT UNSIGNED NOT NULL DEFAULT 0,
  never_shiny TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (pokemon_id, form_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- The last UTC day added to shiny_species; NULL means none yet.
ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS shiny_species_through DATE NULL;
