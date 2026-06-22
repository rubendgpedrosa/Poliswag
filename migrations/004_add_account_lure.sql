-- Migration 004
-- Add account_lure table to track lure counts per account

CREATE TABLE IF NOT EXISTS account_lure (
  username VARCHAR(50) NOT NULL PRIMARY KEY,
  nb_lures INT NOT NULL DEFAULT 12
);
