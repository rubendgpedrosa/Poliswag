-- A linked player's own private channel, created by `!shiny canal`: their
-- map link is pinned there and their shiny alerts go there instead of a DM.
ALTER TABLE shiny_link
  ADD COLUMN IF NOT EXISTS channel_id BIGINT UNSIGNED NULL;
