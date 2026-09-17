# Environment variables

Compose reads a repo-root `.env` file. Copy `.env.example`. Do not commit `.env`.

| Variable | Role |
|----------|------|
| `HF_TOKEN` | Hugging Face token for gated tokenizers and models inside the container |
| `HUGGING_FACE_HUB_TOKEN` | Alias; defaults to `HF_TOKEN` |
| `TRACERATOR_HF_CACHE_DIR` | Host Hugging Face cache mounted at `/root/.cache/huggingface` |
| `TRACERATOR_INFERENCE_HOST` | Hostname of a host inference server from inside Docker. Default `host.docker.internal` |
| `TRACERATOR_INFERENCE_BACKEND` | `omlx`, `vllm`, `sglang`, `ollama`, or `openai`. Default `omlx` |
| `TRACERATOR_INFERENCE_API_KEY` | Dedicated key for local inference |
| `OMLX_API_KEY`, `VLLM_API_KEY`, `SGLANG_API_KEY`, `LLM_API_KEY` | Per-backend keys |
| `OPENAI_API_KEY` | Cloud OpenAI key for other tools. Not forwarded to local MLX/vLLM |
| `TRACERATOR_DB` | SQLite path. Default `artifacts/tracerator.db` |
| `TRACERATOR_RUNS_ROOT` | Run artifact directory. Default `artifacts/runs` |
| `TRACERATOR_RAGPULSE_DIR` | Host RAGPulse checkout. Default `./external/RAGPulse` |
| `TRACERATOR_CC_WEKA_DIR` | Host CC Weka checkout. Default `./external/cc-traces-weka-with-subagents-051826` |
| `TRACERATOR_CODEX_SWEBENCHPRO_DIR` | Host Codex SWE-Bench Pro traces. Default `./external/codex_swebenchpro_traces` |
| `TRACERATOR_AIPERF_TOOLKIT_DIR` | Host aiperf-toolkit checkout. Default `./external/aiperf-toolkit` |
| `TRACERATOR_SAGESERVE_DIR` | Host SageServe checkout. Default `./external/SageServe` |
| `TRACERATOR_PUBLISH_PORT` | Host port for `docker-compose.prod.yml`. Default `8008` |
| `DEPLOY_HOST`, `DEPLOY_DIR`, `DEPLOY_URL` | Required for `scripts/deploy-prod.sh` |
| `DEPLOY_USER`, `DEPLOY_BARE` | Optional SSH user and bare-repo path |
