# t3modal: implementation plan

t3modal is [t3boat](https://github.com/paukraft/t3boat) rebuilt on [Modal](https://modal.com) Sandboxes in place of [Boat](https://boat.dev). It runs cloud machines for T3 Code agents from the menu bar. Machines sleep when idle, so the user pays only while agents work.

The plan starts from t3boat's MIT-licensed code. It keeps the parts that don't depend on the cloud provider and replaces every part that calls Boat.

How to read this document:
- Section 2 summarizes how t3boat works.
- Section 3 compares Boat and Modal.
- Sections 4 to 14 are the design.
- Section 15 is the security model.
- Section 16 orders the work into phases.
- Section 18 lists the facts that Phase 0 must confirm before the design depends on them.

Revised 2026-11-18. This version adds pause and resume (§4.4), GitHub and agent sign-in (§10, §11), the security model (§15) and failure handling (§6.5).

---

## 1. Goals and non-goals

**Goals**
- Match t3boat's behaviour:
  - Create, resize and delete machines from the tray.
  - T3 Code pairs with a stable `http://localhost:<port>` address per machine.
  - A message to a sleeping machine wakes it.
  - A machine finishes its work and then sleeps, even with the laptop closed.
  - Add projects and their `.env` files.
  - Sign agents in.
  - Turn on T3 Connect.
  - Show costs.
  - Run a local MCP server.
- Run machines in the user's own Modal workspace with the user's own Modal login. t3modal runs no backend of its own.
- Improve on t3boat where Modal allows it:
  - Scheduled work wakes a machine even when the desktop app isn't running.
  - Snapshots never expire, so machines need no keep-alive wakes.
  - One sign-in can cover every machine.

**Non-goals for v1**
- GPU machines. Modal supports them, and the design leaves room for them.
- Supporting several cloud providers at runtime.
- Prebuilt, signed installers. Building from source is enough, as with t3boat.
- Docker inside machines. Modal sandboxes probably can't run it (§18, question 9).

---

## 2. How t3boat works

```
T3 Code ──ws──> localhost proxy (Tauri app) ──https/wss──> T3 server on a Boat sandbox
                    │                                     └─ watchdog.mjs: drains, then `boat stop self` when idle
                    └─ boat CLI: new, resume, host, exec, scp, forward, snapshot, usage, delete
```

| Area | Files in `apps/desktop/src-tauri/src` | Depends on Boat? |
|---|---|---|
| Boat CLI wrapper | `boat.rs`, 284 lines | Entirely |
| Lifecycle: power polling, wake, sleep, resize, restore, keep-alive, scheduled wakes | `machine/mod.rs`, 980 lines | Heavily |
| Provisioning: T3 install, systemd units, tokens | `machine/provision.rs`, `provision.sh` | Heavily |
| Watchdog on the sandbox | `machine/watchdog.mjs`, 463 lines | Only for `boat snapshot self` and `boat stop self`. The idle logic is generic |
| Agent sign-in | `machine/signin.rs` | Uses `boat exec` and `boat forward` |
| T3 Connect | `machine/connect.rs` | Uses `boat exec` |
| Projects, links, capture, work tracking | `machine/projects.rs`, `link.rs`, `capture.rs`, `work.rs` | Only one error message |
| Sleep-aware proxy | `proxy/*`, about 3,000 lines | Only the check for a stopped sandbox, which expects Boat's redirect page |
| App model, costs, setup states | `app.rs`, `tray.rs`, `store.rs` | Setup installs and signs in Boat. Costs come from `boat usage` |
| MCP server | `mcp.rs` | Names only |
| UI | `apps/desktop/src` | Branding, setup screens, size labels |

The proxy is the hardest part, and it doesn't depend on Boat. `docs/t3-protocol.md` documents it. t3modal reuses it almost unchanged.

---

## 3. Boat compared with Modal

### 3.1 Concept mapping

| Boat | Modal | Consequence for t3modal |
|---|---|---|
| A sandbox with a persistent disk | A gVisor container whose disk is lost when it ends | Snapshots are the only persistence (§4.1) |
| `boat stop` and `boat resume` keep the same sandbox | Only `terminate()` | Sleep saves a snapshot and terminates. Wake creates a new sandbox. The sandbox id changes at every wake |
| `boat snapshot self` as a backup | `sb.snapshot_filesystem(ttl=None)` returns an image kept forever. The default TTL is 30 days | Snapshots never expire, so keep-alive wakes go away |
| Disk deleted after 14 days stopped, then a restore | None | t3boat's restore path becomes the normal wake |
| `boat host <port> --public`, one URL per sandbox | `encrypted_ports=[...]` at create, then `sb.tunnels()[port].url` on `*.w.modal.host` | New URLs at every wake. Ports must be chosen at create |
| `boat exec`, `boat scp` | `sb.exec`, `sb.filesystem.*`, in the SDK only | t3modal's agent on the machine exposes fixed operations over HTTPS (§7.3) |
| `boat forward` | None | Sign-in uses code paste, device codes, or a relay (§10) |
| No lifetime limit | At most 24 hours | Automatic restart before the limit (§8.6) |
| systemd services | No systemd | A startup script supervises T3 and the agent (§7.2) |
| Sizes small, default, large | Any `cpu` and `memory` at create | t3modal defines sizes (§12) |
| `boat usage` in dollars | No per-sandbox dollar report found | Cost computed from runtime and published rates (§12) |
| `boat limits`, starts per day | Plan concurrency limits | The starts counter goes away |
| Boat signs sandboxes in to GitHub | Nothing | §11 |
| `boat onboard` browser sign-in | `modal token new` writes `~/.modal.toml` | Setup flow (§13) |
| Scheduled wakes only while the desktop app runs | Cron functions | The controller wakes machines on schedule (§6) |

### 3.2 Drawbacks of Modal, and how the plan handles each

The Boat side comes only from t3boat's code and docs, not from Boat's own documentation.

| # | Drawback | Modal feature that helps | What t3modal builds | What remains |
|---|---|---|---|---|
| 1 | An awake machine costs about 13 times Boat's hourly price when it uses all it requests | Billing is the larger of requested and used resources | Small requests, fast sleep (§4.4, §12) | Modal sets the rates |
| 2 | No pause and resume | Disk snapshots, memory snapshots in alpha | Layered sleep (§4.4) | A slower fresh boot about once a week |
| 3 | Sandboxes end after 24 hours | Snapshots make restarts possible | Restart before the limit (§8.6) | An occasional interrupted long turn |
| 4 | The snapshot is the only copy, so a crash loses work since the last sleep | Snapshots without terminating | Periodic checkpoints (§8.7) | A shorter loss window, not zero |
| 5 | No systemd, Docker probably unavailable | `runtime="vm"` may help, unconfirmed | Startup script (§7.2) | Docker likely unavailable |
| 6 | A sandbox can't snapshot or stop itself | Deployed functions and web endpoints | The controller (§6) | Solved |
| 7 | No GitHub login | Modal Secrets | §11 | Solved |
| 8 | No port forwarding for sign-in | Extra tunnel ports | §10 | Solved |
| 9 | New URLs at every wake | None needed | The proxy already handles it | Solved |
| 10 | No per-sandbox cost report found | `App.sandboxes.stats()`, unconfirmed | Runtime multiplied by rates | An estimate, not the exact bill |
| 11 | More setup | None | Automated setup (§13) | One more component to maintain |

t3modal fixes 5 of these fully in its own code (6 to 10). It reduces 5 (1, 2, 4, 5, 11). It can only work around 1 (3). Four limits belong to Modal's platform, and only Modal can change them: the rates, the lack of pause, the 24-hour limit and the container runtime. Modal's client SDK is open source under Apache 2.0, which helps with debugging but doesn't change what Modal's servers allow.

### 3.3 Benefits of Modal

1. Machines sleep and wake for scheduled work with the desktop app closed. t3boat's docs say Boat can't resume a sandbox on a timer.
2. Snapshots kept with `ttl=None` never expire.
3. Any CPU and memory size. A sandbox can use more CPU than it requests when the host has room. GPUs are available later.
4. No daily limit on starts.
5. A widely used platform with a Python SDK out of beta.
6. Free monthly credits on the Starter plan. The amount needs checking.
7. Images with T3 and the agent CLIs already installed, so creating a machine skips the install.
8. Modal allows one running sandbox per name, so two wakes can't start two copies of a machine.
9. Modal Secrets attach credentials at each start, so one sign-in can cover every machine.
10. Memory snapshots make a pause-and-resume style wake possible.

---

## 4. Main design decisions

### 4.1 Persistence through snapshots of the whole disk

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| `snapshot_filesystem()` of the whole disk at every sleep. The next wake creates a sandbox from that image | Keeps everything: system packages, toolchains, agent logins, git worktrees, T3's SQLite database | One image per sleep. Images may stack into long chains (§18). Snapshot time grows with the changes | **Chosen** as the long-term copy |
| `snapshot_directory("/home/t3")` mounted on a fresh base image | Clean upgrades. Smaller snapshots | Anything installed outside `/home` is lost at every sleep, which surprises agents | Fallback if whole-disk snapshots are slow or chains degrade |
| A Modal Volume mounted at `/home/t3` | No snapshot step | A network filesystem. Slow for `node_modules` and git, risky for SQLite, and system packages are still lost | Rejected for machines. Used for controller state |
| Memory snapshots | Processes keep running. Wake is fast | Alpha. Expire after 7 days. Need `runtime="vm"` | Fast wake on top of disk snapshots (§4.4). Never the only copy |

Every snapshot call passes an explicit `timeout`. The default is 55 seconds, which is too short for a large disk. Start at 600 seconds and tune it from the Phase 0 measurements.

A disk snapshot must capture a consistent T3 database. The plan uses one of two methods:
- **Disk-only sleep.** Stop T3, checkpoint its database with `PRAGMA wal_checkpoint(TRUNCATE)`, run `sync`, then snapshot.
- **Sleep with memory.** Keep T3 running. Copy the database with SQLite's online backup, which is consistent while T3 writes. On a fresh boot from that image, restore the copy before T3 starts (§4.4).

The controller keeps each machine's two newest images and deletes older ones with `modal.experimental.image_delete`. If a wake fails from the newest image, it tries the older one and notifies the user.

### 4.2 A controller app in the user's Modal workspace

Two facts lead to a controller:
- The desktop app is Rust, and Modal has no Rust SDK. The JavaScript and Go SDKs are in beta.
- Nothing inside a sandbox can snapshot or terminate that sandbox.

The controller is a small Python Modal app named `t3modal-controller`. Setup deploys it into the user's workspace. It has three parts:
- An HTTPS API that the desktop app and the agent on each machine call.
- A function that runs every minute for scheduled wakes, restarts before the 24-hour limit, and cleanup.
- State in a Modal Volume named `t3modal-state`, one JSON file per machine. It doesn't use `modal.Dict`, because Dict entries expire after 7 days unused.

The controller creates every sandbox inside its own deployed app. That allows named sandboxes, `m-<machineId>`, and Modal allows only one running sandbox per name. A wake from the desktop and a wake from the timer therefore can't start two copies of a machine.

The desktop app only makes HTTPS calls to the controller. It needs Modal's Python CLI only during setup, to sign in and deploy. t3modal installs that CLI in a Python environment it manages (§13).

A sidecar process on the laptop that uses the SDK directly was rejected. It couldn't sleep or wake machines while the laptop is closed, and that is the main promise of the product.

### 4.3 Start from t3boat's code

t3boat is MIT-licensed. t3modal imports its source at a pinned commit, keeps its copyright notice in LICENSE, and credits it in the README. About 70% of the Rust code carries over with renames: the proxy, capture, projects, links, MCP, tray and store. The new work is in `modal.rs`, which replaces `boat.rs`, and in the lifecycle, provisioning and watchdog code.

A `Cloud` trait sits between the machine runtime and the provider. Its methods are `create`, `wake`, `sleep`, `checkpoint`, `terminate`, `status`, `list` and `usage`. The runtime depends only on the trait. Tests use a fake implementation.

### 4.4 Getting close to pause and resume

Boat's pause keeps the disk and the running processes, and it wakes fast. Disk snapshots alone keep only the disk. t3modal combines four mechanisms. Mechanisms 1 and 2 depend on Modal behaviour that Phase 0 must confirm. Mechanisms 3 and 4 are t3modal's own code.

| Stage | When | What happens | Wake |
|---|---|---|---|
| Awake | In use | Normal | Not needed |
| Idle stage, mechanism 2 | Idle for 5 minutes | The sandbox stays up. Billing drops close to its small request | Instant |
| Asleep with memory, mechanism 1 | Idle for `NAP_SECS`, default 1 hour. The memory chain is under 6 days old and no resize is pending | Disk snapshot as the long-term copy, then a memory snapshot, which terminates the sandbox | Memory restore. Processes continue |
| Asleep, disk only | The chain is 6 days old or more, a resize is pending, a memory snapshot failed, or the feature is off | Quiesce, disk snapshot, terminate | Fresh boot. Recorded commands restart, mechanism 4 |

Mechanism 3 applies to both asleep stages.

#### Mechanism 1: memory snapshots

The API is in alpha, so the names may change:
- Create every sandbox with `runtime="vm"` and `_experimental_enable_snapshot=True`.
- Sleep with `sb._experimental_snapshot()`, which saves the sandbox and terminates it.
- Wake with `modal.Sandbox._experimental_from_snapshot(snapshot)`, or look the snapshot up with `modal.SandboxSnapshot.from_id(id)`.

Modal's docs list these limits:
- **A snapshot expires 7 days after creation.** A snapshot taken from a restored sandbox keeps the original expiry date, so a whole chain expires 7 days after its first snapshot. The controller stores `memoryChainStartedAt`. Once the chain is 6 days old, the next sleep saves only the disk, and the wake after it boots fresh and starts a new chain.
- **Open TCP connections close.** The proxy reconnects to the new tunnel URL. T3 Connect's tunnel dials out again. Sleep happens only when no agent is working, so no call to a model's API is cut off.
- **A restore uses the same machine type.** A pending resize forces a disk-only sleep.
- **No `exec` may run at snapshot time.** t3modal doesn't use `exec`.

Sleep order with memory:
1. The agent drains as t3boat's watchdog does. It publishes the manifest, and the proxy captures the state and acknowledges.
2. The agent copies T3's database with `sqlite3 statev2.sqlite ".backup ~/.t3modal/statev2.backup.sqlite"`, writes a marker naming the copy, and runs `sync`.
3. The controller runs `snapshot_filesystem(ttl=None)` while the sandbox still runs.
4. The controller writes the new image id to the machine's state and commits the Volume.
5. The controller runs `_experimental_snapshot()`, which terminates the sandbox, then stores `memorySnapshotId`.

If step 5 fails, the controller terminates the sandbox anyway, because step 3 already saved the disk. The next wake then boots fresh.

Wake order:
1. If `memorySnapshotId` exists, its chain is under 6 days old, and the size is unchanged, restore it.
2. Otherwise, or if the restore fails, boot fresh from `images[0]`. If the image carries the backup marker, `init.sh` replaces `statev2.sqlite` with the backup before T3 starts, because the live file may have been mid-write.

After a memory restore, the agent sees its clock jump. t3boat's watchdog already treats a tick more than 30 seconds late as a warm resume (`RESUMED_GAP_MS`) and ends any keep-awake, so that logic carries over.

#### Mechanism 2: the idle stage

The idle stage is the awake state with a longer timer. It is cheap only if machines request little CPU and memory and use more when an agent works. Sizes then become limits. Each size requests 0.25 cores and 1 GiB, with a limit of 2, 4 or 8 cores and 4, 8 or 16 GiB.

Modal accepts `(request, limit)` pairs for Functions. Phase 0 tests two things:
- whether `Sandbox.create` accepts the same pairs
- whether a sandbox reliably gets CPU above its request

Cost of the idle stage, if an idle T3 uses about 0.5 GiB:
- CPU at the 0.25-core request: about $0.035 an hour.
- Memory at the 1 GiB request: about $0.024 an hour.
- Total: about $0.06 an hour. An hour idle before each sleep adds about $0.06 per sleep.

`NAP_SECS` is a setting, and 0 turns the idle stage off. If Phase 0 shows requests can't be small, the default becomes 0 and machines sleep after 5 minutes.

#### Mechanism 3: start the wake early

The proxy already receives T3 Code's `server.reportClientActivity` heartbeat, with `visible` and `focused` flags. It also sees which chat T3 Code subscribes to. When a chat on a sleeping machine is open and T3 Code has been focused for 3 seconds, the proxy starts a wake. The user is usually still typing when the machine comes up. A wake that wasn't needed costs at most the idle time before the next sleep. The proxy starts at most one early wake per machine every 15 minutes.

#### Mechanism 4: restart recorded commands after a fresh boot

Before each sleep, the agent records the commands running in T3's terminals. It gets the terminals from `subscribeTerminalMetadata` and reads each process's command line and working directory from `/proc`. After a fresh boot, it opens a T3 terminal for each command in the same directory and runs the command again, using `terminal.open` and `terminal.write`, so the terminals show in T3 Code. Memory restores skip this, because the processes are still running.

This needs a T3 token with terminal write access. The scope name needs checking. The behaviour is a setting, on by default.

#### Rejected

- **A checkpoint tool such as CRIU inside the sandbox.** It needs kernel access that a gVisor container doesn't give.
- **One sandbox kept up forever at a small size.** Modal can't resize a running sandbox, and the 24-hour limit still applies.

---

## 5. Architecture

```
                     Laptop                                             User's Modal workspace
┌──────────────────────────────────────────────┐        ┌──────────────────────────────────────────────────────┐
│ T3 Code ──ws──> t3modal proxy, one localhost │  https │ t3modal-controller, a deployed Python app            │
│                 port per machine             │───────>│  HTTPS API: machines, credentials, ops               │
│                    │                         │        │  Every minute: due wakes, 24 h restarts, cleanup     │
│ t3modal tray app ──┤ modal.rs: controller    │        │  Volume t3modal-state: machines/<id>.json            │
│  Tauri, Rust       │ client over HTTPS       │        │  Secrets: t3modal-admin, t3modal-github/claude/codex │
│                    │                         │        └───────────────┬──────────────────────────────────────┘
│ ~/.t3modal/venv: modal CLI for setup only    │                        │ creates, snapshots, terminates
└────────────────────┼─────────────────────────┘                        ▼
                     │ https/wss through tunnels, new URLs at each wake
                     ▼
           ┌────────────────────────────────────────────────────────────────────────┐
           │ Sandbox m-<machineId>, from the latest snapshot, 24 h timeout          │
           │  tini → /opt/t3modal/init.sh                                           │
           │   ├─ t3 serve as user t3 on :3773          ← tunnel A                  │
           │   └─ agent.mjs on :3774: watchdog and control API  ← tunnel B          │
           │  :3775 sign-in relay, open only during a sign-in   ← tunnel C          │
           └────────────────────────────────────────────────────────────────────────┘
```

---

## 6. The controller: `modal/controller/app.py`

### 6.1 Deployment

- The desktop app runs `modal deploy modal/controller/app.py --name t3modal-controller` from its managed Python environment. It runs it during setup and again whenever the bundled controller's `CONTROLLER_REVISION` differs from `GET /version`.
- The controller source ships inside the desktop app. Deploying never downloads controller code from the internet.
- Setup creates three named resources, only if absent:
  - The Secret `t3modal-admin`, holding `ADMIN_TOKEN`, 32 random bytes. The desktop app keeps a copy in the OS keychain.
  - The Secret `t3modal-hmac`, holding the key the controller uses to derive each machine's secret (§6.6).
  - The Volume `t3modal-state`, version 2.
- The HTTPS API is one FastAPI app served with `@modal.asgi_app()`, with `scaledown_window` around 300 seconds and `@modal.concurrent(max_inputs=20)`. `min_containers=0` keeps the idle cost at zero. A cold start adds about 1 to 3 seconds to a wake.

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
  "urls": {"t3": "https://…w.modal.host", "agent": "https://…", "relay": "https://…"},
  "lastSleep": {"sequence": 4812, "acked": true},
  "nextDueAt": null,               // ms since epoch: next automation, held webhook or limit reset
  "credentialMode": {"github": "all", "claude": "all", "codex": "machine"},
  "wakeLog": [0, 0],               // recent wake times, for rate limits
  "runs": [{"sandboxId": "sb-…", "start": 0, "end": 0, "cpu": 2, "memGiB": 8}]
}
```

Rules for the state files:
- Every read first calls `volume.reload()`. Every write goes to a temporary file, is renamed into place, and is followed by `volume.commit()`.
- A per-machine lease serializes operations. The lease is a `modal.Dict` key `lock:<id>` with a 2-minute expiry that the holder renews. Lease entries are short-lived, so Dict's 7-day expiry doesn't matter. If leasing fails, sandbox names still prevent two copies of a machine.
- The desktop app keeps a copy of each machine's image ids. If a state file is ever lost, `POST /machines/{id}/import` rebuilds it from that copy.
- `runs` older than two months are pruned.

### 6.3 API

Long operations return `202 {opId}`. The caller polls `GET /ops/{opId}`, because Modal web requests time out after about 150 seconds.

Authentication depends on the caller:
- The desktop app sends `Authorization: Bearer <ADMIN_TOKEN>`.
- The agent on a machine signs each request with that machine's secret (§6.6).

| Method and path | Caller | What it does |
|---|---|---|
| `GET /version` | desktop | Controller revision and Modal SDK version |
| `POST /machines` `{name, size, t3Version}` | desktop | Registers the machine, builds or reuses the base image (§7.1), and wakes it. Returns `{id, machineSecret}` once |
| `POST /machines/{id}/wake` | desktop, timer | If running, returns the URLs. Otherwise creates a sandbox (§8.2) |
| `POST /machines/{id}/checkpoint` | desktop | Disk snapshot without terminating. Used once after create, and periodically while busy (§8.7) |
| `POST /machines/{id}/sleep` `{sandboxId, sequence, mode, wakeAfter}` | agent | Snapshots and terminates (§8.3) |
| `POST /machines/{id}/heartbeat` `{sandboxId, nextDueAt, keepAwakeUntil}` | agent, every 60 s | Liveness for cleanup and schedule data for the timer |
| `GET /machines` | desktop | Every machine's power state, sandbox id, URLs, `stoppedAt`, `lastSleep`, `pendingSize` and image count. Replaces `boat info`, polled every 60 seconds |
| `GET /machines/{id}` | desktop | The same for one machine |
| `POST /machines/{id}/resize` `{size}` | desktop | Sets `pendingSize` |
| `POST /machines/{id}/terminate` | desktop | Terminates without a snapshot, for "Delete anyway" or a stuck sandbox |
| `DELETE /machines/{id}` | desktop | Terminates the sandbox and deletes its images, memory snapshot and state |
| `POST /machines/{id}/import` `{images}` | desktop | Rebuilds a lost state file |
| `GET /usage?since=<unix>` | desktop | Dollars and seconds per machine, including machines deleted this month |
| `PUT /credentials/{kind}` `{values}` | desktop | Creates or replaces the Secret `t3modal-<kind>`, where kind is `github`, `claude` or `codex`. Values travel in the HTTPS body, never on a command line |
| `DELETE /credentials/{kind}` | desktop | Deletes that Secret |
| `GET /credentials` | desktop | Which kinds exist and when they were set. Never returns values |
| `GET /ops/{opId}` | desktop, agent | Status of a long operation. The agent may read only its own machine's operations |

### 6.4 The timer: `@app.function(schedule=modal.Period(minutes=1))`

1. **Due wakes.** A sleeping machine with `nextDueAt` at most 2 minutes away, and later than its `stoppedAt`, gets a wake. This is t3boat's `scheduled_wake` without the laptop. At most one due wake per machine every 15 minutes (§15.2).
2. **24-hour restarts.** A running sandbox older than 23 hours 30 minutes, with no sleep in progress, gets a call to the agent's `/rollover` (§8.6).
3. **Cleanup.** `Sandbox.list(app_id, tags={"t3modal": ...})` is compared with the state files. A running sandbox that no state file names as current is terminated. These come from interrupted creates. A sandbox with no heartbeat for 15 minutes is only logged, because its agent may be stuck while an agent works. The 24-hour timeout bounds its cost.
4. **Retries.** A sandbox whose terminate failed after a successful snapshot is terminated again.

The interval is a setting between 1 and 5 minutes. At 1 minute the timer costs a few cents a month.

### 6.5 Failure handling

| Failure | What happens |
|---|---|
| The disk snapshot fails | The controller doesn't terminate. The operation fails. The agent restarts T3, returns to active and tries again after the next idle period. After 3 failures in a row, the desktop app notifies the user |
| Writing the state file fails after a snapshot | The controller doesn't terminate. The image is kept, and the next successful sleep's cleanup removes it |
| Terminate fails after the snapshot and state write | The state already marks the machine asleep. The timer retries terminate every minute. If someone uses the machine meanwhile, the agent sees the sleep didn't finish after `STOP_SETTLE` (5 minutes), restarts T3 and returns to active. The next sleep saves a newer snapshot |
| The controller crashes in the middle of an operation | The lease expires after 2 minutes. The agent times out on the operation and returns to active |
| The controller is unreachable during a sleep | The agent stays up and retries with backoff. Cost continues. If the sandbox nears its 24-hour limit while the controller is still unreachable, the agent writes the database backup and notifies the user through the desktop app. Without the controller, nothing can save the disk, so this is the one case that can lose data. It requires Modal's own API to be down for hours |
| A wake fails from `images[0]` | The controller retries from `images[1]` and flags `restoredFromPrevious`. The desktop app shows it |
| A wake finds a sandbox with the same name running | The controller adopts it and returns its URLs |
| A desktop wake and a timer wake race | The lease orders them. The second one finds the machine running |
| A memory restore fails | The controller falls back to a fresh boot from `images[0]` |

### 6.6 How machines authenticate

Each machine has a secret: an HMAC of its id under the controller's `t3modal-hmac` key. The controller passes it to the sandbox at create in an ephemeral Secret (`modal.Secret.from_dict`). The desktop app gets it once from `POST /machines` and keeps it in the keychain.

The machine secret is used in two places:
- The agent's own API, as `Authorization: Bearer <secret>`, compared in constant time.
- Requests from the agent to the controller, signed as `HMAC(secret, method, path, timestamp, nonce, body)`. The controller rejects timestamps more than 60 seconds off and nonces it has seen in the last 2 minutes.

A machine secret allows actions on that machine only, and only these: sleep, heartbeat, and reading its own operations. It can't wake other machines, read credentials, or delete anything.

---

## 7. Inside the sandbox

### 7.1 Base image

The controller builds the base image, and Modal caches it:

```python
base = (modal.Image.debian_slim(python_version="3.12")
  .apt_install("git", "curl", "ca-certificates", "build-essential", "sqlite3", "ripgrep", "jq",
               "unzip", "openssh-client", "tini", "sudo", "procps", "gh", "socat")
  .run_commands(
     "curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs",  # node:sqlite needs 22.5 or later
     "useradd -m -s /bin/bash t3 && echo 't3 ALL=(ALL) NOPASSWD:ALL' >/etc/sudoers.d/t3",
     f"su - t3 -c 'curl -fsSL https://t3.codes/install.sh | T3CODE_VERSION={t3_version} sh'",
     f"npm i -g @anthropic-ai/claude-code@{claude_version} @openai/codex@{codex_version}")
  .add_local_dir("sandbox/", "/opt/t3modal", copy=True))   # init.sh, agent.mjs, provision.sh
