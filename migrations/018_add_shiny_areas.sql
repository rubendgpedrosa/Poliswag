-- Where a linked player plays: shiny predictions outside it are not sent.
-- Leiria only by default; Marinha Grande is switched on per player. The
-- boundary is Config.MARINHA_LON_MAX, as for quests.
-- alert_marinha defaulted to 1 for the first hours of 2026-10-10; the live
-- table's default was changed by hand when the owner chose Leiria only.
ALTER TABLE shiny_link
  ADD COLUMN IF NOT EXISTS alert_leiria TINYINT(1) NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS alert_marinha TINYINT(1) NOT NULL DEFAULT 0;
