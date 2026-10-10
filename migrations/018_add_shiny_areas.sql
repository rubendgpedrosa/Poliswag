-- Where a linked player plays: shiny predictions outside it are not sent.
-- Both on by default. The boundary is Config.MARINHA_LON_MAX, as for quests.
ALTER TABLE shiny_link
  ADD COLUMN IF NOT EXISTS alert_leiria TINYINT(1) NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS alert_marinha TINYINT(1) NOT NULL DEFAULT 1;
