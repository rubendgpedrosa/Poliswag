-- Maceira (Dragonite area 26, 2026-11-21) is active as soon as Dragonite has it
-- (scan_areas.merge_registry), but the per-player area SETs hold fixed keys.
-- Appending keeps every stored value and bit; MODIFY COLUMN is re-runnable.
ALTER TABLE shiny_link
  MODIFY COLUMN alert_areas SET('leiria','marinha','batalha','portodemos','moita','maceira')
    NOT NULL DEFAULT 'leiria';

ALTER TABLE pogoleiria.trade_player
  MODIFY COLUMN hundo_areas SET('leiria','marinha','batalha','portodemos','moita','maceira')
    NOT NULL DEFAULT 'leiria,marinha';

ALTER TABLE pogoleiria.trade_player
  MODIFY COLUMN shiny_areas SET('leiria','marinha','batalha','portodemos','moita','maceira')
    NOT NULL DEFAULT 'leiria';
