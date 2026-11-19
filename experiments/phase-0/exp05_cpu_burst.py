# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.4"]
# ///
"""PROTOTYPE, throwaway. Phase 0 experiment 5: using more CPU and memory than requested.

Answers, from implementation.md §18 and §13:
- Q6: does a sandbox created with cpu=(0.25, 4) and memory=(1024, 8192) get
      close to 4 cores and 6 GiB when it needs them, on both runtimes?
- Q12: what does Modal bill for the burst and for the idle time around it?

Each sandbox idles 60 s, runs 4 busy processes for 60 s, allocates 6 GiB,
then idles 60 s more. Run with "stats" a few minutes later to read billed usage.
Cost is about $0.05.
Run: uv run experiments/phase-0/exp05_cpu_burst.py
     uv run experiments/phase-0/exp05_cpu_burst.py stats
"""

import json
import sys
import time
from pathlib import Path

import modal

from common import RESULTS, TAG, app, image, log, save, sh

BURN = r"""
python3 - <<'EOF'
import multiprocessing as mp, os, time
def burn(q):
    t0 = time.process_time(); end = time.time() + 60
    while time.time() < end: pass
    q.put(time.process_time() - t0)
q = mp.Queue(); ps = [mp.Process(target=burn, args=(q,)) for _ in range(4)]
w = time.time(); [p.start() for p in ps]; [p.join() for p in ps]
cpu = sum(q.get() for _ in ps); wall = time.time() - w
print(f"{cpu:.1f} {wall:.1f} {cpu / wall:.2f}")
EOF
"""

ALLOC = r"""
python3 - <<'EOF'
b = bytearray(6 * 1024**3)
for i in range(0, len(b), 4096): b[i] = 1
print("allocated 6 GiB")
EOF
echo "exit $?"
"""


def run(runtime):
    kw = {"runtime": runtime} if runtime else {}
    sb = modal.Sandbox.create("sleep", "infinity", app=app, image=image, cpu=(0.25, 4), memory=(1024, 8192),
                              timeout=900, tags={TAG: "exp05"}, **kw)
    rec = {"runtime": runtime or "default", "sandbox_id": sb.object_id}
    try:
        rec["nproc"] = sh(sb, "nproc")
        rec["cgroup_cpu_max"] = sh(sb, "cat /sys/fs/cgroup/cpu.max 2>/dev/null || echo none")
        rec["meminfo_total"] = sh(sb, "grep MemTotal /proc/meminfo")
        log(f"{rec['runtime']}: idle 60 s")
        time.sleep(60)
        cpu, wall, cores = sh(sb, BURN).split()
        rec["burst"] = {"cpu_s": float(cpu), "wall_s": float(wall), "cores_used": float(cores)}
        log(f"{rec['runtime']}: burst used {cores} cores")
        rec["alloc_6gib"] = sh(sb, ALLOC)
        log(f"{rec['runtime']}: {rec['alloc_6gib']}")
        time.sleep(60)
    except Exception as e:
        rec["error"] = repr(e)
    finally:
        sb.terminate()
    return rec


def stats():
    latest = sorted(RESULTS.glob("exp05-*.json"))[-1]
    data = json.loads(latest.read_text())
    out = {}
    for rec in data["runs"]:
        s = modal.Sandbox.from_id(rec["sandbox_id"])._experimental_stats()
        out[rec["runtime"]] = {
            "duration_s": round(s.duration.total_seconds(), 1),
            "billed_cpu_core_seconds": s.billed_cpu_core_seconds,
            "billed_memory_gib_seconds": s.billed_memory_gib_seconds,
            "cost_usd": str(s.cost),
            "cost_by_resource": {k: str(v) for k, v in s.cost_by_resource.items()},
            "percentiles": {k: {"p50": v.p50, "p90": v.p90, "p99": v.p99} for k, v in s.percentile_stats.items()}
            if s.percentile_stats else {},
        }
        log(f"{rec['runtime']}: {out[rec['runtime']]}")
    data["stats"] = out
    Path(latest).write_text(json.dumps(data, indent=2, default=str))
    log(f"stats added to {latest}")


if __name__ == "__main__":
    if sys.argv[1:] == ["stats"]:
        stats()
    else:
        result = {"experiment": "exp05_cpu_burst", "runs": []}
        try:
            for rt in ("vm", None):
                result["runs"].append(run(rt))
        except Exception as e:
            result["error"] = repr(e)
        finally:
            save("exp05", result)
