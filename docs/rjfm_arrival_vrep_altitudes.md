# RJFM ARITA / SHIRAHAMA final-VREP altitude policy

When the destination is RJFM and the final route point before the destination is a VREP,
ARITA or SHIRAHAMA automatically plans 1,500 ft MSL. This is an automatic policy;
a pilot-selected manual VREP altitude and its reason remain authoritative until the user
returns the arrival plan to the automatic mode. It is a user-approved Issue #103 planning
rule recorded from this conversation on 2026-09-06, not a published RJFM procedure.

The policy accepts either of these independent identifiers:

- A KML-compatible VREP name: `ARITA` or `有田`, and `SHIRAHAMA` or `白浜`. The
  matcher normalizes Unicode/case and permits surrounding `V-REP`, altitude, and source
  annotations. It retains token boundaries, so `NARITA` is not ARITA.
- A WGS-84 coordinate within 0.5 NM inclusive of the reference coordinate.

| VREP | Reference coordinate | Reference method |
| --- | --- | --- |
| ARITA | 31.94977931375621, 131.3535165268158 | Existing imported KML V-REP coordinate. |
| SHIRAHAMA | WGS-84 direct solution from RJFM ARP, true bearing 160 degrees, 5.8 NM | Reference note: hotel at the tip of Tozaki Cape (戸崎鼻先端のホテル). RJFM ARP is 31.8772222222, 131.4486111111 from `data/reference/default/airports.csv`. |

All other destinations and VREPs continue to use the normal distance rule. The calculation
outcome records `automatic_altitude_rule`, `automatic_altitude_reason`, and an AutoNavLog
rule version so the selected policy is auditable without publishing an internal-source section mapping.
