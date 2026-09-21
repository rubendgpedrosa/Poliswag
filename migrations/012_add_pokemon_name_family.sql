-- Migration 012
-- Evolution family and costume flag, for the PoGoLeiria collection tracker.
-- family_id is the masterfile's `family` (the base species' dex number) and is
-- only set on species rows (form_id = 0): Mega Venusaur's family string is
-- every species sharing Venusaur's family_id. is_costume is the masterfile
-- form's `isCostume`; the tracker hides those tiles unless a player asks.

ALTER TABLE pokemon_name
  ADD COLUMN IF NOT EXISTS family_id  SMALLINT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS is_costume TINYINT(1) NOT NULL DEFAULT 0;
