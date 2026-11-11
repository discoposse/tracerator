# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased] - 2026-11

### Added
- Trace pages at `/traces` and `/traces/<id>`, with whole-trace profiles and prefix-chain sessions.
- `scripts/build_trace_profiles.py` writes `site/data/traces/<id>/profile.json` and `sessions.jsonl.gz`.

### Changed
- Trace cards use IBM Plex Sans, one blue accent, and two charts computed from each trace.
- `/profiles` redirects to `/traces`.
- The golden source is labeled Turn-1 warm / Turn-2 resume. Registry traces carry license, source, and citation fields.

### Removed
- Profile page figures and their PNGs.
- The bundled Mooncake FAST '25 PDF, in favor of the USENIX and arXiv pages.
- The Codex SWE-bench Pro source, which had no public URL or license.
- The old golden-source alias route.

## [Unreleased] - 2026-09

### Changed
- Public community edition: vendor-neutral Turn-1 warm / Turn-2 resume traces, generic self-host docs, and Diátaxis documentation under `docs/`.
- Golden source id is `lightinferra-golden`.
- Docker Compose extra-source mounts default to `./external/...` instead of a personal laptop path.
- Production Compose publishes port 8008 as container `tracerator` and no longer requires a private Docker network.

### Removed
- Internal deploy hostnames, private GitHub URLs, and vendor pass/fail copy from the public tree.

## [Unreleased] - 2026-06

### Added
- `reuse_temperature` parameter (default 1.0) to `generate_extended()` for controlling the sharpness of hot prefix selection when `reuse_bias` is high.
  - Lower values (e.g. 0.3-0.6) make selection near-deterministic on the hottest/longest prefixes at high bias.
- Stronger, monotonic bias amplification in `_choose_hit_prefix` and `_generate_hash_list`:
  - Amplified probability of hot reuse (`p_attempt` curve based on `reuse_bias ** 0.55`).
  - Temperature-controlled power-law weighting for peaked selection among hot prefixes.
  - Bias toward committing to longer/deep hit lengths (higher causal block hit ratio) at high `reuse_bias`.
- Better modeling for new/injected content (`new_req_fraction`, `add_new_sessions`): they now strongly respect `reuse_bias` + `reuse_temperature` for deeper prefix hits.
- `demo_reuse_bias_effect()` helper function + CLI (`--demo-reuse`) for validating expected vs. observed `approx_cache_hit_ratio` movement.
- Support for `--slice-duration` in `scripts/validate-with-aiperf.sh` (enables time-sliced plots in `aiperf plot --dashboard`).
- Improved `approx_cache_hit_ratio` computation in manifest (larger causal sample) and in the simple demo (`app.py` now computes it from actual generated prefix overlaps for realism).
- Documentation:
  - Dedicated section in `Mooncake/trace_gen/README.md` on controlling cache hit ratio.
  - Expanded hardware notes and quick starts in `docs/how-to/validate-with-aiperf.md`.
  - Updated root `README.md`, embedded zip READMEs, script help, UI, and launchers with toolkit links and usage guidance.
  - New `CHANGELOG.md`.
- Validation: clearer notes in validator script reports for non-NVIDIA environments (GPU plots require DCGM).

### Changed
- `reuse_bias` now has a much stronger, predictable, and controllable effect on generated KV cache hit ratios (manifest + real `aiperf analyze-trace` + replay).
- Demo in `app.py` updated for parity with production generator (stronger bias logic + computed hit ratio).
- All changes preserve full AIPerf compatibility (`normalize_trace_for_aiperf`, exact `ceil(input_length / 512)` rules, etc.).
- Backward compatible: new params have safe defaults.

### Fixed
- Weak `reuse_bias` behavior on injected/new requests (now amplified for realistic Mooncake-style prefix sharing).

See `docs/how-to/validate-with-aiperf.md` for usage.
