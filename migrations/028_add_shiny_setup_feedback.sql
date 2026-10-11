-- Create channels on approval and update their pinned setup message in place.
ALTER TABLE poliswag.shiny_link
  ADD COLUMN IF NOT EXISTS channel_intro_state VARCHAR(16) NULL,
  ADD COLUMN IF NOT EXISTS link_processing_at DATETIME NULL;
