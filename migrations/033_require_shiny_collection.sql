-- An active shiny map requires the Shiny collection; preserve all other categories.
-- Safe to replay: rows already collecting Shiny are left untouched.
UPDATE pogoleiria.trade_player
SET collecting = CONCAT_WS(',', NULLIF(collecting, ''), 'shiny')
WHERE shiny_map = 1 AND left_at IS NULL AND FIND_IN_SET('shiny', collecting) = 0;
