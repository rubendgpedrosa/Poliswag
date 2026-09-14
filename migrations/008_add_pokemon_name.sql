-- Migration 008
-- Pokémon and form names from the masterfile, for consumers that only speak
-- SQL. The PoGoLeiria hub joins against this for raid and Max Battle bosses.
-- form_id = 0 is the species row; other rows exist only for named,
-- non-default forms (see modules/pokemon_name_sync.py).

CREATE TABLE IF NOT EXISTS pokemon_name (
  pokemon_id SMALLINT UNSIGNED NOT NULL,
  form_id    INT UNSIGNED      NOT NULL,
  name       VARCHAR(64)       NOT NULL,
  form_name  VARCHAR(64)       NULL,
  PRIMARY KEY (pokemon_id, form_id)
);
