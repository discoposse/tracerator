# Tracerator

Tracerator generates simulated traces from base patterns for replay and modeling.

## Running

Use Docker Compose (recommended):

```bash
docker compose up -d
```

Open http://localhost:8000 .

The page is the UI:

- Choose base
- Set parameters using the sliders
- "Generate" downloads a zip (trace.jsonl + manifest.json)
- "Preview manifest" shows the manifest and sample lines on the page

## Parameters

- base
- scale
- input_mult, output_mult
- reuse_bias
- new_sessions
- modeled_mix
- seed

The output is suitable for replay in modeling tools.

## Notes

This produces output based on statistical patterns from the base collection(s). It is intended for testing and modeling purposes.

See the site/ for the UI source if customizing.

To run locally without Docker:

pip install -r requirements.txt
python app.py

Then open http://localhost:8000

## Old launchers

The run_trace_ui.sh and related scripts in Mooncake/trace_gen/ are legacy and no longer the primary path. The Docker Compose + static UI is the current way. 
