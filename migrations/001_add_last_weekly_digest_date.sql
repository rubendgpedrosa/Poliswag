-- Migration 001
-- Full schema for the poliswag state table

CREATE TABLE IF NOT EXISTS poliswag (
    scanned                 TINYINT(4)   NULL,
    version                 VARCHAR(10)  NULL,
    last_scanned_date       DATETIME     NULL,
    last_weekly_digest_date DATE         NULL
);
