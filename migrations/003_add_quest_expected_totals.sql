-- Migration 003
-- Adaptive expected quest totals per area, used by the plateau-based
-- scan-completion detector. Seeded with the current natural ceilings;
-- updated on each successful completion so new pokestops are accommodated.

ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS quest_expected_leiria  INT NOT NULL DEFAULT 371,
  ADD COLUMN IF NOT EXISTS quest_expected_marinha INT NOT NULL DEFAULT 109;
