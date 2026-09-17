# Evaluating CoffeeBench for Enterprise Agentic Trace Replays

CoffeeBench is a useful upstream workload source for Tracerator/AIPerf, but it should be treated as a multi-agent trace generator and agent-quality benchmark rather than a direct infrastructure replay tool.

Sources:

- Paper: https://arxiv.org/pdf/2606.16613
- Code: https://github.com/SakanaAI/CoffeeBench
- Trajectories: https://pub.sakana.ai/coffeebench/trajectories.html

## What CoffeeBench Adds

CoffeeBench simulates a 90-day coffee supply-chain economy with six autonomous firms: two farmers, two roasters, and two retailers. The focal model controls one roaster while fixed reference agents control the other firms. The benchmark records long-horizon behavior: tool calls, direct messages, transactions, inventory, revenue, net income, idle days, and bankruptcies.

That makes CoffeeBench valuable for agentic enterprise workload modeling because it includes things that plain request-level traces usually miss:

- Long-horizon state growth across hundreds to thousands of agent actions.
- Heterogeneous agents with different roles, tool sets, and objectives.
- Asynchronous reactivation from messages, trade offers, and deliveries.
- Business-process tool mix: marketplace, financial, communication, demand-observability, production, pricing, and wait actions.
- Behavioral failure modes such as idle drift, where an agent reasons coherently but repeatedly chooses no-op/wait actions.
- Quality-side outcomes, especially net income, revenue, deals, inventory health, and communication intensity.

## Best Fit in Our Pipeline

### 1. Trace-source adapter for released trajectories

The highest-value integration is a CoffeeBench trajectory importer that converts released runs into AIPerf-compatible Mooncake-style JSONL:

```json
{"timestamp": 0, "input_length": 90000, "output_length": 800, "hash_ids": [123, 456]}
```

The importer should extract one replay record per LLM call and preserve:

- `timestamp`: simulation day/time or relative wall-clock/event order converted to milliseconds.
- `input_length`: prompt/context tokens for that step.
- `output_length`: generated reasoning/tool-call tokens.
- `hash_ids`: deterministic prefix blocks derived from stable components such as shared system prompt, role prompt, agent id, day bucket, current business state, message thread, and tool category.
- optional metadata in a sidecar manifest: agent role, model, tool name, recipient, event type, day, net-income run id, and source trajectory id.

This would give us enterprise-agentic traces with realistic long-context recurrence, event-driven burstiness, and multi-agent prefix reuse, while keeping `trace.jsonl` clean for `aiperf profile --custom-dataset-type mooncake_trace`.

### 2. Calibration source for `aiperf synthesize`

CoffeeBench should become a guardrail for generated agentic workloads, especially the NVIDIA Agentic Code Dataset Generator / `aiperf synthesize` path.

Use CoffeeBench-derived manifests to calibrate:

- calls per simulated run;
- daily and per-hour tool-call burst shapes;
- average and tail input context sizes;
- output-token distribution by tool category;
- wait/no-op frequency;
- direct-message frequency and recipient fan-out;
- cache-prefix depth from recurring role prompts, state summaries, and message histories;
- active-vs-idle phase transitions over long horizons.

Synthetic agentic traces should not be accepted only because they match request count or median ISL/OSL. They should also match CoffeeBench-style action cadence, tool mix, communication fan-out, long-context reuse, and idle/reactivation behavior.

### 3. Joint quality/performance benchmark

CoffeeBench can be used as the workload driver while AIPerf-style profiling observes the serving stack:

1. Run CoffeeBench against an OpenAI-compatible endpoint or proxy.
2. Capture every model call with request timestamp, prompt token count, completion token count, model, agent role, and tool outcome.
3. Export the captured calls into Tracerator/AIPerf replay format.
4. Compare infrastructure metrics against CoffeeBench business KPIs.

This lets us answer a better enterprise question: not just "how many tokens/sec?", but "does serving behavior affect multi-agent business outcomes such as net income, responsiveness, deals closed, or idle drift?"

## What Not To Use It For

CoffeeBench should not replace SageServe, Mooncake, RAGPulse, or production traces for infrastructure-only load representativeness.

Reasons:

- It is a simulated coffee economy, not a direct enterprise software/SRE/customer-support workload.
- Supply chains are simplified, with abstracted production, financing, logistics, and regulation.
- Runs are expensive and slow enough that large statistical sweeps need planning.
- The benchmark reports high variance across runs, so small model differences are not necessarily meaningful without enough seeds.
- It evaluates agent decision quality as much as serving behavior, so poor outcomes may reflect model policy, environment stochasticity, latency, or all three.

## Recommended Integration Plan

1. Add `TRACERATOR_COFFEEBENCH_DIR` discovery, matching the existing optional source pattern.
2. Inspect released trajectory schema and write `scripts/import_coffeebench_trajectories.py`.
3. Emit:
   - `trace.jsonl` for AIPerf replay;
   - `manifest.json` with run/model/role/tool/KPI statistics;
   - `tool_mix.json` or manifest section for action-category distributions.
4. Add source ids such as:
   - `coffeebench-roaster-gpt`;
   - `coffeebench-roaster-sonnet`;
   - `coffeebench-roaster-haiku-idle-drift`;
   - `coffeebench-all-agents`.
5. Use `aiperf analyze-trace` to compare CoffeeBench-derived traces against SageServe and Mooncake on ISL/OSL, burstiness, and cache reuse.
6. Use these manifests as acceptance criteria for future `aiperf synthesize` agentic workloads.

## Immediate Recommendation

Treat CoffeeBench as a phase-two source:

- Phase one: keep the current SageServe and Mooncake path as the infrastructure replay baseline.
- Phase two: ingest CoffeeBench trajectories to enrich the agentic-enterprise suite with long-horizon multi-agent behavior.
- Phase three: use CoffeeBench as a quality/performance bridge by profiling CoffeeBench runs through the same endpoint stack we replay with AIPerf.

This gives us the best of both worlds: real-ish enterprise serving traces for load fidelity, plus CoffeeBench-derived long-horizon agent behavior for workloads that need negotiation, state growth, async reactivation, and business outcome metrics.
