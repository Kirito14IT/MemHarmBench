# Running and recovering an evaluation

[Home](../README.md) · [中文首页](../README.zh-CN.md) · [Protocols](protocols.md)

## Prerequisites

Install Python 3.11+, the package (`python -m pip install -e .`), and Docker Desktop with Linux containers. The public background control commands target Windows. Offline input inspection and validation do not require Docker or credentials.

Build the images from the repository root:

```powershell
docker build -f docker/Dockerfile.claude-code -t memharmbench/claude-code:2.1.228 .
docker build -f docker/Dockerfile.openclaw -t memharmbench/openclaw:2026.7.1 .
docker build -f docker/Dockerfile.gateway -t memharmbench/gateway:1.101.0 .
```

Build the selected agent's image; Claude Code also uses the gateway image. The Dockerfiles pin agent/runtime versions; the OpenClaw recipe pins its base image digest. Actual container/image identities are captured when creating a campaign.

## Configuration

Copy an example into a local file; keep it in `configs/` so relative output paths retain their meaning:

```powershell
Copy-Item configs/main.claude-gpt5.json configs/my-run.local.json
```

The four main examples are `main.claude-gpt5.json`, `main.claude-gemini.json`, `main.openclaw-gpt5.json`, and `main.openclaw-gemini.json`. Ablation templates are `semantic_alignment.example.json` and `exposure_distance.example.json`.

| Field | What to set |
| --- | --- |
| `experiment` | `main`, `semantic_alignment`, or `exposure_distance` |
| `agent` | `claude_code` or `openclaw`; distance uses Claude Code |
| `model` | Exact `gpt-5` or `gemini-2.5-flash`; alignment uses GPT-5 |
| `judge_model` | The fixed `gpt-5` Judge |
| `replicates` | Three, as specified by the released protocols |
| `agent_endpoint`, `judge_endpoint` | Your compatible provider's HTTPS API base URLs |
| `agent_key_env`, `judge_key_env` | Environment-variable names for the respective credentials |
| `agent_http_host`, `judge_http_host` | Optional provider-specific HTTP Host overrides; normally `null` |
| `agent_image`, `gateway_image` | The Docker image names built above, or corresponding pinned local images |
| `run_dir` | A dedicated output directory, resolved relative to the configuration file |

Example endpoint values are placeholders. Use your own compatible relay/provider; no official-provider account or built-in relay account is assumed. The Claude Code gateway adapts its agent protocol while preserving the configured model and native agent tools. A mismatched returned model is an error, not permission to select another model.

The default credential variables are `MHB_AGENT_API_KEY` and `MHB_JUDGE_API_KEY`. Set them locally in the shell or your environment-management tool before starting a worker. Do not place real values in committed configuration files or bug reports. A resumed worker needs the same variables to be available in its launching environment.

Use a distinct `run_dir` for each agent/model/configuration. Do not change frozen inputs or protocol settings inside an existing campaign; create a new configuration and output directory instead.

## Control commands

```powershell
# Validate local inputs, configuration, and Docker prerequisites without a model call.
python scripts/experiment.py --config configs/my-run.local.json check

# Launch one background queue for this configuration.
python scripts/experiment.py --config configs/my-run.local.json start

# Read status once, or refresh it continuously.
python scripts/experiment.py --config configs/my-run.local.json status
python scripts/experiment.py --config configs/my-run.local.json status --watch

# Stop at a safe checkpoint after the active stage finishes.
python scripts/experiment.py --config configs/my-run.local.json pause

# Continue from persisted checkpoints.
python scripts/experiment.py --config configs/my-run.local.json resume

# After fixing a recoverable technical block, authorize a bounded retry.
python scripts/experiment.py --config configs/my-run.local.json retry
```

**Closing the terminal or pressing Ctrl+C in the watcher does not stop the background worker.** To stop execution, issue `pause`, inspect `status`, and wait for `worker_alive=False`. Only then stop Docker or restart the machine.

After a terminal disconnect, check status first; the worker may still be running. `resume` preserves completed work and avoids launching a duplicate worker. After a machine restart, start Docker, restore the credential environment, and use `resume` with the same configuration.

Ordinary `resume` uses existing checkpoints. `retry` explicitly releases eligible recoverable blocks after their cause has been addressed; it does not reset consumed attempt budgets, erase failed evidence, or resample valid judgments. Stop the worker before using `retry`. Ambiguous interrupted calls still require evidence reconciliation and are not blindly replayed.

## Ablation queues

For alignment, create one local configuration for Claude Code and one for OpenClaw, both with GPT-5. Set the corresponding agent image and a distinct `run_dir` for each. Each queue covers 36 chains; together they cover the 72-chain design.

For distance, create two Claude Code configurations: one with GPT-5 and one with Gemini-2.5-Flash. Keep the same Claude Code image and fixed GPT-5 Judge. Each queue covers 18 chains, with sequential normal sessions within a chain. Starting the two configurations gives the declared total concurrency of two.

## Outputs and evidence

All newly generated material belongs to the configured local output directory: campaign identity, checkpoints, stage workspaces, native memory captures, tool evidence, Judge outputs, status, and logs. Run directories within the checkout must be under `runs/`, which is ignored by Git; an external dedicated directory is also supported.

Keep partial evidence when diagnosing a failure. A stalled heartbeat, a provider rejection, a model-identity mismatch, and an unresolved semantic label are different conditions. Do not fix them by deleting checkpoints or changing labels. Report technical failures separately from behavioral outcomes when analyzing your own runs.

The published repository contains no historical trajectories or judged results. Reproduction begins from the released inputs and generates fresh evidence under your selected provider configuration.
