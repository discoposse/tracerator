# Why realistic traces matter

People hire a serving benchmark to answer a capacity question. The tape you replay is the experiment. If the tape is independent requests with Poisson arrivals and random prefixes, you measured a different system than production.

Production LLM traffic is bursty. Dozens of requests can share a millisecond. Prompt and generation lengths are heavy-tailed. A few prefixes (system prompts, agent scaffolds, popular RAG contexts) are extremely hot. Sessions grow a long prefix and then branch.

Those properties dominate KV cache hit ratio, prefill versus decode cost, queueing, and any offload to host or remote storage. A generator that ignores them will report a cache that is too cold, a GPU that is too free, and a storage tier that is never touched.

Tracerator starts from traces that already have those properties, then scales, reshapes ISL, and biases reuse while keeping ordered `hash_ids`. The result is still a simulation. It is a better simulation than sampling lengths from a normal distribution and hoping the cache looks busy.

The bundled Mooncake Conversation, Tool & Agent, and Synthetic files are the production-derived starting points. Read [Mooncake/WORKLOAD_NARRATIVE.md](../../Mooncake/WORKLOAD_NARRATIVE.md) for the statistics behind them.
