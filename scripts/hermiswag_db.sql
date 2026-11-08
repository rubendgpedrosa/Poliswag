-- Hermiswag's read-only window on the public map data (CT 105, owner 2026-11-08).
-- Views only, SQL SECURITY DEFINER, over the scanner (golbat), Poliswag and the
-- Dragonite area fences. Never accounts, proxies, workers, players, encounter
-- ids, the scanner's account names or shiny prediction data. The user
-- 'hermiswag'@'172.18.0.1' gets SELECT on this schema only.
CREATE DATABASE IF NOT EXISTS hermiswag CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.pokemon_live AS
SELECT pokemon_id, form, costume, gender, display_pokemon_id, display_pokemon_form,
       is_ditto, lat, lon, expire_timestamp, expire_timestamp_verified,
       first_seen_timestamp, seen_type, atk_iv, def_iv, sta_iv, iv, cp, level,
       weather, size, move_1, move_2, pokestop_id, cell_id
FROM golbat.pokemon;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.spawn_history AS
SELECT first_seen_at, expire_at, expire_verified, pokemon_id, form_id, costume_id,
       gender, lat, lon, seen_type, display_pokemon_id, is_ditto, atk_iv, def_iv,
       sta_iv, cp, level, weather
FROM poliswag.spawn_history;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.pokestop AS
SELECT id, name, lat, lon, url, description, deleted, enabled, ar_scan_eligible,
       power_up_level, lure_id, lure_expire_timestamp,
       quest_type, quest_title, quest_target, quest_rewards, quest_reward_type,
       quest_item_id, quest_reward_amount, quest_pokemon_id, quest_pokemon_form_id,
       quest_expiry,
       alternative_quest_type, alternative_quest_title, alternative_quest_target,
       alternative_quest_rewards, alternative_quest_reward_type,
       alternative_quest_item_id, alternative_quest_reward_amount,
       alternative_quest_pokemon_id, alternative_quest_pokemon_form_id,
       alternative_quest_expiry,
       showcase_pokemon_id, showcase_pokemon_form_id, showcase_pokemon_type_id,
       showcase_ranking_standard, showcase_expiry
FROM golbat.pokestop;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.gym AS
SELECT id, name, lat, lon, url, description, deleted, enabled, team_id,
       available_slots, in_battle, ex_raid_eligible, ar_scan_eligible,
       power_up_level, total_cp, guarding_pokemon_id,
       raid_level, raid_pokemon_id, raid_pokemon_form, raid_pokemon_costume,
       raid_pokemon_gender, raid_pokemon_evolution, raid_pokemon_alignment,
       raid_pokemon_move_1, raid_pokemon_move_2, raid_pokemon_cp, raid_is_exclusive,
       raid_spawn_timestamp, raid_battle_timestamp, raid_end_timestamp
FROM golbat.gym;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.station AS
SELECT id, name, lat, lon, start_time, end_time, is_battle_available, is_inactive,
       battle_level, battle_start, battle_end, battle_pokemon_id,
       battle_pokemon_form, battle_pokemon_costume, battle_pokemon_gender,
       battle_pokemon_move_1, battle_pokemon_move_2, total_stationed_pokemon,
       total_stationed_gmax
FROM golbat.station;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.incident AS
SELECT i.pokestop_id, p.name AS pokestop_name, p.lat, p.lon, i.start,
       i.expiration, i.display_type, i.style, i.`character`, i.confirmed,
       i.slot_1_pokemon_id, i.slot_1_form, i.slot_2_pokemon_id, i.slot_2_form,
       i.slot_3_pokemon_id, i.slot_3_form
FROM golbat.incident i JOIN golbat.pokestop p ON p.id = i.pokestop_id;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.weather AS
SELECT id, level, latitude, longitude, gameplay_condition, severity, warn_weather,
       updated
FROM golbat.weather;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.nests AS
SELECT nest_id, name, lat, lon, area_name, active, pokemon_id, pokemon_form,
       pokemon_avg, pokemon_ratio, pokemon_count, updated
FROM golbat.nests;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.spawnpoint AS
SELECT id, lat, lon, despawn_sec, first_seen, last_seen FROM golbat.spawnpoint;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.route AS
SELECT id, name, description, distance_meters, duration_seconds, start_lat,
       start_lon, end_lat, end_lon, reversible, type, updated
FROM golbat.route;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.pokemon_stats AS SELECT * FROM golbat.pokemon_stats;
CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.pokemon_iv_stats AS SELECT * FROM golbat.pokemon_iv_stats;
CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.pokemon_hundo_stats AS SELECT * FROM golbat.pokemon_hundo_stats;
CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.pokemon_nundo_stats AS SELECT * FROM golbat.pokemon_nundo_stats;
CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.raid_stats AS SELECT * FROM golbat.raid_stats;
CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.quest_stats AS SELECT * FROM golbat.quest_stats;
CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.invasion_stats AS SELECT * FROM golbat.invasion_stats;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.pokemon_name AS
SELECT pokemon_id, form_id, name, form_name, family_id, is_costume FROM poliswag.pokemon_name;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.event AS
SELECT name, start, `end`, event_type, link FROM poliswag.event;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW hermiswag.area AS
SELECT s.area_key, s.label, s.active, s.sort_order, a.geofence
FROM poliswag.scan_area s LEFT JOIN dragonite.area a
  ON a.name COLLATE utf8mb4_unicode_ci = s.dragonite_area COLLATE utf8mb4_unicode_ci;