```

- T3 runs as the user `t3`, not root. Claude Code refuses its skip-permissions mode as root.
- Each t3modal release pins the versions of Node, T3, Claude Code and Codex. The image is keyed by T3 version and provisioning revision.
- T3 and the agent CLIs are baked in, so creating a machine doesn't run the T3 installer.

### 7.2 `init.sh`, the supervisor

`init.sh` runs under `tini` as the sandbox's first process. Its steps:

1. If the image carries a database backup marker and this is a fresh boot, it replaces `statev2.sqlite` with the backup (§4.4).
2. It writes `/etc/machine-info` with the machine's name, so T3 Connect and linked environments show it.
3. It exports credentials from the attached Secrets, following the machine's credential modes (§10, §11).
4. It runs two supervised loops as the user `t3`:
   - `t3 serve --host 0.0.0.0 --port 3773`. Modal tunnels connect to the container's interface, not loopback, so T3 must listen on all interfaces. Phase 0 confirms this. The loop doesn't restart T3 while `~/.t3modal/t3.hold` exists, which lets the agent stop T3 for a snapshot or restart it for T3 Connect.
   - `node ~/.t3modal/agent.mjs`, or `/opt/t3modal/agent.mjs` before the first provisioning. It restarts on exit, so an upgraded agent replaces itself without restarting T3.

`init.sh` never exits. If the first process exits, the sandbox ends without a snapshot. If a loop keeps crashing, the script keeps retrying with backoff. The 24-hour timeout bounds the cost.

### 7.3 `agent.mjs`: t3boat's watchdog plus a control API

The agent keeps all of t3boat's idle logic: running agents, actions by a person seen in SQLite, CPU load, keep-awake, `nextDueAt`, the cap on "can't tell" states, and drain with manifest and acknowledgement.

| t3boat | t3modal |
|---|---|
| `boat snapshot self`, drain, `boat stop self` | Drain, then a disk-only or memory sleep (§8.3) through the controller |
| Phases `active`, `saving`, `draining`, `stopping` | `active`, `draining`, `quiescing`, `stopping`. The status JSON keeps its shape, so the Rust side changes little |
| Resume detected by a late tick | The same for memory restores. A fresh boot always resets keep-awake |
| Talks only to the laptop | Also sends a heartbeat to the controller every 60 seconds |
| No lifetime limit | Knows `STARTED_AT` and its timeout from the environment (§8.6) |
| `boat exec` from the laptop | A fixed list of HTTPS operations, listed below |

The agent's operations, all requiring the machine secret:
- Watchdog, unchanged from t3boat: `GET /status`, `POST /activity`, `POST /drain`, `POST /keep-awake`, `POST /sleep-ack`.
- Setup: `POST /provision`, `POST /restart-t3`, `POST /rollover`.
- Agent sign-in (§10): `POST /login/start {agent, method}` returns `{url, code, needsPaste}`. Also `POST /login/input {text}`, `GET /login/status`, `POST /login/cancel`.
- GitHub (§11): `POST /github/login/start` returns `{url, code}`. Also `GET /github/status`.
- T3 Connect: `POST /connect/start` returns `{url}`. Also `GET /connect/status`.

Release builds have no arbitrary command endpoint. Development builds add `POST /exec` behind a build flag.

The agent counts failed authentication attempts. After 20 failures in a minute it refuses all requests for 5 minutes and logs the event.

### 7.4 `provision.sh`

`provision.sh` is a port of t3boat's script without the systemd parts. The agent's `/provision` runs it, and running it twice is safe. It:
- Checks T3's version and reinstalls only during an upgrade.
- Sets `linkedEnvironmentsEnabled` to true in T3's `settings.json`.
- Installs or upgrades the agent under `~/.t3modal/`, then restarts the agent.
- Mints the agent's read-only T3 token once and keeps it in `~/.t3modal/config.json` with mode 0600.
- When asked, mints the proxy's upstream token and returns `{environmentId, upstreamToken}`, as t3boat does. The token goes back once over the agent's HTTPS tunnel and is never logged.
- Restarts T3 through the hold file only when something T3 depends on changed.

---

## 8. Machine lifecycle on the desktop: `machine/mod.rs`

`Power` keeps its values: `Unknown`, `Starting`, `Running`, `Stopping`, `Stopped`, `Missing`. The controller's `GET /machines` and the agent's status replace `boat info` as its source.

### 8.1 Create

1. `POST /machines {name, size, t3Version}`. The UI shows "Preparing image". The first machine on a new T3 version takes 1 to 3 minutes for the image build. Later ones reuse the cached image.
2. Poll the operation until the machine runs and its URLs are known. The UI shows "Starting machine".
3. `POST {agentUrl}/provision {t3Version, name, mintUpstream: true}`, which returns `{environmentId, upstreamToken}`. The UI shows "Setting up T3".
4. Apply credentials and GitHub identity (§10, §11).
5. `POST /machines/{id}/checkpoint`. Until the first sleep, this snapshot is the only saved copy of the machine's identity: its T3 environment id and tokens.
6. Save the record and pair with T3 Code, as t3boat does with `t3code://pair?pairingUrl=...`.

