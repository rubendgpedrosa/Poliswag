-- Supplemental verified costumes; do not rewrite masterfile IDs or user flags.
CREATE TABLE IF NOT EXISTS pokemon_costume (
  pokemon_id SMALLINT UNSIGNED NOT NULL,
  form_id INT UNSIGNED NOT NULL DEFAULT 0,
  costume_id SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  name VARCHAR(100) NOT NULL,
  form_name VARCHAR(100) NOT NULL,
  family_id INT UNSIGNED NULL,
  is_costume TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (pokemon_id, form_id, costume_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Modern costume labels override matching existing forms. Legacy costume enums
-- stay in a separate dimension and can never replace the ordinary form-0 row.
CREATE OR REPLACE VIEW pokemon_catalogue AS
SELECT n.pokemon_id, n.form_id, 0 AS costume_id,
       COALESCE(c.name, n.name) COLLATE utf8mb4_unicode_ci AS name,
       COALESCE(c.form_name, n.form_name) COLLATE utf8mb4_unicode_ci AS form_name,
       n.family_id,
       GREATEST(n.is_costume, COALESCE(c.is_costume, 0)) AS is_costume
FROM pokemon_name n
LEFT JOIN pokemon_costume c
  ON c.pokemon_id = n.pokemon_id AND c.form_id = n.form_id AND c.costume_id = 0
UNION ALL
SELECT pokemon_id, form_id, costume_id,
       name COLLATE utf8mb4_unicode_ci, form_name COLLATE utf8mb4_unicode_ci, family_id, is_costume
FROM pokemon_costume
WHERE costume_id > 0;
