-- One registry of scanned areas, so adding an area is a row here plus its
-- Dragonite area, never a code change. Every reader (quests, shiny, hundo,
-- scanner status, public text) follows this table; active = 0 means "not
-- scanned yet" and is ignored by every reader. The seed uses INSERT IGNORE
-- on purpose: a replay must not reset a row the owner has activated/edited.
CREATE TABLE IF NOT EXISTS scan_area (
  area_key       VARCHAR(32) NOT NULL PRIMARY KEY,
  label          VARCHAR(64) NOT NULL,
  dragonite_area VARCHAR(64) NOT NULL,
  poracle_area   VARCHAR(64) NOT NULL,
  alert_role     VARCHAR(64) NOT NULL,
  sort_order     SMALLINT    NOT NULL,
  active         TINYINT(1)  NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO scan_area
  (area_key, label, dragonite_area, poracle_area, alert_role, sort_order, active)
VALUES
  ('leiria',     'Leiria',        'Leiria',     'leiria',       'AlertasLeiria',   1, 1),
  ('marinha',    'Marinha Grande', 'MarinhaGrande', 'marinhagrande', 'AlertasMarinha', 2, 1),
  ('batalha',    'Batalha',       'Batalha',    'batalha',      'AlertasBatalha',  3, 0),
  ('portodemos', 'Porto de Mós',  'PortoDeMos', 'portodemos',   'AlertasPortoDeMos', 4, 0),
  ('moita',      'Moita',         'Moita',      'moita',        'AlertasMoita',    5, 0);

-- Per-player alert areas for shiny predictions, replacing the two flags it
-- was split between. Writers write this column and keep alert_leiria /
-- alert_marinha in step until a later migration drops them.
ALTER TABLE shiny_link
  ADD COLUMN IF NOT EXISTS alert_areas SET('leiria','marinha','batalha','portodemos','moita')
    NOT NULL DEFAULT 'leiria';

-- Backfill only rows still on the column's default: once a row is anything
-- else it has an explicit selection, and a replay must leave it alone.
UPDATE shiny_link
SET alert_areas = CONCAT_WS(
    ',',
    IF(alert_leiria = 1, 'leiria', NULL),
    IF(alert_marinha = 1, 'marinha', NULL)
  )
WHERE alert_areas = 'leiria'
  AND NOT (alert_leiria = 1 AND alert_marinha = 0);

-- 100IV DMs now cover the same five areas. The SET's bit order is the
-- registry's sort_order, which is what hundo_confirmed_setting stores; the
-- default and every stored value stay valid. MODIFY COLUMN is re-runnable.
ALTER TABLE pogoleiria.trade_player
  MODIFY COLUMN hundo_areas SET('leiria','marinha','batalha','portodemos','moita')
    NOT NULL DEFAULT 'leiria,marinha';

-- The site's own copy of where a player hunts shinies (migration 022) takes
-- the same five keys, so saving a newly activated area is not truncated.
ALTER TABLE pogoleiria.trade_player
  MODIFY COLUMN shiny_areas SET('leiria','marinha','batalha','portodemos','moita')
    NOT NULL DEFAULT 'leiria';
