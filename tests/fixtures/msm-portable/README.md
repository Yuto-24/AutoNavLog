# Offline real-MSM snapshot

Produced on 2026-09-16 with `scripts/prepare_msm_feed.py`, public
`jma-gpv-weather 0.5.0` APIs, `--start 2026-09-16T03:00:00+00:00 --hours 3
--bounds 31.5 34 130 132`. Runs: 20260915210000 and 20260915180000.
Original RISH listing bodies are in catalog.json; NPZ payloads contain library
source URLs/hashes, fields and provenance. Filenames are SHA-256 digests.
Tests refresh catalog lifetime only; weather times, arrays and provenance stay fixed.
This snapshot is never included in a production build or used as acquisition fallback.