Changes to `store::Machine`:
- `sandbox_id` becomes informational, because it changes at every wake.
- `upstream_url` and `watchdog_url` are refreshed at every wake.
- `machine_secret` replaces `watchdog_secret`. `relay_url`, `image_ids` and `restored_from_previous` are new.
- `pending_sandboxes` is removed. The controller's cleanup covers it.

### 8.2 Wake

`ensure_awake` and `wake_locked` replace `boat resume` and the restore path:

1. `POST /machines/{id}/wake`, then wait for the operation. The controller:
   - takes the lease;
   - restores the memory snapshot if it is valid (§4.4), or else creates a sandbox from `images[0]`;
   - creates it with `name="m-<id>"`, the size, `timeout=24h`, `encrypted_ports=[3773, 3774, 3775]` and the entrypoint `init.sh`;
   - attaches the machine's ephemeral Secret and each `t3modal-<kind>` Secret that exists;
   - tags it `{"t3modal": id}`;
   - stores the tunnel URLs and opens a new entry in `runs`.
2. Update the record with the new URLs.
3. Poll `GET {t3Url}/.well-known/t3/environment` until the environment id matches, using the existing `fetch_descriptor`.
4. Run `upgrade_if_needed`, unchanged: if the T3 version or provisioning revision differs, call `/provision`.
5. `send_activity(resumed=true)`, `flush_deferred_commands()`, `on_ready()`, all unchanged.

