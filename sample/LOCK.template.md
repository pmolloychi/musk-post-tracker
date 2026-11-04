# Random sample lock

This file is committed before the beacon pulse below exists. The review app refuses to draw if this file
changes at or after that minute, or if its values don't match the frozen pool.

- frozen_at_ct: FILL
- frame_size: FILL
- frame_sha256: FILL
- beacon_pulse_utc: FILL

How the draw works: the pool is every post Claude had flagged at `frozen_at_ct`, sorted by Post ID, one per line;
`frame_sha256` is the SHA-256 of that text. The seed is the `outputValue` of the NIST Randomness Beacon pulse at
`beacon_pulse_utc` (https://beacon.nist.gov/beacon/2.0/pulse/time/<milliseconds>), in lowercase. Each post is ranked by
SHA-256 of `seed:post_id`, and the 100 lowest are the sample. The full pool and sample are published once all 100 are
reviewed, so anyone can redo the draw.

An earlier draw on 2026-11-03 was discarded because its lock wasn't published before review began.
