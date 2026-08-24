# Example 05: Dual-Model Split (Cloud Planner + Local GPU Worker)

**Capability**: Hybrid routing — powerful cloud planner, free local worker
**Difficulty**: Intermediate
**Prerequisites**: A cloud provider key (e.g. Copilot) AND a local model server (llama.cpp `llama-server` or LM Studio) running with tool-calling support (`--jinja`)

## Objective

Run missions where planning happens on a strong cloud model while execution turns run on your local GPU — cutting cost and latency.

## Steps

1. Start a local server (example with llama.cpp):

```bash
llama-server -m ./models/LFM2.5-1.2B-Q4_K_M.gguf --jinja --port 8080
```

2. Configure the split via environment variables:

```bash
export AJA_PLANNER_MODEL=copilot:gpt-4o-mini
export AJA_WORKER_MODEL=llama_cpp:LFM2.5-1.2B
```

3. Set the operating mode to hybrid (in `aja.json` or settings):

```json
{ "operating_mode": "hybrid" }
```

4. Run a mission:

```bash
aja run "Summarize the README.md of this project in 5 bullet points"
```

## Expected Output

The mission completes with planning logs referencing the cloud model and worker execution logs referencing the local model. `aja doctor` shows both roles resolved, no mode warnings.

## How It Works

`resolve_provider_model()` routes per-role: hybrid mode honors explicit per-role selections so planner=cloud and worker=local coexist. Spawned workers resolve `AJA_WORKER_MODEL` to build their own gateway. Models that cannot satisfy the structured output contract are cached as contract-incapable and served simpler prompts next turn.

## Troubleshooting

- **Worker falls back to cloud**: local server not detected; verify `llama-server` is up and check `aja doctor`.
- **Garbled JSON from worker**: ensure `--jinja` is enabled (native tool-calling); AJA will retry with a simplified schema automatically.
- **Doctor warns about local model**: you set a local role model but stayed in `online` mode — switch to `hybrid`.
