# t3modal: implementation plan

t3modal runs cloud machines for T3 Code on [Modal](https://modal.com) Sandboxes. A machine sleeps when no one uses it and wakes when someone does, so it costs money only while it works. The idea comes from [t3boat](https://github.com/paukraft/t3boat), which does the same on [Boat](https://boat.dev). t3modal is our own code. t3boat is a reference only.

How to read this document:
- [GLOSSARY.md](GLOSSARY.md) defines the words used here. "Agent" always means a coding agent such as Claude Code. t3modal's own process on each machine is the "daemon".
- Architecture decisions are recorded in [docs/adr/](docs/adr/).
- Sections 1 to 3 cover scope, t3boat and how Modal differs from Boat.
- Sections 4 to 14 are the design.
- Section 15 is the security model.
- Section 16 orders the work into phases.
- Section 18 lists what Phase 0 answered and what is still open.

Revised 2026-11-18 after a design review. The main changes from the earlier draft:
- We build our own code instead of forking t3boat (ADR 0001).
- The laptop side is a TypeScript service, and the controller stays Python (ADR 0002).
- The sign-in relay is gone.
- There is a monthly budget.
- The tray, T3 Connect and the early wake move to later.

Revised again on 2026-11-18 after Phase 0. The measurements are in [docs/phase-0-findings.md](docs/phase-0-findings.md), and what docs and source code say is in [docs/phase-0-research.md](docs/phase-0-research.md). The main changes:
- Every machine runs on `runtime="vm"`, so Docker works.
- Connect-token URLs replace public tunnels.
- Sizing mode B, with small requests and limits by size.
- Memory sleeps ship on.
- Costs come from Modal's per-tag stats.
- v1 supports one provider, Claude Code. Antigravity moves to later.

Revised on 2026-11-19 after experiment 8 and the grilling round. The main changes:
- Every machine runs on the VM runtime with no fallback (ADR 0004), and `dockerd` starts at boot.
- Connect tokens are tested on the VM runtime with two ports (ADR 0003). The controller keeps them and refreshes them after a 401.
- The proxy tells an ended sandbox, an expired token and a network error apart, and it sends no keepalive pings.
- The budget counts gross sandbox cost, with a 7-day average for hours the stats don't cover yet.
- The git identity comes from the GitHub Secret at every boot.

---

## 1. Goals and scope

t3modal is a personal tool first. It runs in the user's own Modal workspace with the user's own Modal login. It has no backend of its own.

**v1 goals**
- Create, resize and delete machines from the `t3modal` command or the MCP server.
- T3 Code pairs with a stable `http://localhost:<port>` address per machine.
- A message to a sleeping machine wakes it.
- A machine finishes its work and then sleeps, even with the laptop closed.
- Scheduled work in T3 wakes a sleeping machine even when the laptop is off.
- Add projects and their `.env` files.
- GitHub access for machines.
- Sign machines in to Claude Code.
- Docker inside machines.
- Show costs and enforce a monthly budget.
- macOS notifications for rollovers, failures and budget warnings.

**Later, not in v1**
- A Tauri tray app on the same local API (§8.2).
- T3 Connect, for reaching machines from a phone.
- The early wake: waking a machine when one of its chats is open and focused in T3 Code.
- Codex and OpenCode. T3 already has its own ChatGPT sign-in, which may work on machines as it is.
- Antigravity. T3's Google sign-in failed on a machine with no screen in Phase 0. A Gemini API key was not tested (§11.2).
- Windows and Linux laptops.
- A marketing website.

**Non-goals**
- GPU machines. Modal supports GPU sandboxes only on its default runtime, and every machine runs on the VM runtime (ADR 0004).
- Supporting several cloud providers at runtime.

---

## 2. What we take from t3boat

t3boat is MIT-licensed. We read its code and docs and write our own (ADR 0001). The README credits it. Any piece we copy keeps its notice.

What t3boat teaches us:

| t3boat part | What we learn from it | What we do differently |
|---|---|---|
| `apps/desktop/docs/t3-protocol.md` | How T3 Code talks to a T3 server over WebSocket: pairing, auth, the calls it makes, `directEndpoints` | We reuse T3's own TypeScript types instead of rewriting them (§10) |
| `proxy/*`, about 3,000 lines of Rust | A localhost proxy per machine can serve T3 Code while the machine sleeps | v1 captures only the reads T3 Code makes at startup (§10) |
| `machine/watchdog.mjs` | Idle detection: running agents, actions by a person in T3's SQLite, CPU load, keep-awake, `nextDueAt`, drain with a manifest and acknowledgement | The daemon keeps this logic but sleeps through the controller (§7.3) |
| `machine/signin.rs` | Agent sign-in by running the login on the machine | Modal has no port forwarding. We use `claude setup-token` and code paste (§11) |
| `machine/provision.rs`, `provision.sh` | Pinning T3's version to the laptop's T3 Code, minting T3 tokens, `linkedEnvironmentsEnabled` | No systemd on Modal (§7.2) |
| `machine/mod.rs` | Power states, wake and sleep races, resize policy | Boat's stop and resume, disk expiry and keep-alive wakes don't apply |

---

## 3. Boat compared with Modal

### 3.1 Concept mapping

| Boat | Modal | Consequence for t3modal |
|---|---|---|
| A sandbox with a persistent disk | A gVisor container by default, or a VM with `runtime="vm"`. Either loses its disk when it ends | Every machine runs on `runtime="vm"` (§7.1). Snapshots are the only persistence (§4.1) |
| `boat stop` and `boat resume` keep the same sandbox | Only `terminate()` | A sleep saves a snapshot and ends the sandbox. A wake starts a new sandbox. The sandbox id changes at every wake |
| `boat snapshot self` as a backup | `sb.snapshot_filesystem(ttl=None)` returns an image kept forever. The default TTL is 30 days | Snapshots never expire, so no keep-alive wakes |
| Disk deleted after 14 days stopped | None | No restore path |
| `boat host <port> --public`, one URL per sandbox | Public tunnels with `encrypted_ports`, or `sb.create_connect_token(port=...)`, which returns a URL that Modal guards with a token | Connect-token URLs, new at every wake (§10.2) |
| `boat exec`, `boat scp` | `sb.exec`, `sb.filesystem.*`, in the SDKs only | The daemon exposes a fixed list of operations over HTTPS (§7.3) |
| `boat forward` | None | Sign-in without port forwarding (§11) |
| No lifetime limit | At most 24 hours | Rollover before the limit (§9.6) |
| systemd services | No systemd | A startup script supervises T3, the daemon and `dockerd` (§7.2) |
| Docker | Works only on `runtime="vm"` | Supported, because every machine uses the VM runtime |
| Sizes small, default, large | Any `cpu` and `memory` at create | t3modal defines sizes (§13) |
| `boat usage` in dollars | `app.sandboxes.stats(group_by_tags=...)`, cost per tag over at most 7 days | Costs from per-tag stats, summed per machine every hour (§6.4) |
| Boat signs sandboxes in to GitHub | Nothing | §12 |
| `boat onboard` | `modal token new` writes `~/.modal.toml` | `t3modal setup` (§14) |
| Scheduled wakes only while the laptop app runs | Scheduled functions | The controller wakes machines on schedule (§6.4) |

### 3.2 Drawbacks of Modal, and how the plan handles each

The Boat side comes only from t3boat's code and docs, not from Boat's own documentation.

| # | Drawback | Modal feature that helps | What t3modal builds | What remains |
|---|---|---|---|---|
| 1 | An awake machine costs about 13 times Boat's hourly price when it uses its full size limit | Billing is the larger of requested and used resources | Small requests, standby, fast sleep, a budget (§4.4, §13) | Modal sets the rates |
| 2 | No pause and resume | Disk snapshots, memory snapshots in alpha | Layered sleep (§4.4) | A slower fresh boot about once a week |
| 3 | Sandboxes end after 24 hours | Snapshots make restarts possible | Rollover (§9.6) | An occasional interrupted long turn |
| 4 | The snapshot is the only copy, so a crash loses work since the last sleep | Snapshots without ending the sandbox | Checkpoints while busy (§9.7) | A shorter loss window, not zero |
| 5 | No systemd. Docker and `sudo` fail on the default runtime | `runtime="vm"`, where both work | Startup script (§7.2), VM runtime for every machine | Solved |
| 6 | A sandbox can't snapshot or end itself | Deployed functions and web endpoints | The controller (§6) | Solved |
| 7 | No GitHub login | Modal Secrets | §12 | Solved |
| 8 | No port forwarding for sign-in | Modal Secrets | §11 | Solved |
| 9 | New URLs at every wake | Connect tokens guard each URL | The proxy hides them (§10) | Solved |
| 10 | No per-sandbox cost report on Starter | `app.sandboxes.stats()` per tag | The controller sums it per machine every hour (§6.4) | Ignores credits. Stats lag a few minutes |
| 11 | More setup | None | `t3modal setup` (§14) | One more component to maintain |

t3modal fixes 6 of these fully (5 to 10), reduces 4 (1, 2, 4, 11) and works around 1 (3). Three limits belong to Modal's platform: the rates, the lack of a true pause and the 24-hour limit.

### 3.3 Benefits of Modal

1. Machines sleep, and wake for scheduled work, with the laptop closed.
2. Snapshots kept with `ttl=None` never expire.
3. Any CPU and memory size. GPUs need the default runtime, so t3modal doesn't use them (ADR 0004).
4. No daily limit on starts.
5. A Python SDK out of beta.
6. $30 of credits a month on the Starter plan. Starter also limits a workspace to 5 deployed crons and 1 day of logs.
7. Images with T3 and the agents already installed, so creating a machine skips the install.
8. Modal allows one running sandbox per name, so two wakes can't start two copies of a machine.
9. Modal Secrets attach credentials at each wake, so one sign-in can cover every machine.
10. Memory snapshots make a pause-and-resume style wake possible. Phase 0 measured 0.9 to 1.7 s from restore to T3 answering.

---

## 4. Main design decisions

### 4.1 Persistence through snapshots of the whole disk

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| `snapshot_filesystem()` of the whole disk at every sleep. The next wake starts from that image | Keeps everything: system packages, toolchains, agent logins, git worktrees, Docker images, T3's SQLite database | One image per sleep. Snapshot time grows with the number of changed files. After a fresh boot, Modal loads file contents lazily, at about 44 MB/s on first read | **Chosen**. A 50-image chain showed no slowdown in Phase 0 |
| `snapshot_directory("/home/t3")` on a fresh base image | Clean upgrades. Smaller snapshots | Anything installed outside `/home` is lost at every sleep | Rejected. Phase 0 found no slowdown in a 50-image chain |
| A Modal Volume at `/home/t3` | No snapshot step | A network filesystem. Slow for `node_modules` and git, risky for SQLite | Rejected for machines. Used for controller state |
| Memory snapshots | Processes keep running. Wake takes 0.9 to 1.7 s | Alpha. Expire after 7 days. Modal recommends `runtime="vm"` | Memory sleeps on top of disk snapshots (§4.4). Never the only copy |

Every snapshot call passes an explicit `timeout` of 600 seconds. The default of 55 seconds is too short. Phase 0 measured 15 s for 20 GB of new data and 199 s for 200,000 new files of 4 KB, so file count sets the time. A sleep after a large `npm install` can take minutes.

A disk snapshot must hold a consistent T3 database:
- **Disk sleep.** Stop T3, run `PRAGMA wal_checkpoint(TRUNCATE)` on its database, run `sync`, then snapshot.
- **Memory sleep and checkpoints.** Keep T3 running. Copy the database with SQLite's online backup, which stays consistent while T3 writes. A fresh boot from that image restores the copy before T3 starts (§4.4).

The controller keeps each machine's two newest images and deletes older ones with `modal.experimental.image_delete`. If a wake fails from the newest image, it tries the older one and the laptop shows a notification.

### 4.2 A controller in the user's Modal workspace

Nothing inside a sandbox can snapshot or end that sandbox, and machines must sleep and wake with the laptop closed. So a small Python app, `t3modal-controller`, runs in the user's workspace. Only Modal's Python SDK can deploy an app with web endpoints and a schedule (ADR 0002).

The controller has three parts:
- An HTTPS API that the laptop service and each daemon call.
- A function that runs every minute for scheduled wakes, rollovers, budget checks and cleanup.
- State in a Modal Volume named `t3modal-state`, one JSON file per machine. It doesn't use `modal.Dict`, because Dict entries expire after 7 days unused.

The controller creates every sandbox inside its own deployed app. That allows named sandboxes, `m-<machineId>`, and Modal allows one running sandbox per name.

### 4.3 Our own code, in TypeScript

| Component | Where it runs | Language | Notes |
|---|---|---|---|
| Laptop service | The user's Mac, started at login by launchd | TypeScript on Bun, compiled to one `t3modal` binary | Proxy, local API, CLI, MCP server, notifications (§8) |
| Daemon | Each machine | TypeScript bundled into one JavaScript file, run by Node 22 | Node is already on the machine for T3 (§7.3) |
| Controller | The user's Modal workspace | Python | §6 |
| Shared types | Laptop service and daemon | TypeScript | Our own API types, plus T3's types from its `contracts` package (§10) |

The laptop service talks to Modal only through the controller. It needs Modal's Python CLI only during setup, to sign in and deploy (§14).

The service's machine code depends on a `Cloud` interface with the methods `create`, `wake`, `sleep`, `checkpoint`, `terminate`, `status`, `list` and `usage`. The real version calls the controller. Tests use a fake one.

### 4.4 Getting close to pause and resume

Boat's pause keeps the disk and the running processes, and it wakes fast. Disk snapshots alone keep only the disk. t3modal combines three mechanisms. Phase 0 confirmed the Modal behaviour that mechanisms 1 and 2 depend on. Mechanism 3 is our own code.

Memory sleeps matter more than first planned. A fresh boot reads file contents at about 44 MB/s the first time, so a large repo's first build after a disk sleep is slow. Disk sleeps should stay rare.

| State | When | What happens | Wake |
|---|---|---|---|
| Awake | In use | Normal | Not needed |
| Standby, mechanism 2 | Idle for 5 minutes | The sandbox stays up. Billing drops close to its small request | Instant |
| Memory sleep, mechanism 1 | In standby for 1 hour. The memory chain is under 6 days old and no resize is pending | Disk snapshot first, then a memory snapshot, which ends the sandbox | Memory restore. Processes continue |
| Disk sleep | The chain is 6 days old or more, a resize is pending, a memory snapshot failed, or memory sleeps are off | Quiesce, disk snapshot, end the sandbox | Fresh boot. Recorded commands rerun, mechanism 3 |

#### Mechanism 1: memory sleep

The API is in alpha, so the names may change:
- Create every sandbox with `runtime="vm"` and `_experimental_enable_snapshot=True`.
- Sleep with `sb._experimental_snapshot()`, which saves the sandbox and ends it.
- Wake with `modal.Sandbox._experimental_from_snapshot(snapshot)`, or look the snapshot up with `modal.SandboxSnapshot.from_id(id)`.

Limits from Modal's docs:
- **A snapshot expires 7 days after creation.** A snapshot taken from a restored sandbox keeps the original expiry, so a whole chain expires 7 days after its first snapshot. The controller stores `memoryChainStartedAt`. Once a chain is 6 days old, the next sleep is a disk sleep.
- **Open TCP connections close.** The proxy reconnects to the new connect URL. A sleep happens only when no agent is working, so no call to a model's API is cut off.
- **A restore uses the same machine type.** A pending resize forces a disk sleep.
- **No `exec` may run at snapshot time.** t3modal doesn't use `exec`.

What Phase 0 measured on a VM sandbox:
- T3 and other processes kept their PIDs, and the boot ID stayed the same. The VM's clock paused while it slept.
- T3 answered 0.9 to 1.7 s after a restore, in two runs, with the same environment id.
- A restore gets a new full timeout, counted from the restore.
- `snapshot_filesystem` works on a running VM sandbox, in 3.8 s for a small disk.

Memory sleep order:
1. The daemon drains, as t3boat's watchdog does. It publishes the manifest. The laptop service refreshes the capture and acknowledges. If the laptop is offline, the daemon continues without the acknowledgement, and the old capture is marked stale (§10.4).
2. The daemon copies T3's database with `sqlite3 statev2.sqlite ".backup ~/.t3modal/statev2.backup.sqlite"`, writes a marker naming the copy, and runs `sync`.
3. The controller runs `snapshot_filesystem(ttl=None)` while the sandbox still runs.
4. The controller writes the new image id to the machine's state and commits the Volume.
5. The controller runs `_experimental_snapshot()`, which ends the sandbox, then stores `memorySnapshotId`.

If step 5 fails, the controller ends the sandbox anyway, because step 3 already saved the disk. The next wake is a fresh boot.

The memory snapshot calls are alpha. The controller pins the Modal SDK version, and only a t3modal release upgrades it. Any error from a memory snapshot or restore falls back to a disk sleep or a fresh boot. After 3 such failures in a row, the laptop shows a notification.

Wake order:
1. If `memorySnapshotId` exists, its chain is under 6 days old and the size is unchanged, do a memory restore.
2. Otherwise, or if the restore fails, do a fresh boot from `images[0]`. If that image carries the backup marker, `init.sh` replaces `statev2.sqlite` with the backup before T3 starts.

After a memory restore, the daemon sees its clock jump. t3boat's watchdog treats a tick more than 30 seconds late as a resume and ends any keep-awake. The daemon does the same.

#### Mechanism 2: standby

Standby is cheap only if a sandbox requests little CPU and memory and can use more while an agent works. Each size then requests 0.25 cores and 1 GiB, with limits of 2, 4 or 8 cores and 4, 8 or 16 GiB.

Phase 0 confirmed this. A sandbox created with `cpu=(0.25, 4)` and `memory=(1024, 8192)` used 4.00 cores and allocated 6 GiB on the VM runtime. Modal billed the larger of request and use, sampled over time, and idle time billed at the 0.25-core request.

Cost of standby, if an idle T3 uses about 0.5 GiB:
- CPU at the 0.25-core request: about $0.035 an hour.
- Memory at the 1 GiB request: about $0.024 an hour.
- Total: about $0.06 an hour, so about $0.06 per sleep.

The standby period is a setting, 1 hour by default. Setting it to 0 turns standby off.

#### Mechanism 3: rerun recorded commands after a fresh boot

Before each sleep, the daemon records the commands running in T3's terminals. It gets the terminals from `subscribeTerminalMetadata` and reads each process's command line and working directory from `/proc`. After a fresh boot, it opens a T3 terminal for each command in the same directory and runs it again with `terminal.open` and `terminal.write`, so the terminals show in T3 Code. Memory restores skip this, because the processes still run.

This needs a T3 token with terminal write access. Phase 2 finds the scope name. The behaviour is a setting, on by default.

#### Rejected

- **A checkpoint tool such as CRIU inside the sandbox.** Memory snapshots already do this job. On the VM runtime CRIU might work, but nobody tested it.
- **One sandbox kept up forever at a small size.** Modal can't resize a running sandbox, and the 24-hour limit still applies.

---

## 5. Architecture

```
                  User's Mac                                        User's Modal workspace
┌─────────────────────────────────────────────┐        ┌──────────────────────────────────────────────────┐
│ T3 Code ──ws──> t3modal service (launchd)   │  https │ t3modal-controller, a deployed Python app        │
│                  ├ proxy: one localhost     │───────>│  HTTPS API: machines, credentials, budget, ops   │
│                  │ port per machine, capture│        │  Every minute: due wakes, rollovers, cleanup     │
│                  ├ local API ← t3modal CLI  │        │  Volume t3modal-state: machines/<id>.json        │
│                  ├ MCP server               │        │  Secrets: t3modal-admin, -hmac, -github, -claude │
│                  └ notifications, Keychain  │        └──────────────┬───────────────────────────────────┘
│ ~/.t3modal/venv: modal CLI, setup only      │                       │ creates, snapshots, ends
└──────────────────┼──────────────────────────┘                       ▼
                   │ https/wss through connect-token URLs, new at each wake
                   ▼
         ┌──────────────────────────────────────────────────────────────────┐
         │ Sandbox m-<machineId>, runtime vm, latest snapshot, 24 h timeout │
         │  tini → /opt/t3modal/init.sh                                     │
         │   ├─ t3 serve as user t3 on :3773          ← connect URL A       │
         │   ├─ daemon on :3774 (Node 22)             ← connect URL B       │
         │   └─ dockerd as root                                             │
         └──────────────────────────────────────────────────────────────────┘
```

---

## 6. The controller: `modal/controller/app.py`

### 6.1 Deployment

- `t3modal setup` runs `modal deploy modal/controller/app.py --name t3modal-controller` from the managed Python environment. It runs it again whenever the bundled `CONTROLLER_REVISION` differs from `GET /version`.
- The controller source ships inside the `t3modal` binary. Deploying never downloads controller code.
- Setup creates these resources, only if absent:
  - The Secret `t3modal-admin`, holding `ADMIN_TOKEN`, 32 random bytes. The laptop keeps a copy in the Keychain.
  - The Secret `t3modal-hmac`, holding the key the controller derives machine secrets from (§6.7).
  - The Volume `t3modal-state`, version 2.
- The HTTPS API is one FastAPI app served with `@modal.asgi_app()`, with `scaledown_window` around 300 seconds and `@modal.concurrent(max_inputs=20)`. `min_containers=0` keeps the idle cost at zero. Modal documents about 1 second to boot a container. The cold start with FastAPI and the Modal client loaded is still to measure (§18).
- The timer is one of the 5 deployed crons Starter allows.

### 6.2 State

One file per machine, `/state/machines/<machineId>.json`:

```jsonc
{
  "id": "m_4f2a…",                 // stable machine id
  "name": "dev", "size": "default",
  "pendingSize": null,             // applied at the next wake
  "images": ["im-new", "im-prev"], // newest first. [] means the base image
  "imageHasDbBackup": [true, false],
  "memorySnapshotId": null, "memoryChainStartedAt": null,
  "baseImage": "t3-<t3Version>-<provisionRevision>",
  "sandboxId": null, "startedAt": 0, "stoppedAt": 0,
  "endpoints": {"t3": {"url": "https://…", "token": "…"}, "daemon": {"url": "https://…", "token": "…"}}, // connect URLs and tokens, made at each wake (ADR 0003)
  "lastSleep": {"sequence": 4812, "acked": true},
  "nextDueAt": null,               // ms since epoch: next automation, held webhook or limit reset
  "credentialMode": {"github": "all", "claude": "all"},
  "wakeLog": [0, 0],               // recent wake times, for rate limits
  "runs": [{"sandboxId": "sb-…", "start": 0, "end": 0, "cpuLimit": 4, "memLimitGiB": 8}],
  "costByDay": {"2026-11-18": 0.21} // from per-tag stats (§6.4)
}
```

A second file, `/state/budget.json`, holds `{monthlyUsd, warnedAt, blockedAt}`.

Rules:
- Every read first calls `volume.reload()`. Every write goes to a temporary file, is renamed into place, and is followed by `volume.commit()`.
- A per-machine lease serializes operations. The lease is a `modal.Dict` key `lock:<id>` with a 2-minute expiry that the holder renews. If leasing fails, sandbox names still prevent two copies of a machine.
- The laptop keeps a copy of each machine's image ids. If a state file is lost, `POST /machines/{id}/import` rebuilds it.
- `runs` and `costByDay` older than two months are pruned.

### 6.3 API

Long operations return `202 {opId}`. The caller polls `GET /ops/{opId}`, because Modal web requests time out after about 150 seconds.

Authentication:
- The laptop service sends `Authorization: Bearer <ADMIN_TOKEN>`.
- The daemon signs each request with its machine secret (§6.7).

| Method and path | Caller | What it does |
|---|---|---|
| `GET /version` | laptop | Controller revision and Modal SDK version |
| `POST /machines` `{name, size, t3Version}` | laptop | Registers the machine, builds or reuses the base image (§7.1), and wakes it. Returns `{id, machineSecret}` once |
| `POST /machines/{id}/wake` `{force}` | laptop, timer | If running, returns the connect URLs and tokens. Otherwise checks the budget (§6.5) and starts a sandbox (§9.2) |
| `POST /machines/{id}/checkpoint` | laptop, timer | Disk snapshot without ending the sandbox (§9.7) |
| `POST /machines/{id}/sleep` `{sandboxId, sequence, mode, wakeAfter}` | daemon | Snapshots and ends the sandbox (§9.3) |
| `POST /machines/{id}/heartbeat` `{sandboxId, nextDueAt, keepAwakeUntil}` | daemon, every 60 s | Liveness for cleanup and schedule data for the timer |
| `GET /machines` | laptop | Every machine's power state, sandbox id, connect URLs and tokens, `stoppedAt`, `lastSleep`, `pendingSize` and image count |
| `GET /machines/{id}` | laptop | The same for one machine |
| `POST /machines/{id}/endpoints` | laptop | Makes fresh connect tokens for the running sandbox and returns its URLs and tokens. The laptop calls it after a 401 from Modal |
| `POST /machines/{id}/resize` `{size}` | laptop | Sets `pendingSize` |
| `POST /machines/{id}/terminate` | laptop | Ends the sandbox without a snapshot, for "delete anyway" or a stuck sandbox |
| `DELETE /machines/{id}` | laptop | Ends the sandbox and deletes its images, memory snapshot and state |
| `POST /machines/{id}/import` `{images}` | laptop | Rebuilds a lost state file |
| `GET /usage?since=<unix>` | laptop | Dollars per machine from `costByDay`, plus an estimate for hours the stats don't cover yet. Includes machines deleted this month |
| `GET /budget`, `PUT /budget` `{monthlyUsd}` | laptop | Reads or sets the monthly budget |
| `PUT /credentials/{kind}` `{values}` | laptop | Creates or replaces the Secret `t3modal-<kind>`, where kind is `github` or `claude`. The `github` Secret also holds the commit name and email (§12.2). Values travel in the HTTPS body, never on a command line |
| `DELETE /credentials/{kind}` | laptop | Deletes that Secret |
| `GET /credentials` | laptop | Which kinds exist and when they were set. Never returns values |
| `GET /ops/{opId}` | laptop, daemon | Status of a long operation. A daemon may read only its own machine's operations |

### 6.4 The timer: `@app.function(schedule=modal.Period(minutes=1))`

1. **Due wakes.** A sleeping machine with `nextDueAt` at most 2 minutes away, and later than its `stoppedAt`, gets a wake. At most one due wake per machine every 15 minutes (§15.2). Due wakes obey the budget (§6.5).
2. **Rollovers.** A running sandbox older than 23 hours 30 minutes, with no sleep in progress, gets a call to the daemon's `/rollover` (§9.6).
3. **Checkpoints.** A machine busy for more than 2 hours since its last snapshot gets a checkpoint (§9.7).
4. **Cleanup.** `Sandbox.list(app_id, tags={"t3modal": ...})` is compared with the state files. A running sandbox that no state file names as current is ended. These come from interrupted creates. A sandbox with no heartbeat for 15 minutes is only logged, because its daemon may be stuck while an agent works. The 24-hour timeout bounds its cost.
5. **Retries.** A sandbox whose end failed after a successful snapshot is ended again.
6. **Costs.** Once an hour, `app.sandboxes.stats(since, group_by_tags=["t3modal"])` for the hours since the last run, added to each machine's `costByDay`. One call covers at most 7 days, so the controller never falls behind by more than that. The stats ignore credits.

### 6.5 Budget

- The budget is a monthly dollar amount, $20 by default, set with `t3modal budget <usd>`.
- Spending is the month's gross sandbox cost. That is `costByDay`, plus an estimate for hours the stats don't cover yet. The estimate uses the machine's average hourly cost over its last 7 days, or $0.10 an hour for a new machine. When the stats arrive, they replace it.
- Credits, snapshot storage and the controller's own runs are outside the budget. `t3modal status` shows the controller's cost as a separate estimate.
- At 80%, the laptop service shows a notification once a month.
- At 100%, the controller refuses new wakes with `budget_exceeded`. That covers wakes from the laptop, due wakes and the wake half of a rollover. Running machines finish their work and sleep as usual. A machine refused a due wake stays asleep, and the laptop shows a notification.
- `t3modal wake --force` and the MCP tool's `force` flag override the block for one wake. A machine secret can never set `force`.

### 6.6 Failure handling

| Failure | What happens |
|---|---|
| The disk snapshot fails | The controller doesn't end the sandbox. The operation fails. The daemon restarts T3, returns to active and tries again after the next idle period. After 3 failures in a row, the laptop shows a notification |
| Writing the state file fails after a snapshot | The controller doesn't end the sandbox. The image is kept, and the next successful sleep's cleanup removes it |
| Ending the sandbox fails after the snapshot and state write | The state already marks the machine asleep. The timer retries every minute. If someone uses the machine meanwhile, the daemon sees the sleep didn't finish after 5 minutes, restarts T3 and returns to active. The next sleep saves a newer snapshot |
| The controller crashes during an operation | The lease expires after 2 minutes. The daemon times out on the operation and returns to active |
| The controller is unreachable during a sleep | The daemon stays up and retries with backoff. Cost continues. If the sandbox nears its 24-hour limit while the controller is still unreachable, the daemon writes the database backup, and the laptop shows a notification if it can reach the daemon. Nothing can save the disk without the controller, so this one case can lose data. It needs Modal's own API to be down for hours |
| A wake fails from `images[0]` | The controller retries from `images[1]` and flags `restoredFromPrevious`. The laptop shows a notification |
| A wake finds a sandbox with the same name running | The controller adopts it, makes fresh connect tokens and returns its URLs and tokens |
| A laptop wake and a timer wake race | The lease orders them. The second finds the machine running |
| A memory snapshot or restore fails | The controller falls back to a disk sleep, or to a fresh boot from `images[0]`. After 3 failures in a row, the laptop shows a notification |
| A VM sandbox can't start | The wake fails with a clear error. There is no fallback to the default runtime (ADR 0004) |

### 6.7 How machines authenticate

Each machine has a secret: an HMAC of its id under the controller's `t3modal-hmac` key. The controller passes it to the sandbox at create in an ephemeral Secret (`modal.Secret.from_dict`). The laptop gets it once from `POST /machines` and keeps it in the Keychain.

The machine secret is used in two places:
- The daemon's API requires `Authorization: Bearer <secret>`, compared in constant time.
- Requests from the daemon to the controller are signed as `HMAC(secret, method, path, timestamp, nonce, body)`. The controller rejects timestamps more than 60 seconds off and nonces seen in the last 2 minutes.

A machine secret allows actions on that machine only: sleep, heartbeat and reading its own operations. It can't wake machines, read credentials, change the budget or delete anything.

---

## 7. Inside the sandbox

### 7.1 Base image

The controller builds the base image, and Modal caches it:

```python
base = (modal.Image.debian_slim(python_version="3.12")
  .apt_install("git", "curl", "ca-certificates", "build-essential", "sqlite3", "ripgrep", "jq",
               "unzip", "openssh-client", "tini", "sudo", "procps", "gh", "docker.io")
  .run_commands(
     "curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs",  # node:sqlite needs 22.5 or later
     "useradd -m -s /bin/bash t3 && echo 't3 ALL=(ALL) NOPASSWD:ALL' >/etc/sudoers.d/t3",
     f"su - t3 -c 'curl -fsSL https://t3.codes/install.sh | T3CODE_VERSION={t3_version} sh'",
     f"npm i -g @anthropic-ai/claude-code@{claude_version}",
     "git config --system user.name t3modal && git config --system user.email t3modal@localhost")
  .add_local_dir("sandbox/", "/opt/t3modal", copy=True))   # init.sh, daemon.mjs, provision.sh
```

- Every sandbox runs with `runtime="vm"`. `sudo`, Docker and cgroup CPU stats work only on it, and Modal recommends it for memory snapshots. There is no fallback to the default runtime (ADR 0004). On the default runtime, `sudo` fails because the container sets "no new privileges", and Docker fails on iptables.
- T3 runs as the user `t3`, not root. Claude Code refuses its skip-permissions mode as root.
- The image sets a placeholder git identity in the system git config, because T3 can't make a project's first commit without one. `init.sh` replaces it at every boot (§12.2).
- The image includes Docker, and `init.sh` starts `dockerd` at boot (§7.2). An idle `dockerd` uses about 40 MB and almost no CPU, which fits inside the 1 GiB request that Modal bills anyway.
- Each t3modal release pins the versions of Node, T3 and Claude Code. The image is keyed by T3 version and provisioning revision.

### 7.2 `init.sh`, the supervisor

`init.sh` runs under `tini` as the sandbox's first process:

1. On a fresh boot from an image with the database backup marker, it replaces `statev2.sqlite` with the backup (§4.4).
2. It writes `/etc/machine-info` with the machine's name.
3. It exports credentials from the attached Secrets, following the machine's credential modes (§11, §12). It writes the commit name and email from the GitHub Secret to `/etc/gitconfig`, or keeps the placeholder (§12.2).
4. It runs three supervised loops. The first two run as the user `t3`:
   - `t3 serve --host 0.0.0.0 --port 3773`. Both flags are required. Without `--port`, T3 picks the first free port from 3773, and without `--host` it doesn't listen on the container's interface. The loop doesn't restart T3 while `~/.t3modal/t3.hold` exists, which lets the daemon stop T3 for a disk sleep.
   - `node ~/.t3modal/daemon.mjs`, or `/opt/t3modal/daemon.mjs` before the first provisioning. It restarts on exit, so an upgraded daemon replaces itself without restarting T3.
   - `dockerd`, as root, in the background. T3 doesn't wait for it.

`init.sh` never exits, because the sandbox ends without a snapshot when its first process exits. A crashing loop retries with backoff. The 24-hour timeout bounds the cost.

The daemon stops T3 by the PID the loop records, never by matching command text. In Phase 0, `pkill -f 't3 serve'` also matched the sandbox's own start command and ended the sandbox.

`init.sh` leaves `ANTHROPIC_API_KEY` unset, because it outranks `CLAUDE_CODE_OAUTH_TOKEN` in Claude Code.

### 7.3 The daemon: `apps/daemon`

The daemon keeps t3boat's idle logic: running agents, actions by a person seen in T3's SQLite, CPU load, keep-awake, `nextDueAt`, the cap on "can't tell" states, and drain with manifest and acknowledgement. It adds standby, memory and disk sleeps through the controller, heartbeats, rollover timing, database backups and command recording.

Phases: `active`, `standby`, `draining`, `quiescing`, `stopping`. During `stopping`, `t3modal status` shows "saving", because a snapshot after a large install can take minutes.

Operations, all requiring the machine secret:
- Idle logic: `GET /status`, `POST /activity`, `POST /drain`, `POST /keep-awake`, `POST /sleep-ack`.
- Setup: `POST /provision`, `POST /restart-t3`, `POST /rollover`.
- Claude Code sign-in (§11): `POST /login/start` returns `{url, needsPaste}`. Also `POST /login/input {text}`, `GET /login/status`, `POST /login/cancel`.
- GitHub (§12): `POST /github/login/start` returns `{url, code}`. Also `GET /github/status`.
- Projects: `POST /projects/env {projectPath, contents}` writes a `.env` file with mode 0600.

There is no endpoint that runs arbitrary commands in release builds. Development builds add `POST /exec` behind a build flag.

After 20 failed authentication attempts in a minute, the daemon refuses all requests for 5 minutes and logs the event.

### 7.4 `provision.sh`

The daemon's `/provision` runs it, and running it twice is safe. It:
- Checks T3's version and reinstalls only during an upgrade.
- Sets `linkedEnvironmentsEnabled` to true in T3's `settings.json`.
- Installs or upgrades the daemon under `~/.t3modal/`, then restarts the daemon.
- Mints the daemon's read-only T3 token once and keeps it in `~/.t3modal/config.json` with mode 0600.
- When asked, mints the proxy's upstream token and returns `{environmentId, upstreamToken}`. The token goes back once over the daemon's connect URL and is never logged.
- Restarts T3 through the hold file only when something T3 depends on changed.

---

## 8. The laptop service: `apps/service`

### 8.1 Parts

One Bun process, started at login by a launchd agent, `~/Library/LaunchAgents/dev.t3modal.service.plist`. It holds:
- The **proxy**, with one localhost port per machine (§10).
- The **machine runtime**: power states, wake, sleep, resize, rollover notices, polling (§9).
- The **local API** on `127.0.0.1`, used by the CLI and the MCP server, and later by the tray.
- The **MCP server**.
- **Notifications** through macOS's notification center.
- **Secrets** in the Keychain (§8.5).

State that isn't secret lives in `~/.t3modal/state.json`, written atomically.

### 8.2 Local API and CLI

The local API listens on `127.0.0.1` only. Every request needs a token from `~/.t3modal/api-token`, mode 0600. Requests with a browser `Origin` header are rejected, so web pages can't call it.

The `t3modal` binary is both the service and the CLI. Commands:

| Command | What it does |
|---|---|
| `t3modal setup` | Setup and upgrades (§14) |
| `t3modal create <name> [--size]` | Creates a machine and pairs it with T3 Code |
| `t3modal list`, `t3modal status [name]` | Power state, size, URLs, cost this month, "restarts in X h" |
| `t3modal wake <name> [--force]` | Wakes a machine. `--force` overrides the budget block |
| `t3modal sleep <name>` | Asks the daemon to drain now |
| `t3modal resize <name> <size>` | §9.5 |
| `t3modal delete <name> [--anyway]` | §9.8 |
| `t3modal pair <name>` | Prints a new pairing link, valid for 15 minutes, and copies it to the clipboard |
| `t3modal project add <name> <repo> [--env <file>]` | Clones a repo through T3 and writes its `.env` |
| `t3modal accounts github\|claude` | §11, §12 |
| `t3modal budget [usd]` | Shows or sets the monthly budget |
| `t3modal logs [name]` | Service logs, or a machine's daemon log |

A Tauri tray later becomes another client of the same local API.

### 8.3 MCP server

The MCP server keeps t3boat's tool names, so agents' prompts carry over: `create_machine`, `list_machines`, `wake_machine`, `sleep_machine`, `resize_machine`, `delete_machine`, `add_project`. `wake_machine` takes a `force` flag. The server listens on localhost, checks `Host` and `Origin`, and calls the local API.

### 8.4 Notifications

The service shows a macOS notification when:
- A machine will roll over in 20 minutes while kept awake (§9.6).
- A sleep failed 3 times in a row.
- Memory sleeps or memory restores fell back 3 times in a row (§4.4).
- A wake fell back to the previous image.
- Spending reached 80% of the budget, or the budget blocked a wake.
- A GitHub token expires within 7 days.

### 8.5 Secrets on the laptop

The Keychain holds the controller's admin token, each machine secret and each upstream T3 token. Connect tokens change at every wake, so the service keeps them in memory only. After a 401 from Modal, it gets fresh ones from `POST /machines/{id}/endpoints` (ADR 0003). The service uses a Keychain library that calls the macOS Security framework directly, never the `security` command with a secret as an argument, because arguments show in `ps`. Phase 3 picks a library that works under Bun. Later, on Linux, the fallback is a file in `~/.t3modal/` with mode 0600.

---

## 9. Machine lifecycle

Power states: `unknown`, `starting`, `running`, `standby`, `stopping`, `stopped`, `missing`. Their sources are the controller's `GET /machines` and the daemon's status.

### 9.1 Create

1. `POST /machines {name, size, t3Version}`. The CLI shows "Preparing image". The first machine on a new T3 version takes 1 to 3 minutes for the image build. Later ones reuse the cached image.
2. Poll the operation until the machine runs and its URLs are known.
3. `POST {daemonUrl}/provision {t3Version, name, mintUpstream: true}`, which returns `{environmentId, upstreamToken}`.
4. Apply per-machine sign-ins if the user chose "this machine" mode, such as a GitHub device code or a Claude Code code paste (§11.3, §12.2). Credentials for all machines and the git identity come from the attached Secrets at every boot (§7.2).
5. `POST /machines/{id}/checkpoint`. Until the first sleep, this snapshot is the only saved copy of the machine's T3 environment id and tokens.
6. Assign the next free localhost port and save the record. Print the proxy's pairing link and copy it to the clipboard. The link lasts 15 minutes, and `t3modal pair` makes a new one. The user pastes it into T3 Code under Settings, Environments, Add environment (§10.2). In Phase 0, the `t3code://pair?pairingUrl=` deep link did nothing in T3 Code 0.1.11, so v1 doesn't use it.

### 9.2 Wake

1. `POST /machines/{id}/wake`, then wait for the operation. The controller:
   - takes the lease and checks the budget;
   - does a memory restore if valid (§4.4), or else starts a sandbox from `images[0]`;
   - starts it with `name="m-<id>"`, `runtime="vm"`, `_experimental_enable_snapshot=True`, the size's `(request, limit)` pairs, `timeout=24h` and the entrypoint `init.sh`, with no `encrypted_ports`;
   - attaches the machine's ephemeral Secret and each `t3modal-<kind>` Secret that exists;
   - tags it `{"t3modal": id}`;
   - creates connect tokens for ports 3773 and 3774, stores the URLs and tokens, and opens a new entry in `runs`.
2. Keep the new connect URLs and tokens in memory. They never go to the state file or the Keychain (§8.5).
3. Poll `GET {t3Url}/.well-known/t3/environment` until the environment id matches.
4. If the T3 version or provisioning revision differs, call `/provision`.
5. Tell the daemon about the activity that caused the wake, then send any messages the proxy held (§10.4).

In Phase 0, T3 answered 0.9 to 1.7 s after a memory restore. A fresh boot on the VM runtime took 4.2 to 11.0 s, 5.1 s typical. The controller's cold start and the provisioning check come on top. The proxy holds T3 Code's message meanwhile, so the user doesn't resend it.

### 9.3 Sleep, decided on the machine

Disk sleep:

```
daemon: idle for 5 minutes → standby for 1 hour
  → draining: publish the manifest; the laptop refreshes the capture and acknowledges
  → quiescing: create t3.hold, stop T3, checkpoint the database, sync
  → POST /sleep {mode: "disk"} to the controller; phase becomes stopping
controller: snapshot_filesystem(ttl=None) → write state and commit → end the sandbox
laptop: sees stopping, then the controller reports stopped
```

A memory sleep follows §4.4 and keeps T3 running.

Rules carried over from t3boat:
- Any use during `standby` or `draining` cancels the sleep.
- Use during `quiescing` restarts T3 and cancels the sleep.
- Use during `stopping` is too late. The proxy waits for `stopped`, then wakes the machine.
- `t3modal sleep` calls the daemon's `POST /drain`.

### 9.4 Detecting a sleeping machine in the proxy

Modal's edge can answer a request to a connect URL in three ways. The proxy tells them apart from T3's own answers, whose bodies are T3's JSON (ADR 0003):

| Answer | Meaning | What the proxy does |
|---|---|---|
| 404 "Sandbox not found" | The sandbox has ended. Phase 0 saw this within 2 s of the end | Asks the controller for the machine's state and current URLs, and wakes the machine only if it isn't running |
| 401 | The connect token is missing or no longer valid | Gets fresh tokens from `POST /machines/{id}/endpoints` and retries once |
| No answer | A network error | Retries once after 2 s, then acts as for a 404 |

URLs change at every wake, including a memory restore.

### 9.5 Resize

Modal fixes a sandbox's size at create:
- A sleeping machine gets the new size at its next wake.
- An idle machine sleeps with `wakeAfter: true` and wakes at the new size, which takes about a minute.
- A working machine gets the new size after its next sleep.

A pending resize forces a disk sleep, because a memory restore must use the same size.

### 9.6 Rollover

Modal ends a sandbox at its timeout without a snapshot, so t3modal rolls machines over first:
- **From 23 hours,** the next time the machine is idle, the daemon sleeps it. If it is kept awake or has work due soon, the sleep uses `wakeAfter: true`.
- **At 23 hours 40 minutes,** the daemon rolls over even if an agent is working. The laptop shows a notification 20 minutes earlier. Stopping T3 interrupts the running turn, which T3 marks as interrupted, and the user can resume it.
- **The timer** calls `/rollover` on any sandbox older than 23 hours 30 minutes, in case the daemon stopped working.
- `t3modal status` shows "restarts in X h" for a machine kept awake for long periods.

Phase 0 showed that a memory restore starts a new full timeout. A machine that sleeps at least once a day therefore never reaches the limit.

### 9.7 Checkpoints while busy

A sandbox that dies unexpectedly loses everything since the last snapshot. While a machine has been busy for more than 2 hours, the timer takes a checkpoint every 2 hours:
1. The daemon writes the database backup and marker.
2. The controller takes a disk snapshot without ending the sandbox.

A fresh boot from a checkpoint restores the database from the backup. Checkpoints count toward the two-image limit. The newest checkpoint replaces the older image, never the last sleep's image.

### 9.8 Delete

If an agent is working, `t3modal delete` refuses unless given `--anyway`. Then it calls `DELETE /machines/{id}`, removes the machine's capture and Keychain items, and keeps its port retired so T3 Code never reaches a different machine at an old address. Spending from deleted machines stays in the month's totals.

### 9.9 Polling

- `GET /machines` every 60 seconds, and every 5 seconds while a machine is changing state.
- The daemon's `/status` every 5 seconds while a machine runs.

---

## 10. Proxy, pairing and capture

### 10.1 T3's protocol

t3boat's `docs/t3-protocol.md` describes how T3 Code talks to a T3 server. We use it as a reference. For types, we use T3's own `contracts` package, so a T3 update that changes a call shows up as a type error. The package, `@t3tools/contracts`, is private and not on npm, so we pin it as a git dependency at the T3 version the image uses.

### 10.2 Pairing and auth

- The proxy listens on `127.0.0.1:<port>`, one port per machine.
- T3 Code pairs with the proxy, not the machine. The proxy runs its own small auth server and issues T3 Code its own tokens, as t3boat does.
- The proxy talks to the machine with the upstream token from provisioning, through the machine's connect URL.
- The proxy adds Modal's connect token to every upstream request as the `_modal_connect_token` query parameter. It can't use the `Authorization` header, because T3 uses that header for its own bearer token, and Phase 0 showed T3 then sees no session.
- Modal answers a request that has the token in its query with a `_modal_connect_token` cookie. The proxy removes that `Set-Cookie` header, so T3 Code never holds Modal's token. A Phase 4 test checks this.
- The proxy passes T3 Code's own query parameters through unchanged. T3's `/ws` answers 426 without `orchestrationProtocol=2`.
- The proxy removes `directEndpoints` from the server's answers, so T3 Code never bypasses it for an address that changes at every wake.

### 10.3 While the machine is awake

The proxy forwards WebSocket messages both ways. When the upstream connection drops at a sleep or rollover, the proxy keeps T3 Code's connection open and switches to answering from the capture.

In Phase 0, an idle WebSocket through a connect URL stayed open for 30 minutes and then answered a ping, so the proxy sends no keepalive pings. If the upstream connection drops while the machine is still running, the proxy reconnects at once and keeps T3 Code's connection open.

### 10.4 While the machine sleeps

The capture holds the answers to the read calls T3 Code makes when it connects. T3 Code's source shows `GET /api/auth/session`, a WebSocket ticket, then `subscribeServerConfig`, `server.probe` and `orchestration.subscribeShell`, which loads the projects and thread list. Subscriptions send their current state first, so the capture replays that and keeps the stream open. The config must keep the machine's environment id. Phase 4 records a full connect to list every call.

- **Capture refresh.** During `draining`, the proxy makes those calls itself on the upstream connection, stores the answers in `~/.t3modal/captures/<machineId>/`, and acknowledges to the daemon with the sleep's sequence number.
- **Fresh capture.** A capture is fresh only if its sequence matches the controller's `lastSleep.sequence` and the controller's `stoppedAt` came after it. This is t3boat's rule.
- **Answering from the capture.** When T3 Code connects to a sleeping machine with a fresh capture, the proxy answers those reads from it. Subscriptions get the captured state and no updates.
- **Calls that wake the machine.** Any other call holds its messages for up to 90 seconds. Those include sending a message, opening a terminal and opening a thread the capture doesn't include. During the hold, the proxy wakes the machine and then forwards them in order. If the wake fails or takes too long, the proxy returns an error that T3 Code shows.
- **Stale or missing capture.** Any call wakes the machine.

Opening T3 Code therefore doesn't wake machines, and sending a message does. The early wake is for later.

---

## 11. Agent sign-in

### 11.1 How t3boat does it, and why it doesn't carry over

t3boat runs `claude auth login` on the machine with `boat exec`. It catches the sign-in URL with a `$BROWSER` script, forwards the URL's localhost callback port with `boat forward`, and opens the URL on the laptop. Modal has no port forwarding. Tunnel ports must be chosen at create, and Claude's callback port is random.

### 11.2 Providers in v1

| Provider | Default | How it works | Fallback |
|---|---|---|---|
| Claude Code | `claude setup-token` on the laptop, shared by all machines | `t3modal accounts claude` runs `claude setup-token` on the laptop, where its localhost callback works. Claude Code prints a token for the user's subscription, valid about a year. The service sends it to `PUT /credentials/claude` as `CLAUDE_CODE_OAUTH_TOKEN` | Code paste on one machine |

Phase 0 confirmed the default. With only `CLAUDE_CODE_OAUTH_TOKEN` set, T3 showed Claude as authenticated, and a thread answered 2.6 s after the message. The token can only make model requests. It can't start Remote Control sessions or fetch claude.ai connectors.

**Claude Code code paste.** `t3modal accounts claude --machine <name>` calls the daemon's `/login/start`. The daemon runs `claude auth login` under `script`, so it has a terminal, with `$BROWSER` set to a script that writes the URL to a file. The service opens the URL. After approval, the browser shows a code. The user pastes it into the CLI, and the daemon types it into the waiting login with `/login/input`. Claude's docs describe a "Paste code here if prompted" step. Phase 2 tests that it appears when nothing answers on the localhost port. If it doesn't, the fallback is an Anthropic API key, shared by all machines.

Copying the laptop's own Claude login is rejected. On macOS it lives in the Keychain, and two devices sharing one refresh token can sign each other out.

**Antigravity is not in v1.** In Phase 0, T3's Google sign-in for Antigravity failed on a Modal machine. The `antigravity` binary refuses to call T3's browser helper without a display. Debian's `xdg-open` then fails on T3's shell-quoted `BROWSER` command. With both worked around, T3 Code still showed no sign-in link. These look like T3 bugs on a Linux machine with no screen. Antigravity can come back later, through a fixed T3 or a Gemini API key, which nobody has tested yet.

### 11.3 Credential modes on the machine

At each boot, `init.sh` applies each account's mode:
- **All machines.** It exports the Secret's variables to T3.
- **This machine.** It doesn't export them, so the machine's own sign-in on disk applies.

After a change, the daemon asks T3 to refresh its providers with `server.refreshProviders`.

---

## 12. GitHub access

### 12.1 How t3boat does it

Boat signs every sandbox in to GitHub with the account linked to the user's Boat account. It sets up `gh` as git's credential helper and renews the login at each resume. Modal does nothing with GitHub, so a new machine can't clone a private repo, push, or open a pull request, and it has no commit name or email.

### 12.2 Design

| Method | Mode | How it works | Access |
|---|---|---|---|
| Fine-grained personal access token | All machines, **default** | The user creates a token on GitHub for chosen repos and pastes it into `t3modal accounts github`. It goes to `PUT /credentials/github` as `GH_TOKEN` | Only the chosen repos and permissions |
| This laptop's `gh` login | All machines | With the user's consent, the service runs `gh auth token` and stores the result the same way | Usually every repo the user can reach |
| Device code on one machine | This machine | The daemon runs `gh auth login --web --git-protocol https`. The CLI shows the code and opens github.com/login/device | What the user approves |

The fine-grained token needs these permissions:
- Contents: read and write
- Pull requests: read and write
- Metadata: read
- Workflows: read and write, only if agents edit `.github/workflows`
- Actions and Commit statuses: read, for CI status in T3

GitHub reports a token's expiry date in each API response. The service checks it daily and notifies the user 7 days before it expires. Some organizations must approve fine-grained tokens.

At every boot, `init.sh` runs `gh auth setup-git` as `t3`, so git asks `gh` for credentials. In "all machines" mode it exports `GH_TOKEN`. In "this machine" mode it doesn't, because `GH_TOKEN` overrides a stored `gh` login.

`t3modal accounts github` also works out the commit name and email and stores them in the `t3modal-github` Secret next to the token. By default they come from the GitHub profile, with the noreply address `<id>+<login>@users.noreply.github.com`. The user can choose the laptop's `git config --global` values instead. At every boot, `init.sh` writes them to the system git config, `/etc/gitconfig`, so every machine follows the current account. A `git config --global` that the user sets inside a machine takes priority and survives boots. Without a GitHub account, the image's placeholder `t3modal <t3modal@localhost>` stays.

Rejected:
- **A GitHub App.** It needs a server holding the app's private key.
- **SSH deploy keys.** Each covers one repo and can't open pull requests.

Commit signing is out of scope for v1.

---

## 13. Sizes, costs and budget

Modal's sandbox rates are $0.00003942 per CPU core per second and $0.00000667 per GiB of memory per second. Modal bills the larger of request and use.

Phase 0 chose **mode B, small requests with limits**. Every size requests 0.25 cores and 1 GiB, with these limits:

| Size | Limit | About $ per hour at full use |
|---|---|---|
| small | 2 cores, 4 GiB | 0.38 |
| default | 4 cores, 8 GiB | 0.76 |
| large | 8 cores, 16 GiB | 1.52 |

Modal guarantees only the request, so a busy host may give a machine less than its limit. Phase 0 got the full 4 cores in one test. If agents report slow builds, the request is one number to raise.

A machine pays its full limit only while it uses it. An agent spends most of a turn waiting for the model's API. A machine averaging 0.3 cores and 2 GiB in use costs about $0.09 an hour. Standby costs about $0.06 an hour. A Modal core is 2 virtual CPUs.

The rejected mode A requested a fixed size, such as 2 cores and 8 GiB for $0.48 an hour, whether used or not.

The $20 default budget buys about 200 awake hours at that average. Starter includes $30 of credits a month, so the default budget stays inside them.

Costs come from `app.sandboxes.stats` grouped by the `t3modal` tag, which Phase 0 matched against the expected cost to within $0.0001 (§6.4). Snapshot storage has no published price. Phase 3 reads it from the workspace's usage page, as §18 says for Q4. `t3modal status` shows it if it matters. The budget counts gross sandbox cost only (§6.5).

---

## 14. Setup: `t3modal setup`

Building: `bun build --compile` produces one `t3modal` binary with the controller source and sandbox scripts embedded. For a personal install, the user clones the repo, builds and runs `t3modal setup`. Upgrading means pulling, building and running `t3modal setup` again.

Steps, each skipped if already done:
1. **Python environment.** Create `~/.t3modal/venv` and install a pinned `modal` version with hash checking: `uv pip install --require-hashes -r requirements.lock`, or `python3 -m venv` and pip if `uv` is missing. The service never uses a global `modal`.
2. **Modal sign-in.** If `modal profile current` succeeds, skip. Otherwise run `modal token new`, which opens the browser and finishes by itself. Use the `main` environment unless the user picks another.
3. **Controller.** Create the Secrets and the Volume if absent, deploy the controller, read its URL, check `GET /version`, and store the URL and admin token.
4. **Service.** Write and load the launchd agent. Create the local API token.
5. **T3 Code.** Check that T3 Code's desktop app is installed and read its version, which pins the T3 version on machines.
6. **Accounts and budget.** Ask for GitHub and Claude Code (§11, §12) and confirm the $20 budget. All of these can be skipped and set later.

---

## 15. Security model

### 15.1 What is exposed, and how it is protected

| Exposed thing | Who can reach it | Protection |
|---|---|---|
| T3, port 3773 | Anyone with the connect URL and its token | Modal answers 401 to any request without the connect token. Behind it, T3's own pairing and bearer tokens. The sandbox opens no public tunnel |
| Daemon, port 3774 | Anyone with the connect URL and its token | Modal's connect token, then the machine secret, 256 bits, compared in constant time. A fixed list of operations. A lockout after repeated failures. No arbitrary commands in release builds |
| Controller API | Anyone with the URL | The admin token for laptop calls. Signed requests with timestamp and nonce for daemon calls. A machine secret works only for that machine's sleep, heartbeat and operations |
| Local API and MCP server | Programs on the laptop | Bound to `127.0.0.1`. A token file with mode 0600. Requests with a browser `Origin` are rejected |
| Proxy ports | Programs on the laptop | Bound to `127.0.0.1`. T3 Code must pair, and the proxy checks its tokens |
| Laptop secrets | The local user | The Keychain (§8.5) |
| Snapshots | The user's Modal workspace | They contain repos and on-disk credentials. Deleting a machine deletes its images and memory snapshot |

### 15.2 Code running on a machine

Agents on a machine can read everything on it, and text in a repo could mislead an agent into misusing what it reads. That includes the Secrets in its environment, the GitHub token, the Claude token, the machine secret and its T3 tokens. Agents need these credentials to work, so this risk can't be removed. t3modal limits what a leaked credential can do:
- **GitHub.** The fine-grained token reaches only the chosen repos.
- **Other machines.** The machine secret works only for its own machine. "This machine" mode for every account isolates machines completely.
- **Billing.** A machine secret can't wake machines or override the budget. The controller limits `wakeAfter` to one per 10 minutes per machine, and due wakes to one per 15 minutes per machine. The budget caps the month.
- **Workspace.** The admin token and the Modal login never reach a machine.

Inside a machine, the user `t3` has passwordless sudo and can run Docker. The VM is the security boundary, not the user account.

### 15.3 Supply chain

- The controller source and sandbox scripts are compiled into the `t3modal` binary.
- The `modal` package is pinned and checked against hashes. `bun.lock` pins the TypeScript dependencies.
- The base image pins Node, T3 and Claude Code per release.
- Tokens never appear on command lines or in logs. They travel in environment variables or HTTPS bodies. The daemon and the service remove anything that looks like a token from log lines.

---

## 16. Work plan

### Phase 0: experiments, 3 to 4 days

Done on 2026-11-19. Throwaway scripts in `experiments/phase-0/` ran 8 experiments against the user's Starter workspace for under $1. The reading is in `docs/phase-0-research.md` and the measurements are in `docs/phase-0-findings.md`. Phase 0 chose sizing mode B, memory sleeps on, the VM runtime for every machine and connect-token URLs, and moved Antigravity to later. The grilling round on 2026-11-19 settled the decisions that followed (§19). §18 lists what is still open.

### Phase 1: repository and controller, 5 to 6 days

- A Bun workspace with `apps/service`, `apps/daemon`, `packages/shared` and `modal/controller` (§20).
- The controller: the API (§6.3), operations, state and lease, disk and memory sleeps, wake fallbacks, image cleanup, the timer, credentials, usage, budget, rate limits and signed requests.
- Tests: `pytest` on pure logic, and an opt-in real-Modal suite (`T3MODAL_IT=1`).
- Done when a full cycle runs through curl alone: create, sleep, a timer-triggered due wake, a memory sleep and restore, a budget block, and cleanup of an orphaned sandbox.

### Phase 2: the sandbox, 5 to 6 days

- The base image, `init.sh`, `provision.sh` and the daemon: idle logic, standby, quiesce, database backup, heartbeat, rollover, Claude Code code paste, GitHub device code, `.env` writes and command recording.
- Done when, in a sandbox created by the controller:
  - T3 answers through its connect URL, and Modal turns away requests without the token.
  - The daemon reports idle and busy correctly.
  - A database restored from the backup passes `PRAGMA integrity_check`.
  - Claude Code code paste finishes.
  - `dockerd` runs after boot, and `docker run hello-world` works as `t3`.

### Phase 3: laptop service core and CLI, 6 to 8 days

- The `Cloud` interface and controller client with operation polling and typed errors.
- The machine runtime (§9), state file, Keychain, launchd agent, local API, CLI and `t3modal setup` (§14).
- Done when `t3modal setup` works on a clean Mac, and `t3modal create`, `wake`, `sleep`, `resize` and `delete` work against Modal.

### Phase 4: proxy, pairing and capture, 8 to 10 days

- WebSocket forwarding, pairing and auth, `directEndpoints` removal, capture refresh and freshness, answering from the capture, holding and forwarding calls that wake the machine (§10). Modal's error answers and token refresh (§9.4), and removing Modal's token cookie.
- Done when a user can:
  - pair a machine with T3 Code and chat;
  - close the laptop in the middle of a turn and reopen it later, to find the turn finished and the machine asleep;
  - quit and reopen T3 Code without waking the machine;
  - send a message that wakes it.

### Phase 5: accounts, projects, MCP, notifications, 5 to 6 days

- GitHub and Claude Code accounts (§11, §12), projects and `.env` files, the MCP server, notifications, costs and the budget in the CLI.
- Done when every v1 goal in §1 works.

### Phase 6: hardening and docs, 3 to 4 days

- Failure injection for every row in §6.6.
- A test build that forces rollover at 30 minutes.
- README with a cost warning and credit to t3boat. `docs/architecture.md`.

The total is about 7 to 9 weeks for one engineer. Phases 1 and 2 can run in parallel.

---

## 17. Testing

- **Laptop service.** `bun test`. The machine runtime runs against a fake `Cloud`: racing wakes, a sleep during a wake, use during `quiescing` and `stopping`, capture freshness, the budget block and each failure in §6.6. The proxy is tested against a fake T3 server built from T3's types.
- **Daemon.** `node --test`, because the daemon runs on Node. A fake T3, made of an HTTP shell endpoint and a SQLite fixture, and a fake controller. Tests cover idle decisions, standby, drain and acknowledgement, quiesce failures, backup and restore, rollover timing with an injected clock, the authentication lockout and the code paste flow.
- **Controller.** `pytest` for state transitions, cost math, due-wake selection, budget, rate limits and request signing. The real-Modal suite is opt-in and ends everything tagged `t3modal-it` on teardown.
- **Security.** Every daemon, controller and local API endpoint rejects missing, wrong, replayed and other-machine credentials. The local API rejects browser origins.
- **Manual acceptance checklist:**
  - A turn finishes with the laptop closed.
  - A scheduled automation runs while the laptop is off.
  - Reopening T3 Code doesn't wake a sleeping machine.
  - A private repo clones.
  - Claude Code signs in, in both modes.
  - Docker runs a container on a machine, and its images survive a disk sleep.
  - Resize works while idle and while working.
  - Delete works while working.
  - A rollover completes.
  - A memory restore keeps a running dev server.
  - The budget blocks a wake, and `--force` overrides it.

---

## 18. Phase 0 answers and open questions

Phase 0 answered 15 of the 21 questions. [docs/phase-0-findings.md](docs/phase-0-findings.md) has the measurements, and the sections above use them.

| # | Question | Answer |
|---|---|---|
| 1 | Snapshot time with 5 to 20 GB of changes | 9 s for 5 GB, 15 s for 20 GB, 199 s for 200,000 small files. File count sets the time |
| 3 | Do long snapshot chains slow down or break? | No. 50 generations, with older images deleted, booted faster at the end |
| 5 | Memory snapshots with T3, disk snapshots on VM, the timeout after a restore | All work. Processes keep their PIDs. A restore gets a new full timeout |
| 6 | `(request, limit)` pairs and CPU above the request | Both work. 4.00 cores used with a 0.25-core request. A Modal core is 2 vCPUs |
| 7 | Do WebSockets through a connect URL survive long idle periods? | Yes. One stayed open for 30 minutes with no traffic, then answered a ping |
| 8 | What an ended sandbox's tunnel answers | Nothing. The connection closes with no HTTP answer |
| 9 | `/proc/stat`, cgroup stats, `node:sqlite`, `script`, `sudo`, Docker | All work on `runtime="vm"`. `sudo`, Docker and cgroup stats fail on the default runtime |
| 10 | `t3 serve` without systemd | Works as user `t3`. Needs both `--host` and `--port` |
| 12 | Billed usage per tag | `app.sandboxes.stats(group_by_tags=...)` matched the expected cost. Per-sandbox stats are refused on Starter |
| 13 | Claude Code with only `CLAUDE_CODE_OAUTH_TOKEN` | T3 shows Claude as authenticated, and threads work |
| 14 | Antigravity's Google sign-in on a machine | Fails. Antigravity moves to later (§11.2) |
| 16 | Connect tokens with T3 | HTTP and WebSocket work with the token as a query parameter. The `Authorization` header collides with T3's bearer |
| 17 | Starter limits | $30 of credits a month, 100 containers, 5 deployed crons, 1 day of logs |
| 18 | How long a connect token lasts, and tokens for two ports | Tokens for 3773 and 3774 work at once, each on its own URL. A second token for a port leaves the first valid. Every token still worked at 90 minutes. Longer is untested |
| 19 | What an ended sandbox's connect URL answers | 404 "Sandbox not found" within 2 s |

Still open:

| # | Question | Needed by | If the answer is bad |
|---|---|---|---|
| 2 | Read speed after a memory restore, and on a second read after a fresh boot | §4.4, Phase 2 | Warm the repo's files after a fresh boot |
| 4 | What snapshot storage costs. Modal publishes no price | §13, Phase 3 | Fewer images, shown in costs |
| 11 | The controller's cold start with FastAPI loaded | §9.2, Phase 1 | Keep one container warm during the day |
| 15 | Every call T3 Code makes on connect, from a recorded session | §10.4, Phase 4 | Wake on any call the capture lacks |
| 20 | Does Claude's sign-in show the "Paste code here" step when nothing answers on its localhost port? | §11.2, Phase 2 | An Anthropic API key as the fallback |
| 21 | Does Docker's data survive a memory sleep and a disk sleep? | §7.1, Phase 2 | Document that images are rebuilt after a disk sleep |

---

## 19. Decisions

Settled in the design review on 2026-11-18. Phase 0 revised 14 and added 16:

| # | Decision | Record |
|---|---|---|
| 1 | Build our own code. Use t3boat only as a reference | ADR 0001 |
| 2 | TypeScript for the laptop service, CLI, MCP server and daemon. Python for the controller | ADR 0002 |
| 3 | A personal tool first. macOS only in v1 | §1 |
| 4 | Phase 0 runs before any product code | §16 |
| 5 | Snapshots of the whole disk | §4.1 |
| 6 | Memory sleeps, behind a setting. Decision 24 sets the default | §4.4 |
| 7 | Standby after 5 idle minutes, for 1 hour | §4.4 |
| 8 | Rerun recorded commands after a fresh boot | §4.4 |
| 9 | One localhost port per machine. v1 captures only T3 Code's startup reads | §10 |
| 10 | A background service with a CLI and MCP server in v1. The Tauri tray comes later | §8 |
| 11 | Laptop secrets in the Keychain | §8.5 |
| 12 | Installed with `bun build --compile` and `t3modal setup` with launchd | §14 |
| 13 | A $20 monthly budget by default. Warn at 80%, block new wakes at 100%, `--force` overrides | §6.5 |
| 14 | Providers in v1: Claude Code only. Antigravity, Codex and OpenCode later | §11 |
| 15 | Claude Code: `claude setup-token` for all machines, code paste per machine as the fallback. No relay | §11 |
| 16 | Antigravity leaves v1 after its Google sign-in failed in Phase 0 | §11.2 |
| 17 | GitHub: a fine-grained token for all machines | §12 |
| 18 | T3 Connect and the early wake come later | §1 |
| 19 | The controller's timer runs every minute | §6.4 |
| 20 | No marketing website in v1 | §1 |

Settled by Phase 0 on 2026-11-18 and checked in the grilling round on 2026-11-19:

| # | Decision | Record |
|---|---|---|
| 21 | Every machine runs on `runtime="vm"`, with no fallback | ADR 0004 |
| 22 | Connect-token URLs replace public tunnels. The token goes in a query parameter, with one token per port | ADR 0003 |
| 23 | Sizing mode B. Every size requests 0.25 cores and 1 GiB, with limits by size. If builds are slow, raise the request | §13 |
| 24 | Memory sleeps ship on. The Modal SDK is pinned, and any error falls back to a disk sleep or fresh boot | §4.4 |
| 25 | Docker is supported. `init.sh` starts `dockerd` at boot | §7.1, §7.2 |
| 26 | Costs come from per-tag stats, summed hourly per machine | §6.4 |
| 27 | Pairing prints a link for the user to paste into T3 Code. It lasts 15 minutes. No deep link in v1 | §9.1 |
| 28 | Each machine has a git identity from its first boot. `init.sh` writes it from the GitHub Secret to `/etc/gitconfig` at every boot | §7.2, §12.2 |

Settled in the grilling round on 2026-11-19:

| # | Decision | Record |
|---|---|---|
| 29 | The budget counts gross sandbox cost. Unreported hours use the machine's 7-day average, or $0.10 an hour for a new machine | §6.5 |
| 30 | The controller keeps connect tokens and refreshes them after a 401. The laptop keeps them in memory only | ADR 0003, §8.5 |
| 31 | The proxy tells 404 "Sandbox not found", 401 and no answer apart | §9.4 |
| 32 | No WebSocket keepalive pings. The proxy reconnects after a drop | §10.3 |
| 33 | The proxy removes Modal's token cookie | §10.2 |

---

## 20. Repository layout

```
t3-modal/
├─ implementation.md            # this file
├─ GLOSSARY.md                  # project vocabulary
├─ CLAUDE.md                    # agent instructions
├─ README.md, LICENSE           # MIT; README credits t3boat
├─ package.json, bun.lock       # Bun workspaces
├─ docs/
│  ├─ adr/                      # architecture decisions
│  ├─ agents/                   # issue tracker, labels, domain docs for agent skills
│  └─ architecture.md
├─ apps/
│  ├─ service/                  # laptop service and CLI, compiled to the t3modal binary
│  │  └─ src/                   # proxy/, capture/, machines/, cloud/, api/, cli/, mcp/, keychain/, notify/, setup/
│  └─ daemon/                   # runs on each machine; bundled to daemon.mjs for Node 22
├─ packages/
│  └─ shared/                   # controller and daemon API types, shared by service and daemon
└─ modal/
   ├─ controller/app.py         # the deployed Modal app: API and timer
   ├─ controller/requirements.lock
   ├─ controller/tests/
   └─ sandbox/                  # init.sh, provision.sh; daemon.mjs is copied in at build time
```
