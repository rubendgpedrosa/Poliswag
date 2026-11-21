-- Forced hidden (owner, 2026-11-21): Dragonite decides which areas exist and
-- scan; hidden = 1 keeps an enabled area off the site (map outline, chips,
-- pins, quests tabs) while it scans and Poliswag and Poracle keep using it.
-- The bot does not read it. Re-runnable; data is set by hand, never here.
ALTER TABLE scan_area
  ADD COLUMN IF NOT EXISTS hidden TINYINT(1) NOT NULL DEFAULT 0 AFTER active;
