# Phase 0 experiments

PROTOTYPE, throwaway. This code lives on the `phase-0` branch only and never merges to main. Only the findings in `docs/phase-0-findings.md` merge.

Each script answers questions from `implementation.md` §18 against the real Modal workspace. Each one creates sandboxes in the app `t3modal-phase0`, tags them `t3modal-phase0`, and ends them before it exits. Images get a one-hour TTL.

| Script | Questions | Rough cost |
|---|---|---|
| `exp01_memory_restore.py` | 2, 5, 8 | $0.10 |
| `exp02_connect_token.py` | 7 in part, 16 | $0.03 |
| `exp03_large_snapshot.py` | 1, 2 | $0.08 |
| `exp04_snapshot_chain.py` | 3 | $0.03 |
| `exp05_cpu_burst.py` | 6, 12 | $0.02 |
| `exp06_runtime_checks.py` | 9, 10 | $0.02 |

Run one with `uv run experiments/phase-0/<script>.py`. It needs a Modal token in `~/.modal.toml`. Results go to `results/` as JSON.
