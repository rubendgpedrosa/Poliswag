-- Persist daily delivery across restarts; the report goes only to MY_ID.
ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS last_shiny_leaderboard_date DATE NULL;