t3boat's restore path, keep-alive wakes and the `DataExpired` result are removed. Phase 0 measures wake time. t3boat already accepts a sent message at once and wakes the machine in the background, so a wake of up to about 30 seconds is acceptable.

### 8.3 Sleep, decided on the machine

Disk-only sleep:

```
agent: idle for the sleep delay
  → draining: publish the manifest; the proxy captures and acknowledges
  → quiescing: create t3.hold, stop T3, checkpoint the database, sync
  → POST /sleep {mode: "disk"} to the controller; phase becomes stopping
controller: snapshot_filesystem(ttl=None) → write state and commit → terminate
desktop: sees phase stopping, or the controller reports stopped → Power::Stopping, then Stopped
```

A sleep with memory follows §4.4 and skips stopping T3.

Rules carried over from t3boat:
- Any use during `draining` cancels the sleep.
- Use during `quiescing` restarts T3 and cancels the sleep.
- Use during `stopping` is too late. The proxy waits for `Stopped`, then wakes the machine from the new snapshot.
- "Sleep now" calls the agent's `POST /drain`, unchanged.

t3boat trusts its capture only when the watchdog confirmed the sequence and Boat's `stoppedAt` came after it. t3modal reads the same two facts from the controller's `lastSleep` and `stoppedAt`. The change is in `machine/capture.rs` and `proxy/cache.rs`.

