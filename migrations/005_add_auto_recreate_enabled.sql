ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS auto_recreate_enabled tinyint(1) NOT NULL DEFAULT 1;
