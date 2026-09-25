-- Pokédex 100IV DMs: the setting as of the last delivered confirmation, one
-- number: 0 off, 1 Leiria, 2 Marinha Grande, 3 both (the hundo_areas SET's
-- own bits). modules/hundo_alerts.py writes it with hundo_confirmed_revision.
-- It lets a change undone before its DM go through silently, with no pause,
-- and lets the DM say what changed ("Começaste a seguir também ..."). NULL
-- until the first delivered confirmation after this migration. The same
-- statement is the site's db/009. Re-runnable.
ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS hundo_confirmed_setting TINYINT UNSIGNED NULL;