### 8.4 Detecting a sleeping machine in the proxy

`proxy/mod.rs`, near line 380, decides that a request never reached the machine when Boat answers with its redirect page. A terminated Modal sandbox's tunnel answers differently. Phase 0 records the exact responses, and the check is extended to match them. Tunnel URLs also change at every wake. So after any unreached response, the proxy first fetches the current URLs from the controller and wakes the machine only if it isn't running.

### 8.5 Resize

Modal fixes a sandbox's size at create. The policy matches t3boat:
- An asleep machine gets the new size at its next wake.
- An idle machine sleeps with `wakeAfter: true` and wakes at the new size, which takes about a minute. A keep-awake is sent again after the wake.
- A working machine gets the new size after its next sleep.

A pending resize forces a disk-only sleep, because a memory snapshot must restore onto the same size.

### 8.6 The 24-hour limit

At its timeout, Modal terminates a sandbox without a snapshot, so t3modal restarts machines first:
- **From 23 hours,** the next time the machine is idle, the agent sleeps it. If it is kept awake or has work due soon, the sleep uses `wakeAfter: true`. A memory sleep keeps the processes.
- **At 23 hours 40 minutes,** the agent restarts the machine even if an agent is working. The desktop app first notifies "Restarting <name> to stay within Modal's 24-hour limit". Stopping T3 interrupts the running turn, which T3 marks as interrupted, and the user can resume it.
- **The timer** calls `/rollover` on any sandbox older than 23 hours 30 minutes, in case the agent has stopped working.
- **The UI** shows "restarts in X h" on a machine kept awake for long periods.

Phase 0 checks whether a memory restore resets the 24-hour timeout. If it doesn't, rollovers always boot fresh.

### 8.7 Checkpoints while busy

A sandbox that dies unexpectedly loses everything since the last sleep. While a machine has been busy for more than 2 hours, the desktop app or the timer calls `/checkpoint` every 2 hours:
1. The agent writes the database backup and marker.
2. The controller takes a disk snapshot without terminating.

That image is not quiesced, so a fresh boot from it restores the database from the backup. Checkpoints count toward the two-image limit. The newest checkpoint replaces the older image, never the last sleep image.

### 8.8 Delete

The desktop app keeps t3boat's guard: if an agent is working, it offers "Delete anyway". Then it calls `DELETE /machines/{id}`, removes the machine's cache folder and keeps its port retired. Spending from deleted machines stays in the month's totals.

### 8.9 Polling

- `GET /machines` every 60 seconds, and every 5 seconds while any machine is changing state.
- The agent's `/status` every 5 seconds while a machine runs, unchanged.

---

## 9. Proxy, capture, projects and links

| Module | Change |
|---|---|
| `proxy/*` | The unreached check (§8.4), refreshed URLs after wakes, the early wake (§4.4), and renames. Everything else stays: `directEndpoints` stripping, the auth server, recordings, `media.rs`, `link.rs` |
| `machine/capture.rs` | Capture freshness comes from the controller (§8.3) |
| `machine/link.rs`, `proxy/link.rs` | None. Linked environments still go through the proxy |
| `machine/projects.rs`, `projects.rs` | `explain_clone_failure` names the machine's GitHub mode and how to fix access (§11) |
| `machine/connect.rs` | Uses the agent's `/connect/*`. `t3 up --connect --headless --yes` expects to restart T3 under a service manager. The agent runs the setup step, then restarts T3 through the hold file. Phase 0 confirms how `t3 up` behaves without systemd |
| `machine/work.rs` | None |
| `t3desktop.rs` | The app data folder becomes `dev.t3modal.app`, and T3's connection label becomes "t3modal" |

---

## 10. Agent sign-in: Claude and Codex subscriptions

### 10.1 How t3boat does it

`machine/signin.rs` uses six steps:

