-- Migration 045
-- 041 dropped the stored per-area quest totals. Three more, for the areas
-- added on 2026-11-05, were created by hand on 2026-11-08 to quiet a build
-- that still wrote them; that code went with 041 and nothing reads or writes
-- these columns. DROP COLUMN IF EXISTS is a no-op once they are gone.
ALTER TABLE poliswag
  DROP COLUMN IF EXISTS quest_expected_batalha,
  DROP COLUMN IF EXISTS quest_expected_portodemos,
  DROP COLUMN IF EXISTS quest_expected_moita;
