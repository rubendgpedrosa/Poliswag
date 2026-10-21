# Curated costume catalogue

The repository dataset is `resources/pokemon_costumes.json`. It supplements, never renumbers or clears, the masterfile-backed `pokemon_name` table. `modules/pokemon_costume_sync.py` validates every species/form and costume enum, then only upserts `pokemon_costume`. The scheduler refreshes this after ordinary names. Migration 035 exposes `pokemon_catalogue`: modern labels override the same real form, and old costumes use a separate numeric `costume_id`. Ordinary rows keep costume_id=0.

## Coverage and provenance

329 reviewed game identities: 208 legacy costume enums, 115 reference-backed modern forms and 6 game-master costume forms the reference list omitted (Pikachu VS 2019, Costume 2020, Kariyushi and Doctor, Armored Mewtwo, Sableye Costume 2020), each confirmed against published game artwork. All 322 combined reference entries plus the separate Detective Pikachu Returns 2023 entry are mapped. Source IDs and image numbers are never game identifiers.

Owner references: https://9db.jp/pokego/data/51 (320 source entries), https://www.eurogamer.net/pokemon-go-event-costume-pokemon-party-hat-flower-crown-7002 (deduplicated context and three supplemental variants). Bulbapedia's https://bulbapedia.bulbagarden.net/wiki/Event_Pok%C3%A9mon_(GO) was Cloudflare-blocked and contributed no imported rows. The resource retains per-entry citations, game proto labels, mapping evidence and snapshot checksums. A source listing/artwork does not establish current obtainability or shiny release.

Manual artwork comparison resolved Berry Shirt colors (Taipei, Singapore, Jeju), GO Tour hats, goggles/monocle, Orange Hanbok, Baseball and Batik outfits; also a missing Charmeleon label and a Slowbro image labelled Slowpoke. Existing equivalent party-hat form keys were retained. The source's costume list is not claimed to be every costume ever represented by the game master.

## Preservation and tests

No resets or deletion of player selections. On a disposable MariaDB, website migration 021 was applied twice to the complete private backup: 22394 collection rows, 6 trade rows and 326 Mega rows. Hashes of every original column, including timestamps, stayed identical. New costume IDs default to zero. Legacy typed form-zero/costume-zero wants remain broad; derived missing wants and specific costumes stay exact. Notification deduplication also includes costume identity.

PoGoLeiria contains 637 verified small WebP normal/shiny images (2510274 bytes) and exact artwork mappings. Missing shiny artwork uses the same costume's normal art, not an invented filename or ordinary species. The private backup and raw source snapshots are operational artifacts, not repository content.

## Manual release prerequisites

Production was not deployed or migrated. Preview reads `poliswag_costume_preview_test.pokemon_catalogue` and keeps writes in `pogoleiria_showcase_test`. Do not run automatic deployment from this work.

When explicitly authorized, coordinate both repos: back up the live collection/trade/notice tables; apply the site's additive 021 and 022 migrations before enabling costume-aware bot readers; apply bot migration 035, sync the ordinary names and validated resource, verify both default and legacy identities, then follow each project's established release procedure. The 022 notice-key changes live in the site's manual migration sequence, not the bot's automatic per-start migrations. Do not disable costume identity to bypass a missing prerequisite. Keep ordinary/Mega/trade selections intact. The owner allowed correction or loss only for costumes, but this migration does not need any reset.
