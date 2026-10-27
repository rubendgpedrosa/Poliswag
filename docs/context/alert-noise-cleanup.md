# Alert noise cleanup — 2026-10-15

## Prepared SSH source classification

CT105 `/etc/alloy/config.alloy` redacts IPv4 in `application_journal` before
Loki receives SSH acceptance logs. The existing `home-ssh-outside` rule then
cannot recognise trusted addresses. Do not trust the literal `REDACTED` token.

Insert `scripts/ssh_source_classification.alloy` at the start of that process,
before all redaction stages. It adds only `ssh_source_zone=internal|external`
to accepted SSH logs, with no source IP labels. A source-only replacement after classification redacts IPv6 as well as IPv4;
existing generic redaction stays active.
The ranges are LAN 192.168.1.0/24, loopback 127.0.0.0/8 and ::1, Tailscale
100.64.0.0/10 and fd7a:115c:a1e0::/48, and link local fe80::/10.

Replace `home-ssh-outside` query A with:

```logql
sum by (instance) (count_over_time({job="journal", unit=~"ssh.*", ssh_source_zone="external"} |= "Accepted " [10m]) or count_over_time({job="journal", unit=~"ssh.*", ssh_source_zone=""} |= "Accepted " !~ `(?i)(?:^|\s)Accepted \S+ for \S+ from (?:192\.168\.1\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])|127\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])|100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])|::1|(?:0{1,4}:){7}0{0,3}1|fe[89ab][0-9a-f]:[0-9a-f:]*(?:%[0-9a-z_.-]+)?|fd7a:115c:a1e0:[0-9a-f:]+)\s` [10m])) > bool 0
```

The two selector branches are disjoint; classify external streams independently
of the legacy raw source fallback so 100.1.* and 100.128.* remain alertable.
The fallback uses the same bounded ranges, correcting the old all-100.* trust.
Unclassified historic redacted records remain conservative and can still alert
until their 10 minute query window expires. Classification affects new records.

Offline validation: `python3 scripts/validate_ssh_source_classification.py`.
This validates the snippet's actual regex against independent IP CIDR membership;
it does not replace native Alloy and Loki parser validation. No security events
or notifications are generated. Deployment and live verification are recorded
below by the coordinating agent.

## Live changes and verification

Applied on 2026-10-15:

- CT105 Alloy classifies SSH sources before redaction; native `alloy validate`
  passed and `systemctl reload alloy` succeeded.
- CT107 `home-ssh-outside` uses the two-branch query above. Loki parsed and
  executed it successfully; Grafana's loaded rule contains the new selector.
- CT107 warning repeats are weekly; critical repeats are daily. SSH security
  events use the no-recovery contact, avoiding misleading "all clear" messages
  when the ten-minute observation window merely expires.
- The storage warning starts above 90%; the critical threshold remains 95%.
- HA monitoring pushes include affected services plus explanatory summaries;
  recovery updates use silent, passive pushes.
- Related PoGo host/check transitions are grouped into one Discord embed per
  host, alert name and state, with vantage and sanitized endpoint details.
- Traffic silence is informational logging only; it no longer pushes DMs or
  repeats every twelve hours.

## Household notification policy

HA automation changes were checked with `check_config` and loaded using
`automation.reload` only while no automation runs were active. No devices were
actuated and no test push was sent.

- `1790470000001`: each away question is sent once, with buttons retained for
  thirty minutes and the existing early cancellation/confirmation behavior.
- `1734825040210`: nursery door opening must persist five seconds and the baby
  must be asleep. Door closing or ending sleep clears the tagged notice silently.
- `1790490000001`: batteries/no-signal summary only on Monday at 10:00.
- `1790480000001`: vacuum maintenance summary only on Monday at 10:00; actual
  vacuum errors and water problems retain their event-driven delivery.
- `1791020000001`: location-permission changes notify the affected person
  immediately; no periodic reminder. Unknown/unavailable states are ignored.
- `1790520000001`: home monitoring includes the explanatory summary and makes
  recovery updates silent.

`input_text.battery_alert_notice` and
`input_text.salvador_maintenance_notice` persist summary signatures/timestamps,
preventing repeat unchanged notices during the same week. The weekday gate
ensures new routine issues also wait for the weekly summary, as the owner requested.

Verification: 141 focused Python tests passed. The source-classification
regression covered 3,114 combinations, including CIDR boundaries and privacy.
Native Alloy validation, Loki query execution, HA template rendering and
configuration checks passed. Loaded Grafana rules/routes were inspected; there
were no evaluation-error or NoData alert instances after loading the change.
