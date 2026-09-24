-- Direct messages about a new mutual trade (modules/trade_dm.py).
--
-- Watermark: spares added after it are looked at. NULL means never run; the
-- first tick sets it and sends nothing, rather than messaging everyone about
-- every trade that already exists.
ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS last_trade_dm_at DATETIME NULL;

-- One row per spare a recipient was told about, so nobody hears of it twice.
CREATE TABLE IF NOT EXISTS pogoleiria.trade_dm_notice (
  recipient_id BIGINT UNSIGNED NOT NULL,
  holder_id    BIGINT UNSIGNED NOT NULL,
  pokemon_id   SMALLINT UNSIGNED NOT NULL,
  form_id      SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  category     ENUM('normal','hundo','lucky','shiny','xxl','xxs') NOT NULL,
  sent_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (recipient_id, holder_id, pokemon_id, form_id, category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- The player's switch, on by default; the site writes it (Perfil e outras
-- opções) and this module only reads it. Also in the site's db/007, both
-- re-runnable, so whichever lands first adds it.
ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS trade_dms TINYINT(1) NOT NULL DEFAULT 1;
