# Shiny availability review — 10 October 2026

Reviewed the complete 1,025-species master catalog (2,531 form and default-alias rows), all 624 scanner-observed forms and all 1,490 entries in Leek Duck's current checklist. The complete row-level results are in [shiny-availability.csv](2026-10-10-shiny-availability.csv); [unmapped variants](2026-10-10-shiny-unmapped-variants.csv) records every source identifier that cannot be safely translated to a scanner form.

## Findings and corrections

- The previous 5,000-zero-encounter heuristic admitted unreleased species before enough encounters accumulated. Tarountula had 276 encounters and no shiny sightings, and its roll was calculated at 1/512. It remains excluded.
- Requiring a local shiny sighting fixed that false positive but hid 326 scanner-observed forms that already have a released wild shiny. The new release policy restores them without requiring a local first sighting.
- Shiny statistics identify species and form but omit costume. A normal-form confirmation must not grant all costumes. Eligibility now matches species, form AND costume. Exact live confirmations are stored before despawn; unmapped costumes require that confirmation.
- Nine checklist entries have future dates. They are not active. The checklist also lacks exact release-day start times: that day requires a live confirmation; dated grants start on the next UTC day.
- Some historical releases are event-dependent. Unown, Smeargle and Meltan do not get permanent grants from a release list or old counts. They require an exact shiny confirmation in the preceding 30 minutes.
- Map and bot now query the same SQL view, avoiding divergent eligibility rules.

## Source review

| Source | Result | Use |
|---|---|---|
| [WatWowMap masterfile](https://github.com/WatWowMap/Masterfile-Generator) | Maps numeric form IDs and names; contains no released-shiny flag | Exact form identity only |
| [Leek Duck checklist](https://leekduck.com/shiny/) and its `shiny/pms.json` | Current, form-specific release dates; includes nine future entries and costume identifiers using a separate enum | Primary dated release source; ambiguous/unmapped identifiers excluded |
| [PoGoAPI shiny documentation](https://pogoapi.net/documentation/#get-apiv1shiny_pokemonjson) and `shiny_pokemon.json` | 863 species records with wild/raid/egg/evolution flags; mostly species-level | Wild eligibility only, combined with an exact released form; never expands to all variants |
| [Rplus sheet](https://github.com/Rplus/Pokemon-shiny) used by GoRefs | The fetched sheet's latest release date is 2024-10-04; the repository's separate assets are newer but have mixed legacy identifiers | Sheet rejected as a current source |
| [GoRefs](https://github.com/theflyingfool/GoRefs) | Aggregates other sources; checked-in raw snapshots include August 2026 and the stale Rplus sheet | Useful provenance model; not an independent live authority |
| [Poracle shiny map](https://github.com/jms412/PkmnShinyMap) | Last pushed 2024-09-20; documents limited-time exceptions | Rejected as a current allowlist |
| Scanner encounters | Direct evidence for exact encountered variants; daily counts omit costumes | Positive confirmation fallback; exact variants persisted |

The release sync runs daily, retries a failed download after an hour and replaces the snapshot atomically. Failed, empty or badly mapped downloads preserve the last snapshot; its extra release grants expire after two days without refresh. Confirmed sightings remain available.

300 checklist identifiers are unmapped, including separate costume enums, battle-only transformations, placeholders and ambiguous names. They are listed individually rather than granted through a species-wide fallback.

These sources document known releases, not Niantic's private live on/off flags. A temporary disable may therefore go undetected; the 30-minute rule for known event-dependent species is a conservative heuristic, not a direct server status. A shiny on a scanner account also does not establish ticket/account eligibility for every user.

This review changes eligibility. The RNG and rate estimator remain unchanged; no event-rate overrides were active during the review. The estimator still groups rates by species, which can mix forms with different odds and merits a separate review before adding form-specific overrides.

## Validation

- Bot: full suite, 2,086 tests passed (43 pre-existing environment skips).
- SQL: 14 live tests passed against the isolated test schema, including real-view regressions for future dates, release day, stale source data, raid-only eligibility, exact costumes, first sightings and event-dependent forms.
- Source fetch and parser tested with the actual current feeds; Tarountula has no eligibility row.
