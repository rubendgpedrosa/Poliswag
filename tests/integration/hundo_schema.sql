-- Live DDL (mariadb-dump --no-data, 2026-09-24) of every table the 100IV
-- alerts SQL touches, before migration 014. Loaded into a disposable server
-- by test_hundo_alerts_sql.py; never run against the live database.

CREATE DATABASE pogoleiria;
USE pogoleiria;
CREATE TABLE `trade_player` (
  `discord_id` bigint(20) unsigned NOT NULL,
  `username` varchar(32) NOT NULL,
  `display_name` varchar(64) NOT NULL,
  `avatar_url` varchar(255) DEFAULT NULL,
  `code_hash` char(64) NOT NULL,
  `code_issued_at` datetime NOT NULL,
  `trainer_name` varchar(32) DEFAULT NULL,
  `friend_code` char(12) DEFAULT NULL,
  `note` varchar(500) DEFAULT NULL,
  `left_at` datetime DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `lists_confirmed_at` datetime DEFAULT NULL,
  `collecting` set('hundo','lucky','shiny','xxl','xxs','normal') NOT NULL DEFAULT '',
  `show_costumes` tinyint(1) NOT NULL DEFAULT 1,
  `trade_dms` tinyint(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (`discord_id`),
  UNIQUE KEY `code_hash` (`code_hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
CREATE TABLE `collection_entry` (
  `discord_id` bigint(20) unsigned NOT NULL,
  `category` enum('hundo','lucky','shiny','xxl','xxs','normal') NOT NULL,
  `pokemon_id` smallint(5) unsigned NOT NULL,
  `form_id` int(10) unsigned NOT NULL DEFAULT 0,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`discord_id`,`category`,`pokemon_id`,`form_id`),
  KEY `idx_tile` (`category`,`pokemon_id`,`form_id`),
  CONSTRAINT `fk_collection_entry_player` FOREIGN KEY (`discord_id`) REFERENCES `trade_player` (`discord_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE DATABASE poliswag;
USE poliswag;
CREATE TABLE `pokemon_name` (
  `pokemon_id` smallint(5) unsigned NOT NULL,
  `form_id` int(10) unsigned NOT NULL,
  `name` varchar(64) NOT NULL,
  `form_name` varchar(64) DEFAULT NULL,
  `family_id` smallint(5) unsigned DEFAULT NULL,
  `is_costume` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`pokemon_id`,`form_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE DATABASE poracle;
USE poracle;
CREATE TABLE `humans` (
  `id` varchar(255) NOT NULL,
  `type` varchar(255) NOT NULL,
  `name` varchar(255) NOT NULL,
  `enabled` tinyint(1) NOT NULL DEFAULT 1,
  `area` text NOT NULL,
  `latitude` float(14,10) NOT NULL DEFAULT 0.0000000000,
  `longitude` float(14,10) NOT NULL DEFAULT 0.0000000000,
  `fails` int(11) NOT NULL DEFAULT 0,
  `last_checked` datetime NOT NULL DEFAULT current_timestamp(),
  `language` varchar(255) DEFAULT NULL,
  `admin_disable` tinyint(1) NOT NULL DEFAULT 0,
  `disabled_date` datetime DEFAULT NULL,
  `current_profile_no` int(11) NOT NULL DEFAULT 1,
  `community_membership` text NOT NULL,
  `area_restriction` text DEFAULT NULL,
  `notes` varchar(255) NOT NULL DEFAULT '',
  `blocked_alerts` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE `profiles` (
  `uid` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `id` varchar(255) NOT NULL,
  `profile_no` int(11) NOT NULL DEFAULT 1,
  `name` varchar(255) NOT NULL,
  `area` text NOT NULL,
  `latitude` float(14,10) NOT NULL DEFAULT 0.0000000000,
  `longitude` float(14,10) NOT NULL DEFAULT 0.0000000000,
  `active_hours` varchar(4096) NOT NULL DEFAULT '[]',
  PRIMARY KEY (`uid`),
  UNIQUE KEY `profile_unique` (`id`,`profile_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE `monsters` (
  `id` varchar(255) NOT NULL,
  `ping` text NOT NULL,
  `clean` tinyint(1) NOT NULL DEFAULT 0,
  `pokemon_id` int(11) NOT NULL,
  `distance` int(11) NOT NULL,
  `min_iv` int(11) NOT NULL,
  `max_iv` int(11) NOT NULL,
  `min_cp` int(11) NOT NULL,
  `max_cp` int(11) NOT NULL,
  `min_level` int(11) NOT NULL,
  `max_level` int(11) NOT NULL,
  `atk` int(11) NOT NULL,
  `def` int(11) NOT NULL,
  `sta` int(11) NOT NULL,
  `template` text DEFAULT NULL,
  `min_weight` int(11) NOT NULL,
  `max_weight` int(11) NOT NULL,
  `form` int(11) NOT NULL,
  `max_atk` int(11) NOT NULL,
  `max_def` int(11) NOT NULL,
  `max_sta` int(11) NOT NULL,
  `gender` int(11) NOT NULL,
  `uid` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `profile_no` int(11) NOT NULL DEFAULT 1,
  `min_time` int(11) NOT NULL DEFAULT 0,
  `rarity` int(11) NOT NULL DEFAULT -1,
  `max_rarity` int(11) NOT NULL DEFAULT 6,
  `pvp_ranking_worst` int(11) NOT NULL DEFAULT 4096,
  `pvp_ranking_best` int(11) NOT NULL DEFAULT 1,
  `pvp_ranking_min_cp` int(11) NOT NULL DEFAULT 1,
  `pvp_ranking_league` int(11) NOT NULL DEFAULT 0,
  `pvp_ranking_cap` int(11) NOT NULL DEFAULT 0,
  `size` int(11) NOT NULL DEFAULT -1,
  `max_size` int(11) NOT NULL DEFAULT 5,
  `override_location_label` varchar(64) DEFAULT NULL,
  `override_areas` text DEFAULT NULL,
  `pvp_ranking_evolution` tinyint(1) NOT NULL DEFAULT 0,
  `costume` int(11) NOT NULL DEFAULT 9000,
  PRIMARY KEY (`uid`),
  KEY `monsters_id_foreign` (`id`),
  KEY `monsters_pvp_ranking_league_pokemon_id_min_iv_index` (`pvp_ranking_league`,`pokemon_id`,`min_iv`),
  KEY `monsters_pvp_ranking_league_pokemon_id_pvp_ranking_worst_index` (`pvp_ranking_league`,`pokemon_id`,`pvp_ranking_worst`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
