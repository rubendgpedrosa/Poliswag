-- Site/pilot writes settings and increments their revision atomically.
-- Poliswag records the attempted revision; timestamps are audit fields only.
ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS hundo_dms TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hundo_areas SET('leiria','marinha') NOT NULL DEFAULT 'leiria,marinha',
  ADD COLUMN IF NOT EXISTS hundo_settings_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hundo_settings_at DATETIME(6) NULL,
  ADD COLUMN IF NOT EXISTS hundo_confirmed_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hundo_confirmed_at DATETIME(6) NULL,
  ADD COLUMN IF NOT EXISTS hundo_dm_refused_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hundo_dm_refused_at DATETIME(6) NULL;