1. T3 reports each agent's sign-in status in its server config. The proxy records it. An agent marked "unauthenticated" gets a "Sign in" button on that machine.
2. t3boat wakes the machine and starts `claude auth login` or `codex login` there with `boat exec`. The login runs inside `script` so it has a terminal. `$BROWSER` points to a small script that writes the sign-in URL to a file.
3. t3boat reads the URL. Its `redirect_uri` parameter names a localhost port on the machine. Claude picks a random port, such as 43327. Codex uses 1455.
4. `boat forward` connects that port on the laptop to the same port on the machine.
5. t3boat opens the URL in the laptop's browser. After the user approves, the browser opens `localhost:<port>/callback?code=...`, and the forward carries it to the login on the machine.
6. Every 3 seconds, for up to 10 minutes, t3boat runs `claude auth status` or `codex login status` on the machine. The machine stays awake meanwhile. On success, t3boat calls T3's `server.refreshProviders`.

The credential is created on the machine and stays there. The laptop's own logins are never touched. Each machine signs in separately. Claude also offers a manual URL whose `redirect_uri` is a page on platform.claude.com that shows a code. t3boat's tests include that URL, and t3boat rejects it as "no local callback".

### 10.2 What doesn't work on Modal

1. Modal has no port forwarding, so step 4 fails. Tunnels give ports public URLs, but each port must be chosen at create, and Claude's port is random.
2. Modal has no `exec` command line, so steps 2 and 6 need another route.
3. A login saved on a machine survives only through snapshots. A wake that falls back to an older image loses a recent sign-in.

### 10.3 Design

Every account has a mode. In mode "all machines", a credential is stored once in a Modal Secret and attached to every sandbox at every wake. In mode "this machine", the sign-in runs on the machine, and the credential lives on its disk and in its snapshots.

Steps 1 and 6 stay as t3boat wrote them, except that the agent's `/login/status` replaces `boat exec`. The agent's `/login/start` replaces step 2 with the same `script` and `$BROWSER` setup.

**Claude Code**

| Method | Mode | How it works | Notes |
|---|---|---|---|
| `claude setup-token` on the laptop | All machines, **default** | t3modal runs it on the laptop, where the localhost callback works. Claude Code prints a token for the user's subscription, valid about a year. t3modal sends it to `PUT /credentials/claude` as `CLAUDE_CODE_OAUTH_TOKEN` | Needs Claude Code on the laptop. Phase 0 checks that T3 shows Claude as signed in with only this variable. If not, `init.sh` writes the token where Claude Code reads its login |
| Code paste | This machine | The agent starts `claude auth login`. The browser shows a code after approval. The user pastes it into t3modal, and the agent types it into the waiting login with `/login/input` | Phase 0 checks that Claude offers the manual URL when nothing answers on its localhost port |
| Relay | This machine | Fallback for any localhost callback (§10.4) | Works with any login of this kind |
| Anthropic API key | All machines | Stored as `ANTHROPIC_API_KEY` | Billed per use, not through the subscription |

Copying the laptop's Claude login is rejected. On macOS, Claude Code stores it in the Keychain, and two devices sharing one refresh token can sign each other out.

**Codex**

| Method | Mode | How it works | Notes |
|---|---|---|---|
| Device code | This machine, **default** | The agent runs `codex login --device-auth`. t3modal shows the code and opens the page | Phase 0 confirms the flag name and whether ChatGPT requires turning on device-code sign-in in its security settings |
| Copy `~/.codex/auth.json` | All machines | t3modal sends the laptop's file to `PUT /credentials/codex`. A machine writes it only at first boot | OpenAI documents copying this file. Codex refreshes the token and saves it back to the file, so writing it again at every wake would restore an older token. Laptop and machines share one refresh token. Phase 0 tests whether one signs the other out |
| Relay | This machine | Fallback, callback port 1455 | |
| OpenAI API key | All machines | Stored as `OPENAI_API_KEY` | Billed per use |

### 10.4 The relay

Every sandbox opens port 3775 as a tunnel at create. During a sign-in:

1. The agent reads the port from the login URL's `redirect_uri` and allows only that port, for 10 minutes.
2. The desktop app listens on the laptop at the same port. It checks that the port is free, as t3boat's `boat.forward` does.
3. The browser opens `localhost:<port>/callback?code=...`. The desktop app forwards it to `https://<relay tunnel>/<port>/callback?code=...` with the machine secret.
4. The agent passes it to `127.0.0.1:<port>` on the machine.

A sign-in callback is one browser request, so forwarding plain HTTP requests is enough. Outside an active sign-in, the relay answers 404 to every request.

### 10.5 Credential modes on the machine

At each boot, `init.sh` decides what to export:
- **All machines.** It exports the Secret's variables into T3's environment.
- **This machine.** It doesn't export them. The machine's own login on disk applies.
- **Codex in either mode.** `init.sh` writes `~/.codex/auth.json` only if the file doesn't exist.

After any change, the agent asks T3 to refresh its providers, and T3 Code stops showing the sign-in prompt.

---

## 11. GitHub access

### 11.1 How t3boat does it

t3boat runs no GitHub setup itself. Boat signs every sandbox in to GitHub with the account linked to the user's Boat account. It sets up `gh` as git's credential helper and renews the login at each resume. T3's clones and pushes use it.

Every machine has the same GitHub account. When a clone fails with an authentication error, `explain_clone_failure` adds that the machine uses the Boat account's GitHub login, which can't reach this repo. The user can sign in on that machine through T3 Code's source control settings.

### 11.2 What doesn't work on Modal

Modal does nothing with GitHub. A new machine can't clone a private repo, push, or open a pull request with `gh`. It also has no commit name or email.

### 11.3 Design

| Method | Mode | How it works | Access | Notes |
|---|---|---|---|---|
| Fine-grained personal access token | All machines, **recommended** | The user creates a token on GitHub for chosen repos and pastes it into t3modal. It goes to `PUT /credentials/github` as `GH_TOKEN` | Only the chosen repos and permissions | Expires within a year. GitHub reports the expiry date in each API response, and t3modal warns 7 days ahead. Some organizations must approve the token |
| This computer's `gh` login | All machines | With the user's consent, t3modal runs `gh auth token` on the laptop and stores the result the same way | Usually every repo the user can reach | One click. Revoking it on GitHub also signs out the laptop's `gh` |
| Device code on the machine | This machine | The agent runs `gh auth login --web --git-protocol https`. t3modal shows the code and opens github.com/login/device | What the user approves | For a different GitHub account on one machine. Stored on disk and kept by snapshots |
| T3's source control sign-in | This machine | T3 Code's settings for that machine | T3 decides | t3modal only links to it |

The fine-grained token needs these permissions:
- Contents: read and write
- Pull requests: read and write
- Metadata: read
- Workflows: read and write, only if agents edit `.github/workflows`
- Actions and Commit statuses: read, for CI status in T3's pull request views

At every boot, `init.sh` runs `gh auth setup-git` as `t3`, so git asks `gh` for credentials. In "all machines" mode it exports `GH_TOKEN`. In "this machine" mode it doesn't, because `GH_TOKEN` overrides a stored `gh` login.

At create, t3modal sets the commit name and email. By default they come from the GitHub profile, with the noreply address `<id>+<login>@users.noreply.github.com`. The user can pick the laptop's `git config --global` values instead.

The first machine creation asks once: "Give machines access to GitHub?" It recommends the fine-grained token and offers the one-click option.

Two methods are rejected:
- **A GitHub App.** It needs a server holding the app's private key, so t3modal would need a backend, or every user would have to create their own app.
- **SSH deploy keys.** Each key covers one repo, and a key can't open pull requests.

Commit signing is out of scope for v1.

---

## 12. Sizes and costs

Modal's sandbox rates are $0.00003942 per CPU core per second and $0.00000667 per GiB of memory per second. Modal bills the larger of the request and the actual use.

Phase 0 picks one of two sizing modes.

**Mode A, fixed requests.** Use this if a sandbox can't reliably use more than it requests.

| Size | Request | About $ per awake hour | About $ per month at 2 hours a day |
|---|---|---|---|
| small | 1 core, 4 GiB | 0.24 | 14 |
| default | 2 cores, 8 GiB | 0.48 | 29 |
| large | 4 cores, 16 GiB | 0.95 | 57 |

