# Tracerator UI

Tracerator exposes two linked browser pages when the Flask app is running.

## Trace Generator

Open `http://localhost:8000/` to create generated trace outputs from the available baseline sources. The page provides controls for:

- Baseline trace source
- Trace scenario
- Scale, input/output length, reuse bias, new sessions, modeled mix, and seed
- ISL distribution shaping
- KV cache planning assumptions

Use **Generate & download zip** for the full output bundle, or **Preview manifest + sample** to inspect the generated manifest and first trace records in the browser.

## Trace Comparison

Open `http://localhost:8000/compare` or use the **Trace Comparison** nav link. Load one to five `manifest.json` files to render:

- Shared KPI cards
- A grouped vertical ISL distribution chart
- Detailed horizontal ISL distribution bars
- A comparison breakdown table

The comparison page can export a standalone HTML report, download the vertical chart as a PNG, or print/save the report as a formatted Letter-size PDF.

## Simulation Note

Generated and augmented traces are simulations for planning and replay experiments. They preserve selected workload characteristics from source traces, but they do not necessarily represent exact production workload profiles.
