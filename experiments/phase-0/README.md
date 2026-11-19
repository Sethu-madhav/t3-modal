# Phase 0 experiments

PROTOTYPE, throwaway. These scripts merge to main as the record behind `docs/phase-0-findings.md`. No product code imports them.

Each script answers questions from `implementation.md` §18 against the real Modal workspace. Each one creates sandboxes in the app `t3modal-phase0`, tags them `t3modal-phase0`, and ends them before it exits. Images get a TTL of one or two hours.

| Script | Questions | Rough cost |
|---|---|---|
| `exp01_memory_restore.py` | 2, 5, 8 | $0.10 |
| `exp02_connect_token.py` | 7 in part, 16 | $0.03 |
| `exp03_large_snapshot.py` | 1, 2 | $0.08 |
| `exp04_snapshot_chain.py` | 3 | $0.03 |
| `exp05_cpu_burst.py` | 6, 12 | $0.02 |
| `exp06_runtime_checks.py` | 9, 10 | $0.02 |
| `exp07_signins.py` | 13, 14. Needs a `claude setup-token` token and the user in T3 Code | $0.06 an hour while running |
| `exp08_connect_tokens.py` | 7, 18, 19. Runs about 90 minutes | $0.10 |

Run one with `uv run experiments/phase-0/<script>.py`. It needs a Modal token in `~/.modal.toml`. Results go to `results/` as JSON.
