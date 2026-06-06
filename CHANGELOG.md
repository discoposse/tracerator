# Changelog

All notable changes to this project will be documented in this file.

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
  - Expanded "Impact on Real NVIDIA/AMD Gear", Mac/Apple Silicon notes, and quick starts in `docs/VALIDATING_WITH_AIPERF.md`.
  - Updated root `README.md`, embedded zip READMEs, script help, Streamlit UI, and launchers with toolkit links and usage guidance.
  - New `CHANGELOG.md`.
- Streamlit UI: new "Reuse temperature (sharpness)" slider + updated presets and help text.
- Validation: clearer notes in validator script reports for non-NVIDIA environments (GPU plots require DCGM).

### Changed
- `reuse_bias` now has a much stronger, predictable, and controllable effect on generated KV cache hit ratios (manifest + real `aiperf analyze-trace` + replay).
- Demo in `app.py` updated for parity with production generator (stronger bias logic + computed hit ratio).
- All changes preserve full AIPerf compatibility (`normalize_trace_for_aiperf`, exact `ceil(input_length / 512)` rules, etc.).
- Backward compatible: new params have safe defaults.

### Fixed
- Weak `reuse_bias` behavior on injected/new requests (now amplified for realistic Mooncake-style prefix sharing).

See `docs/VALIDATING_WITH_AIPERF.md` for full usage, examples, and validation tips.
