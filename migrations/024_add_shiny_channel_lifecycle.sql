-- Mirrors the site's db/pokedex/013_shiny_channels.sql.
-- Keeping the introduction id lets retries finish pinning without reposting.
ALTER TABLE poliswag.shiny_link
  ADD COLUMN IF NOT EXISTS channel_archived TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS channel_intro_message_id BIGINT UNSIGNED NULL;
