-- The private map of a linked player's predicted shinies, at
-- pogoleiria.pt/shiny/<token>. As with the statistics report, the URL is the
-- credential and only the token's SHA-256 is stored.
ALTER TABLE shiny_link
  ADD COLUMN IF NOT EXISTS map_token_hash CHAR(64) NULL;

-- Species whose shiny odds are not the base 1/512 for a while (an event, a
-- Community Day). `rate` is the N in "1 in N"; ends_at NULL means until removed.
CREATE TABLE IF NOT EXISTS shiny_rate (
  pokemon_id SMALLINT UNSIGNED NOT NULL PRIMARY KEY,
  rate       SMALLINT UNSIGNED NOT NULL,
  ends_at    DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
