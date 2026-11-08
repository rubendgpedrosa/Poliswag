-- Migration 041
-- Quest-scan progress now counts each area's pokestops live (scanner_status
-- _count_area), so the stored per-area ceilings from migration 003 are no
-- longer read or written. 003 is gone, so a replay never re-adds them, and
-- DROP COLUMN IF EXISTS is a no-op once they are gone.
ALTER TABLE poliswag
  DROP COLUMN IF EXISTS quest_expected_leiria,
  DROP COLUMN IF EXISTS quest_expected_marinha;
