# Generate your first trace

In this lesson we produce a downloadable Mooncake-style zip from the bundled Conversation baseline.

You need Docker with Compose v2. We use port 8008.

## Start the workspace

From the repository root:

```bash
docker compose up
```

Wait until the `app` service is listening. Open http://localhost:8008. You should see the Tracerator workspace and an empty run list.

If the page does not load, see [How to run the UI](../how-to/run-the-ui.md).

## Create a run

1. Click **New run**.
2. The run detail page opens with a Generate step.

You should see a **Baseline source** dropdown. **Mooncake Conversation** is available without extra downloads.

## Generate a small slice

1. Keep **Mooncake Conversation**.
2. Set **Scale** to `0.05` so the slice stays small.
3. Leave reuse bias and length multipliers at their defaults.
4. Click **Generate**.

When generation finishes, the header shows a request count. Inspect tabs populate. The Overview tab reports integrity OK.

## Download the zip

Click **Download**. The zip contains at least:

- `trace.jsonl`
- `manifest.json`
- a short README

Open `manifest.json` and confirm `base_source.id` is `conversation` and `integrity.aiperf_ready` is true.

## What we built

A reproducible Conversation variant you can hand to AIPerf. Next: [How to validate with AIPerf](../how-to/validate-with-aiperf.md).
