-- A newly linked player gets Leiria only (owner's decision, 2026-10-10):
-- Marinha Grande is switched on per player. Players already linked keep
-- what they have.
ALTER TABLE shiny_link
  ALTER COLUMN alert_marinha SET DEFAULT 0;
