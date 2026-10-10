-- Explicit released forms, with wild eligibility and dated source freshness.
CREATE TABLE IF NOT EXISTS poliswag.shiny_release (
  pokemon_id SMALLINT UNSIGNED NOT NULL,
  form_id SMALLINT UNSIGNED NOT NULL,
  costume_id SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  released_date DATE NOT NULL,
  wild_eligible TINYINT(1) NOT NULL,
  synced_at DATETIME NOT NULL,
  PRIMARY KEY (pokemon_id, form_id, costume_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Exact confirmations survive Golbat despawns. Its daily stats omit costumes.
CREATE TABLE IF NOT EXISTS poliswag.shiny_confirmed_variant (
  pokemon_id SMALLINT UNSIGNED NOT NULL,
  form_id SMALLINT UNSIGNED NOT NULL,
  costume_id SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  last_seen BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (pokemon_id, form_id, costume_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Both the website and bot read this policy, so filters cannot drift apart.
-- Release-day start times are not in the source: wait for a live confirmation
-- on that day, or the next UTC day. Expired sources lose their extra grants.
CREATE OR REPLACE VIEW poliswag.shiny_eligible_variant AS
SELECT pokemon_id, form_id, costume_id FROM poliswag.shiny_release
WHERE wild_eligible = 1 AND released_date < UTC_DATE()
  AND synced_at >= UTC_TIMESTAMP() - INTERVAL 2 DAY
UNION
SELECT pokemon_id, form_id, 0 AS costume_id FROM poliswag.shiny_species
WHERE shinies > 0 AND pokemon_id NOT IN (201,235,808)
UNION
SELECT pokemon_id, form_id, 0 AS costume_id FROM golbat.pokemon_shiny_stats
WHERE `count` > 0 AND pokemon_id NOT IN (201,235,808)
UNION
SELECT pokemon_id, form_id, costume_id FROM poliswag.shiny_confirmed_variant
WHERE pokemon_id NOT IN (201,235,808) OR last_seen >= UNIX_TIMESTAMP() - 1800
UNION
SELECT pokemon_id, COALESCE(form,0) AS form_id, COALESCE(costume,0) AS costume_id
FROM golbat.pokemon WHERE seen_type = 'encounter' AND shiny = 1
  AND COALESCE(costume,0) <> 255
  AND (pokemon_id NOT IN (201,235,808) OR first_seen_timestamp >= UNIX_TIMESTAMP() - 1800);
