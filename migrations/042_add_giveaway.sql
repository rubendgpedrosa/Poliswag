-- Migration 042
-- Sorteios por reação (!sorteio, cogs/giveaways.py): a mensagem do anúncio,
-- o emoji que conta, a hora do sorteio e quem ganhou. Os participantes são as
-- reações da própria mensagem, lidas na hora; não são guardados aqui.
CREATE TABLE IF NOT EXISTS giveaway (
  message_id   BIGINT UNSIGNED  NOT NULL PRIMARY KEY,
  channel_id   BIGINT UNSIGNED  NOT NULL,
  guild_id     BIGINT UNSIGNED  NOT NULL,
  emoji        VARCHAR(100)     NOT NULL,
  draw_at      DATETIME         NOT NULL,
  winners      TINYINT UNSIGNED NOT NULL DEFAULT 1,
  status       ENUM('open','drawn','cancelled') NOT NULL DEFAULT 'open',
  winner_ids   VARCHAR(400)     NULL,
  participants INT UNSIGNED     NULL,
  created_by   BIGINT UNSIGNED  NOT NULL,
  created_at   DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
  drawn_at     DATETIME         NULL,
  KEY giveaway_open (status, draw_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
