ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS auto_reboot_enabled tinyint(1) NOT NULL DEFAULT 1;
