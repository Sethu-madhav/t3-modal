<img src="assets/icon.svg" width="64" height="64" alt="t3modal">

# t3modal

Cloud machines for [T3 Code](https://t3.codes) running on [Modal](https://modal.com) Sandboxes.

A machine sleeps when no one uses it and wakes when someone does, so it costs money only while it works. Everything runs in your own Modal workspace under your own Modal login—there is no hosted service in between.

> [!NOTE]
> **Work in progress.** Phase 0 platform experiments and the design are complete, and Phase 1 (the Modal controller) is underway. The laptop service, CLI, proxy, and machine daemon are not implemented yet—see [implementation.md](implementation.md#16-work-plan) for the roadmap.

## How it works

t3modal splits the work across three parts:

```text
                  Your Mac                                          Your Modal workspace
┌─────────────────────────────────────────────┐        ┌──────────────────────────────────────────────────┐
│ T3 Code ──ws──> t3modal service (launchd)   │  https │ t3modal-controller (deployed Python app)         │
│                  ├ proxy: 127.0.0.1:<port>  │───────>│  HTTPS API: machines, credentials, budget, ops   │
│                  │ per machine + capture    │        │  Every minute: due wakes, rollovers, cleanup     │
│                  ├ local API ← t3modal CLI  │        │  Volume t3modal-state: machines/<id>.json        │
│                  ├ MCP server               │        │  Secrets: t3modal-admin, -hmac, -github, -claude │
│                  └ notifications, Keychain  │        └──────────────┬───────────────────────────────────┘
└──────────────────┼──────────────────────────┘                       │ creates, snapshots, ends
                   │ https/wss via connect-token URLs                 ▼
                   ▼
         ┌──────────────────────────────────────────────────────────────────┐
         │ Sandbox m-<machineId> (runtime="vm", latest disk/memory state)   │
         │  tini → /opt/t3modal/init.sh                                     │
         │   ├─ t3 serve as user t3 on :3773          ← connect URL A       │
         │   ├─ daemon on :3774 (Node 22)             ← connect URL B       │
         │   └─ dockerd as root                                             │
         └──────────────────────────────────────────────────────────────────┘
```

| Component | Where it runs | Stack | Role |
|---|---|---|---|
| **Laptop service & CLI** (`apps/service`) | Your Mac (`launchd`) | TypeScript on Bun (single `t3modal` binary) | Localhost proxy, capture replay, machine lifecycle, local API, CLI, MCP server, Keychain, macOS notifications |
| **Daemon** (`apps/daemon`) | Inside each sandbox | TypeScript bundled for Node 22 | Idle detection, standby, draining, SQLite backups, heartbeats, rollovers, account sign-in helpers |
| **Controller** (`modal/controller`) | Your Modal workspace | Python (FastAPI + Modal SDK) | Creating, snapshotting, waking, and ending sandboxes; 1-minute timer for due wakes, rollovers, checkpoints, cleanup, and cost accounting |
| **Shared types** (`packages/shared`) | Service & daemon | TypeScript | Controller/daemon contracts and signed-request test vectors |

## Key features (v1 design)

- **Layered sleep & fast wakes:** After 5 idle minutes a machine drops into low-cost **standby** for 1 hour, then takes a **memory sleep** (keeping processes alive for ~1–2 s memory restores) backed by a whole-disk snapshot (`ttl=None`, never expires). Older chains or pending resizes fall back cleanly to a **disk sleep** and **fresh boot**.
- **Finishes work with your laptop closed:** The daemon decides when a machine is idle and asks the controller to sleep it. The controller's 1-minute timer handles scheduled wakes, 2-hour busy checkpoints, and rollovers before Modal's 24-hour sandbox limit.
- **Stable localhost pairing:** T3 Code pairs once with `http://127.0.0.1:<port>`. While a machine sleeps, the proxy answers T3 Code's startup reads from its last **capture** so opening T3 Code never wakes sleeping machines; sending a message holds the call, wakes the machine, and forwards it.
- **VM runtime with Docker:** Every sandbox runs with Modal's `runtime="vm"` (no fallback), so `sudo`, `dockerd`, and cgroup CPU stats work out of the box ([ADR 0004](docs/adr/0004-vm-runtime-for-every-machine.md)).
- **Private by default:** No public tunnels—ports 3773 and 3774 are reached exclusively via Modal connect-token URLs ([ADR 0003](docs/adr/0003-connect-tokens-instead-of-public-tunnels.md)). Each machine authenticates to the controller with a per-machine HMAC-SHA256 secret with timestamp and nonce replay protection. Laptop secrets stay in the macOS Keychain.
- **Agent & GitHub credentials:** v1 targets macOS laptops and **Claude Code** (via `claude setup-token` shared across machines, or per-machine login) plus **GitHub** (fine-grained personal access token by default, or per-machine device login), with git identity synced at boot.
- **CLI & MCP server:** Manage machines from `t3modal` (`setup`, `create`, `list`, `status`, `wake`, `sleep`, `resize`, `delete`, `pair`, `project add`, `accounts`, `budget`, `logs`) or via MCP tools (`create_machine`, `list_machines`, `wake_machine`, `sleep_machine`, `resize_machine`, `delete_machine`, `add_project`).

## Sizing & cost warning

> [!WARNING]
> Modal bills the larger of **requested** and **actual** sandbox usage ($0.00003942 per CPU core-second and $0.00000667 per GiB-second). Running a large machine at 100% load continuously is significantly more expensive than flat-rate sandbox hosts.

To keep idle and typical agent costs low, every machine requests **0.25 cores and 1 GiB** and bursts up to its size limit:

| Size | Burst limit | Full-load rate (approx.) |
|---|---|---|
| `small` | 2 cores, 4 GiB | ~$0.38 / hr |
| `default` | 4 cores, 8 GiB | ~$0.76 / hr |
| `large` | 8 cores, 16 GiB | ~$1.52 / hr |

- **Standby** bills near the small request (~$0.06 / hr).
- **Typical coding agent usage** spends most of each turn waiting on model APIs (~$0.09 / hr at ~0.3 cores / 2 GiB average).
- **Monthly budget:** Defaults to **$20/month** (fitting within Modal Starter's $30 monthly credits). The laptop warns at 80%, and the controller blocks new wakes at 100% unless explicitly overridden with `--force`.

## Repository layout

```text
t3-modal/
├─ implementation.md      # Full design, security model, and phased work plan
├─ GLOSSARY.md            # Project vocabulary
├─ apps/
│  ├─ service/            # Laptop service, proxy, CLI, and MCP server (Bun / TypeScript)
│  └─ daemon/             # Per-machine daemon (TypeScript bundled for Node 22)
├─ packages/
│  └─ shared/             # Shared TypeScript types and signed-request fixtures
├─ modal/
│  ├─ controller/         # Deployed Modal Python app (API, timer, tests)
│  └─ sandbox/            # Sandbox boot/supervision scripts (init.sh)
├─ docs/
│  ├─ adr/                # Architecture decision records
│  ├─ phase-0-findings.md # Phase 0 benchmark measurements on Modal
│  └─ phase-0-research.md # Modal and T3 Code platform research
└─ experiments/phase-0/   # Reproducible Phase 0 experiment scripts
```

## Development

### Prerequisites

- [Bun](https://bun.sh)
- [Node.js 22](https://nodejs.org) (see `.node-version`, used to test `apps/daemon`)
- Python 3.12–3.15 and [uv](https://docs.astral.sh/uv/) (used for `modal/controller`)

### Commands

```bash
# Install workspace dependencies
bun install

# Run all checks: typecheck, Bun tests, Node daemon tests, pytest controller tests, and lockfile check
bun run test

# Run individual suites
bun run typecheck
bun run test:bun
bun run test:daemon
bun run test:controller

# Run opt-in integration tests against your Modal workspace (incurs small Modal usage)
bun run test:it

# Regenerate modal/controller/requirements.lock after changing controller dependencies
bun run lock:controller
```

## Documentation

- [implementation.md](implementation.md) — complete system design, lifecycle, security model, and roadmap
- [GLOSSARY.md](GLOSSARY.md) — canonical terms used across code and docs
- [Architecture Decision Records](docs/adr/)
  - [0001: Build t3modal from scratch instead of forking t3boat](docs/adr/0001-build-instead-of-forking-t3boat.md)
  - [0002: TypeScript everywhere except the controller, which is Python](docs/adr/0002-typescript-with-a-python-controller.md)
  - [0003: Reach machines through connect tokens, not public tunnels](docs/adr/0003-connect-tokens-instead-of-public-tunnels.md)
  - [0004: Run every machine on Modal's VM runtime](docs/adr/0004-vm-runtime-for-every-machine.md)
- [Phase 0 Findings](docs/phase-0-findings.md) & [Phase 0 Research](docs/phase-0-research.md)
- [Phase 0 Experiments](experiments/phase-0/README.md)

## Credits

Inspired by [t3boat](https://github.com/paukraft/t3boat) (MIT-licensed), which brings sleeping cloud machines to T3 Code on [Boat](https://boat.dev). t3modal is written from scratch for Modal Sandboxes ([ADR 0001](docs/adr/0001-build-instead-of-forking-t3boat.md)) and uses t3boat as a reference for T3 Code's protocol and idle-sleep design.
