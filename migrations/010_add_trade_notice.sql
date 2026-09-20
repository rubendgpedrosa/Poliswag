ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS last_trade_notice_at DATETIME NULL;

CREATE TABLE IF NOT EXISTS pogoleiria.trade_notice (
  wanter_id   BIGINT UNSIGNED NOT NULL,
  holder_id   BIGINT UNSIGNED NOT NULL,
  pokemon_id  SMALLINT UNSIGNED NOT NULL,
  form_id     SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  category    ENUM('normal','hundo','lucky','shiny','xxl','xxs') NOT NULL,
  sent_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (wanter_id, holder_id, pokemon_id, form_id, category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