**Mode B, small requests with limits.** Use this if Phase 0 confirms `(request, limit)` pairs and CPU use above the request. Every size requests 0.25 cores and 1 GiB. The limits are 2, 4 or 8 cores and 4, 8 or 16 GiB. An agent spends most of a turn waiting for the model's API. A machine averaging 0.3 cores and 2 GiB in use costs about $0.09 an hour, and an idle machine about $0.06 an hour.

Boat's sizes cost $0.018 to $0.072 an hour. In mode A, Modal costs about 13 times more per awake hour. In mode B, typical agent work costs about 2 to 5 times more. The size picker shows hourly prices in both modes.

The table assumes a Modal core equals 2 virtual CPUs, matching Boat's sizes. Phase 0 confirms it. If a core is 1 virtual CPU, matching Boat's sizes takes twice the cores.

Costs come from the controller's `runs` records multiplied by the rates. Phase 0 also tries `App.sandboxes.stats()` grouped by the `t3modal` tag, and uses it if it reports billed usage. Snapshot storage cost is unknown. Phase 0 measures it, and the UI shows it if it matters. The "starts today" counter is removed from `UiState`.

---

## 13. Setup flow

The setup states become `starting`, `installModal`, `signInModal`, `deployController`, `installT3` and `ready`.

1. **installModal.** Create `~/.t3modal/venv` and install a pinned `modal` version with hash checking: `uv pip install --require-hashes -r requirements.lock`, or `python3 -m venv` and pip if `uv` is missing. If neither Python nor `uv` exists, show install instructions. The app never uses a global `modal`, so the SDK version stays pinned.
2. **signInModal.** If `modal profile current` succeeds, skip this step. Otherwise run `modal token new` from the managed environment. It opens the browser and finishes by itself, and the UI shows its URL as t3boat does for Boat. The user picks a Modal environment, `main` by default.
3. **deployController.** Create the Secrets and the Volume if absent, deploy the controller, read its URL from the deploy output or `modal app list --json`, check `GET /version`, and store the URL and admin token.
4. **installT3.** Unchanged. T3 Code's desktop app must be installed.
5. **Accounts for machines.** Optional at setup and available later in Settings: GitHub (§11), Claude and Codex (§10).

`UiState.account` shows the Modal workspace name.

---

## 14. App, MCP and UI

- **Renames.** The crate becomes `t3modal`, the bundle id `dev.t3modal.app`, the dev binary `bin/t3modal-dev.rs`, and the MCP server `t3modal`. Tool names and behaviour stay the same (`create_machine`, `add_project`, `wake_machine` and the rest), so agents' prompts keep working. The MCP instructions describe Modal Sandboxes.
- **UI** (`apps/desktop/src`):
  - New setup screens: Python environment, Modal sign-in, controller deploy.
  - An "Accounts for machines" page in Settings: GitHub, Claude and Codex, each with a mode.
  - Size picker with Modal prices.
  - Machine details: remove "starts today". Add "restarts in X h" when kept awake, "restored from previous snapshot", and the GitHub account in use.
  - A new pixel scene and tray icon. t3boat's boat and ocean are MIT-licensed, but a separate product should look separate.
  - Machine states stay as they are.
- **Website** (`apps/web`): not part of v1.
- **`mock.ts`**: updated, so the UI can be developed without a Modal account.

---

## 15. Security model

### 15.1 What is exposed, and how it is protected

| Exposed thing | Who can reach it | Protection |
|---|---|---|
| T3 tunnel, port 3773 | Anyone with the URL | T3's own pairing and bearer tokens, as with Boat. Phase 0 tests Modal connect tokens (`sb.create_connect_token`, sent as the `_modal_connect_token` query parameter) as a second check |
| Agent tunnel, port 3774 | Anyone with the URL | The machine secret, 256 bits, compared in constant time. A fixed list of operations. A lockout after repeated failures. No arbitrary commands in release builds |
| Relay tunnel, port 3775 | Anyone with the URL | Closed outside a sign-in. Requires the machine secret. Forwards only to the active login's port, for 10 minutes |
| Controller API | Anyone with the URL | The admin token for desktop calls. Signed requests with timestamp and nonce for machine calls. A machine secret works only for that machine's sleep, heartbeat and operations |
| Laptop storage | The local user | The admin token, machine secrets and upstream T3 tokens go in the OS keychain through the `keyring` crate: Keychain on macOS, Credential Manager on Windows, Secret Service on Linux. Where no keychain exists, t3boat's 0600 files. `~/.modal.toml` belongs to Modal's CLI |
| Local MCP server | Programs on the laptop | Unchanged from t3boat: Host and Origin checks keep web pages out |
| Snapshots | The user's Modal workspace | They contain repos and on-disk credentials. Deleting a machine deletes its images and memory snapshot |

### 15.2 Code running on a machine

Coding agents on a machine, possibly misled by text in a repo, can read everything on that machine. That includes the Secrets in its environment, the GitHub token, the Claude token, the machine secret and its T3 tokens. Agents need these credentials to work, so this risk can't be removed. t3modal limits what a leaked credential can do:

- **GitHub.** The recommended fine-grained token reaches only the chosen repos.
- **Other machines.** The machine secret works only for that machine. Users who want machines isolated from each other use "this machine" mode for every account.
- **Billing.** The machine secret can't wake machines. The controller limits `wakeAfter` to one per 10 minutes per machine, and due wakes from `nextDueAt` to one per 15 minutes per machine. A machine can therefore keep itself billing for at most its 24-hour lifetime plus rate-limited restarts. The desktop app alerts when one machine's spending today exceeds a threshold the user sets.
- **Workspace.** The admin token and the Modal login never reach a machine.
- **API keys.** If the user stores API keys, t3modal recommends spending limits at the provider.

Inside a machine, the user `t3` has passwordless sudo, as on Boat. The sandbox is the security boundary, not the user account.

### 15.3 Supply chain

- The controller source and sandbox scripts are compiled into the desktop app, so they don't change without an app update.
- The `modal` package is pinned and checked against hashes.
- The base image pins Node, T3, Claude Code and Codex versions per t3modal release.
- Tokens never appear on command lines or in logs. They travel in environment variables or HTTPS bodies, as t3boat's `provision.rs` requires. The agent removes anything that looks like a token from its log lines.

---

## 16. Work plan

### Phase 0: experiments, 3 to 4 days

Run throwaway Python scripts against a real Modal workspace to answer every question in §18. Write the results to a short findings document, and update §4.4, §10, §11 and §12 to match. Decide the sizing mode (§12) and whether memory snapshots ship on by default.

### Phase 1: fork and seam, 2 to 3 days

- Import t3boat at a pinned commit: `apps/desktop`, `package.json` and the workspace config. Keep its license notice. Add attribution.
- Rename the crate, bundle id, strings and data folder.
- Add the `Cloud` trait and `FakeCloud`. Route `machine/*`, `app.rs` and `tray.rs` through the trait. Delete `boat.rs`.
- Done when `cargo test` passes with `FakeCloud` and the UI runs on `mock.ts`.

### Phase 2: inside the sandbox, 5 to 6 days

- `modal/sandbox/init.sh`, `agent.mjs` and `provision.sh`. The agent covers the watchdog, the control API, quiesce, database backup, heartbeat, rollover, sign-in sessions and command recording.
- The base image definition (§7.1).
- Done when, in a sandbox created by hand:
  - T3 answers through its tunnel.
  - The agent reports idle and busy correctly.
  - A database restored from the backup passes `PRAGMA integrity_check`.
  - Code paste and device-code sign-ins finish.

### Phase 3: controller, 5 to 6 days

- `modal/controller/app.py`: the API (§6.3), operations, state and lease, disk and memory sleeps, wake fallbacks, image cleanup, the timer, credentials, usage, rate limits and signed requests.
- Tests: `pytest` on pure logic, and an opt-in real-Modal suite (`T3MODAL_IT=1`) that runs create, sleep, wake and delete.
- Done when a full cycle runs through curl alone, including a timer-triggered due wake, a memory sleep and restore, and cleanup of an orphaned sandbox.

### Phase 4: desktop integration, 5 to 7 days

- `modal.rs`: the controller client with operation polling, typed errors and the `Cloud` implementation.
- Setup (§13), including redeploys when the controller version changes.
- Lifecycle (§8): create with checkpoint, wake, sleep, resize, rollover notices, checkpoints while busy, delete. Remove the restore and keep-alive code.
- Proxy: the unreached check, URL refresh, capture freshness and early wakes.
- Keychain storage.
- Done when a user can do this from the tray: create a machine, pair it with T3 Code, chat, close the laptop in the middle of a turn, and reopen it. The turn has finished, the machine has slept, and opening the chat serves it from the capture without waking the machine.

### Phase 5: features, 4 to 5 days

Agent sign-in (§10), GitHub access (§11), T3 Connect, a check of projects and `.env` files, costs, MCP text, UI changes and the spending alert.

Done when every command in `docs/ui-contract.md` works against Modal and the contract document is updated.

### Phase 6: hardening and docs, 3 to 4 days

- Failure injection for every row in §6.5.
- A test build that forces rollover at 30 minutes.
- Windows and Linux builds.
- Rewrite the README, architecture and protocol documents for Modal, with a cost warning in the README.

The total is about 5 to 6 weeks for one engineer. Phases 2 and 3 can run in parallel.

---

## 17. Testing

- **Rust unit tests.** t3boat's proxy and classification tests carry over. New tests run the lifecycle state machine against `FakeCloud`: racing wakes, a sleep during a wake, use during `quiescing` and `stopping`, capture freshness, and each failure in §6.5.
- **Agent tests.** `node --test` with a fake T3 (an HTTP shell endpoint and a SQLite fixture) and a fake controller. They cover idle decisions, drain and acknowledgement, quiesce failures, backup and restore, rollover timing with an injected clock, the authentication lockout and relay port checks.
- **Controller tests.** `pytest` for state transitions, cost math, due-wake selection, rate limits and request signing. The real-Modal suite is opt-in. On teardown it terminates everything tagged `t3modal-it`.
- **Security tests.** Every agent and controller endpoint rejects missing, wrong, replayed and other-machine credentials. The relay rejects ports outside the active sign-in.
- **Manual acceptance checklist:**
  - A turn finishes with the laptop closed.
  - A scheduled automation runs while the desktop app is off.
  - A phone reaches a machine through T3 Connect.
  - A private repo clones.
  - Claude and Codex sign in, in both modes.
  - Resize works while idle and while working.
  - Delete works while working.
  - A 24-hour rollover completes.
  - A memory restore keeps a running dev server.

---

## 18. Questions for Phase 0

| # | Question | What depends on it | If the answer is bad |
|---|---|---|---|
| 1 | How long does `snapshot_filesystem` take with 5 to 20 GB of changes? | Sleep duration and snapshot timeout | Clean caches before sleeping, or switch to directory snapshots of `/home` |
| 2 | How long does a wake take, from create to T3 answering, for a fresh boot and for a memory restore? | How long a user waits | Rely more on the idle stage and early wakes |
| 3 | Does each snapshot stack on the previous one, and do long chains slow down or hit a limit? Test 50 generations | Machines used for months | Periodically rebuild from a directory snapshot on a fresh base |
| 4 | What do snapshots cost to store with `ttl=None`? | Hidden costs | Fewer images, shown in costs |
| 5 | Do memory snapshots work with T3, agents and dev servers? Does a disk snapshot work on a `runtime="vm"` sandbox? Does a memory restore reset the 24-hour timeout? | §4.4 | Ship disk-only sleep |
| 6 | Does `Sandbox.create` accept `(request, limit)` for CPU and memory, and does a sandbox reliably get CPU above its request? Is a Modal core 2 virtual CPUs? | Sizing mode, idle stage | Sizing mode A, idle stage off |
| 7 | Must T3 listen on `0.0.0.0` for tunnels? Do WebSockets with permessage-deflate and long-lived sockets work through `*.modal.host`? Do idle connections time out? | The proxy's upstream connection | The proxy already reconnects. Add keepalive pings |
| 8 | What does a terminated sandbox's tunnel URL answer? | The unreached check | Always ask the controller before waking |
| 9 | Does gVisor support `/proc/stat` CPU accounting, `node:sqlite`, `script` with a terminal, `sudo` and Docker? | Watchdog, sign-in, non-goals | Read CPU from cgroup `cpu.stat`. Document that Docker is unavailable |
| 10 | How do `t3 serve` and `t3 up --connect` behave without systemd? | Startup script, T3 Connect | Restart through the hold file |
| 11 | How long is a web endpoint's cold start, and does the 150-second request limit apply as expected? | Wake latency, operations | Keep one container warm during the day, at a small cost |
| 12 | Does `App.sandboxes.stats()` report billed usage per tag? | Cost accuracy | Compute from runtime |
| 13 | Does T3 show Claude as signed in with only `CLAUDE_CODE_OAUTH_TOKEN` set? | Claude in "all machines" mode | Write the token to Claude Code's credentials file at boot |
| 14 | Does `claude auth login` offer the manual code page when nothing answers on its localhost port? | Claude code paste | Use the relay |
| 15 | What is Codex's device-code flag, and does ChatGPT require turning on device-code sign-in? | Codex default | Use the relay, and tell the user which setting to turn on |
| 16 | Does a copied `auth.json` sign out the laptop or other machines when one copy refreshes? | Codex in "all machines" mode | Drop that option |
| 17 | Do Modal connect tokens work as a query parameter on HTTP and WebSocket requests without conflicting with T3's `Authorization` header? | Extra protection on the T3 tunnel | Rely on T3's auth alone, as t3boat does |
| 18 | What are the Starter plan's credits and sandbox concurrency limits? | README and cost text | Document the limits |

---

## 19. Decisions for the project owner

Defaults are in bold.

1. Long-term persistence: **snapshots of the whole disk**, or directory snapshots of `/home` (§4.1).
2. Memory snapshots: **on by default if Phase 0 passes**, or off until they leave alpha (§4.4).
3. Sizing: **mode B if Phase 0 confirms it**, otherwise mode A (§12).
4. Idle stage before sleep: **1 hour in mode B**, 0 in mode A (§4.4).
5. A controller app deployed in the user's workspace: **yes**. Sleeping with the laptop closed and scheduled wakes need it.
6. GitHub default: **fine-grained token for all machines**, with one-click copy of the laptop's `gh` login as the alternative (§11).
7. Claude default: **`claude setup-token` for all machines** (§10).
8. Codex default: **device code per machine** (§10).
9. Restart recorded commands after a fresh boot: **on** (§4.4).
10. Timer interval: **1 minute** (§6.4).
11. Marketing website: **not in v1**.
12. Fork t3boat's code with attribution: **yes**, or rewrite from scratch.

---

## 20. Repository layout

```
t3-modal/
├─ implementation.md            # this file
├─ README.md, LICENSE           # MIT, including t3boat's notice
├─ package.json                 # bun workspaces
├─ apps/desktop/                # from t3boat; Tauri and React
│  ├─ docs/                     # architecture, t3-protocol, ui-contract, updated for Modal
│  ├─ src/                      # UI
│  └─ src-tauri/src/
│     ├─ cloud.rs               # Cloud trait and FakeCloud
│     ├─ modal.rs               # controller client, replaces boat.rs
│     ├─ setup.rs               # Python environment, Modal sign-in, controller deploy
│     ├─ credentials.rs         # accounts for machines: GitHub, Claude, Codex
│     ├─ secrets.rs             # OS keychain storage
│     ├─ machine/               # mod, provision, signin, relay, github, connect, capture, link, projects, work
│     ├─ proxy/                 # unchanged except §8.4 and early wakes
│     └─ app.rs, tray.rs, store.rs, mcp.rs, t3desktop.rs, projects.rs
└─ modal/
   ├─ controller/app.py         # the deployed Modal app: API and timer
   ├─ controller/requirements.lock
   ├─ controller/tests/
   └─ sandbox/                  # init.sh, agent.mjs, provision.sh; baked into the base image
```
