# Phase 0 research

This file answers the reading half of the Phase 0 questions in `implementation.md` §18. It was written on 2026-11-18.

No code ran against Modal, no account was signed in to, and no money was spent. The sources are:

- Modal's docs at modal.com/docs, read through the Markdown copies listed in https://modal.com/llms.txt, and the pricing page at https://modal.com/pricing.
- The Modal Python SDK 1.6.4 wheel from PyPI, which is the latest release. It was unpacked and read, not run. SDK paths below are relative to the `modal/` package.
- T3 Code at commit `f3b168b9f661eb464ad47d986dc16813c622f8e2`, dated 2026-11-18. T3 paths below are relative to the repo root.
- Claude Code's docs at code.claude.com.
- Google's Antigravity docs at antigravity.google.
- The npm registry.

Questions 1, 2 and 7 need measurements only, so they are not covered here. For the other questions, "Still to measure" lists what reading couldn't settle.

## Summary

| # | Short answer | Confidence | Experiment still needed |
|---|---|---|---|
| 3 | Filesystem snapshots store only the changes from their base image. A snapshot of a sandbox started from an earlier snapshot therefore probably adds a layer on top of it. No limit on layers or chain length is documented. | Inferred | Yes, 50 generations |
| 4 | `ttl=None` keeps an image with no expiry, and the default TTL is 30 days. Modal publishes no price for snapshot or image storage. `modal.experimental.image_delete(image_id)` deletes one image but not its intermediate layers. | Confirmed by source, except the price, which is unknown | Yes, storage cost |
| 5 | Create with `_experimental_enable_snapshot=True`, snapshot with `sb._experimental_snapshot()`, and restore with `modal.Sandbox._experimental_from_snapshot()`. Memory snapshots expire after 7 days, and a chain keeps the first snapshot's expiry. The docs recommend `runtime="vm"` for them. Neither source says whether `snapshot_filesystem` works on `runtime="vm"` or whether a restore resets the 24-hour clock. | Confirmed for the API names and expiry. Unknown for the VM filesystem snapshot and the timeout | Yes |
| 6 | Yes, `cpu` and `memory` take `(request, limit)` tuples. One Modal core is 2 vCPUs. Billing is the larger of request and use. Sandboxes cost $0.00003942 per core-second and $0.00000667 per GiB-second. | Confirmed by source | Yes, CPU above request |
| 8 | Not documented. The docs only say tunnel URLs are random but public. | Unknown | Yes |
| 9 | Docker is documented on `runtime="vm"`, and `/var/lib/docker` is kept in filesystem snapshots. Nothing is documented about `/proc/stat`, `node:sqlite`, `script` or `sudo` on gVisor. | Confirmed for Docker. Unknown for the rest | Yes |
| 10 | `t3 serve` is T3's headless mode. It needs both `--host 0.0.0.0` and `--port 3773`, because without them T3 binds to no fixed host and picks the first free port from 3773. State lives in `~/.t3/userdata/`. systemd is optional. | Confirmed by source | Yes, a boot test |
| 11 | Every web endpoint has a 150-second request limit, after which Modal answers with a 303 redirect. A container boots in about 1 second, before app imports. `scaledown_window` defaults to 60 seconds and accepts 2 seconds to 20 minutes. | Confirmed by source | Yes, the FastAPI cold start |
| 12 | Yes. `App.sandboxes.stats(since, group_by_tags=...)` returns cost per tag over at most 7 days. `Sandbox._experimental_stats()` returns cost and billed core-seconds per sandbox. Neither includes credits. | Confirmed by source | Yes, compare with the bill |
| 13 | `claude setup-token` prints a 1-year token for `CLAUDE_CODE_OAUTH_TOKEN`. T3 counts Claude as signed in when Claude reports any token source other than "none", so the env var alone probably counts. The login page offers a "Paste code here if prompted" step. | Confirmed for Claude Code. Inferred for T3 | Yes |
| 14 | Google publishes a Linux Antigravity CLI. T3 downloads and manages its own copy, including `linux_amd64`. On a remote machine, T3's sign-in finishes when the user pastes the `127.0.0.1` redirect URL. T3 stores the token in a file under `~/.t3/userdata/`, not the keyring. | Confirmed by source | Yes, survival across a sleep |
| 15 | On connect, T3 Code makes 2 HTTP calls, opens `/ws`, and subscribes to `subscribeServerConfig`, then calls `server.probe` and subscribes to `orchestration.subscribeShell`. `@t3tools/contracts` is private and not on npm. | Confirmed for the calls named. Inferred for the full list | Yes, record a session |
| 16 | A connect token can go in the `Authorization` header, a `_modal_connect_token` query parameter, or a cookie. It protects a separate connect-token URL, not an `encrypted_ports` tunnel. T3 already uses `Authorization: Bearer` for HTTP and a `wsTicket` query parameter for its WebSocket. | Confirmed by source | Yes |
| 17 | Starter costs $0 a month plus usage, includes $30 of credits a month, and allows 100 containers at once. It also allows 5 deployed crons, 200 apps and 1 day of logs. $30 buys about 79 hours of a 2-core, 4 GiB sandbox or about 504 hours of standby. | Confirmed by source, except that the container limit applies to sandboxes, which is inferred | No |

## 3. Snapshot layers and chains

Reading suggests that each filesystem snapshot adds a layer on the previous one, but Modal doesn't say so directly. Modal documents no limit on layers or on chain length.

Evidence:

- https://modal.com/docs/guide/sandbox-snapshots says filesystem snapshots "are calculated as the difference from your base image, so only modified files are stored". When a sandbox starts from snapshot N, snapshot N is its base image. Snapshot N+1 would then store only the changes since N, which makes a chain. This step is an inference.
- `experimental/__init__.py:311-339` in SDK 1.6.4 says of `image_delete`: "Deleting an Image will not delete any of its intermediate layers." That also suggests images are built from shared layers.
- Searches of the same page, the Sandbox SDK reference and the SDK source found no maximum depth.

Still to measure: the time and wake speed across 50 generations, and whether deleting old images frees anything while newer images still build on them.

## 4. Snapshot storage cost and `image_delete`

The default TTL is 30 days, and `ttl=None` keeps a snapshot with no expiry. Modal publishes no storage price for snapshots or images. Old images are removed one at a time with `modal.experimental.image_delete(image_id)`.

Evidence:

- `sandbox/_sandbox.py:850-871` defines `snapshot_filesystem(timeout=55, *, ttl=30*24*3600)` and says "Pass `ttl=None` to retain the image indefinitely."
- `sandbox/_common.py:135-140` turns `None` into a no-expiry sentinel on the wire.
- https://modal.com/docs/guide/sandbox-snapshots has this "Breaking change in v1.5": "Filesystem Snapshots now default to a 30-day TTL. Previously, Filesystem Snapshots persisted indefinitely."
- The same page says "If you try to use an expired snapshot, Modal will raise a `NotFoundError`."
- The same page says "there is currently no API to list all snapshots you have created." The controller must therefore record every image ID itself, which the plan already does.
- `experimental/__init__.py:311-339` defines `image_delete(image_id, *, client=None)`. It is labelled "an experimental interface", and its note says "Deleting an Image will not delete any of its intermediate layers."
- https://modal.com/pricing lists compute, Volumes at $0.09 per GiB-month with 1 TiB free, and egress. It lists no price for images or snapshots.

Still to measure: whether snapshot storage shows up on the bill or in `App.sandboxes.stats()` cost at all, and whether `image_delete` changes that.

## 5. Memory snapshots

The API names in §4.4 are correct. Memory snapshots are alpha. They expire 7 days after the first snapshot in a chain, and taking one ends the sandbox. Neither the docs nor the SDK settle whether `snapshot_filesystem` works on `runtime="vm"` or whether a restore starts a new 24-hour clock.

Evidence:

- https://modal.com/docs/guide/sandbox-snapshots shows `modal.Sandbox.create(runtime="vm", _experimental_enable_snapshot=True)`, then `sb._experimental_snapshot()`, then `modal.Sandbox._experimental_from_snapshot(snapshot)`. It also shows `modal.SandboxSnapshot.from_id(snapshot_id)`.
- The SDK exports `SandboxSnapshot` at `__init__.py:47`. `sandbox/_sandbox.py:1281` defines `_experimental_snapshot` and `:1286-1319` defines `_experimental_from_snapshot(snapshot, client=None, *, name=...)`.
- The same docs page says "Memory snapshots are supported on both runtimes, and we recommend using memory snapshots with `runtime="vm"`."
- It also says "Memory Snapshots expire 7 days after creation and cannot currently be extended."
- On chains: "the new snapshot inherits the expiration date of the original snapshot. This means a "chain" of snapshotted state can only ever become as old as the expiration date of the first snapshot in the series." This supports tracking `memoryChainStartedAt`.
- The page lists these limits: "Snapshotting a Sandbox will currently cause it to terminate." A restore needs "the same exact instance type", "this can sometimes lead to scheduling delays", and the sandbox "cannot pin its region". The page also says TCP connections close and a snapshot can't be taken while an exec runs.
- `sandbox/_impl_v1.py:194-226` sends a `SandboxRestoreRequest` with only a snapshot ID, a name override and a worker ID. It has no timeout field, so a restore can't set a new timeout. Whether the server keeps the old deadline or starts a new one is not stated.
- `sandbox/_impl_v1.py:250-267` waits 55 seconds for the snapshot to finish.
- https://modal.com/docs/guide/sandbox-resources says the VM runtime root filesystem is a fixed 512 GiB. https://modal.com/docs/guide/sandboxes says `/var/lib/docker` on the VM runtime "is included in Filesystem Snapshots", which implies filesystem snapshots work there. This is an inference.

Still to measure: T3, agents and dev servers across a memory restore. Whether `snapshot_filesystem` returns a usable image on `runtime="vm"`. Whether a restored sandbox still ends 24 hours after the original create.

## 6. Resources and billing

`Sandbox.create` accepts `(request, limit)` tuples for both CPU and memory. One Modal core is a physical core, which equals 2 vCPUs. Modal bills the larger of request and use. Current sandbox rates match §13 of the plan.

Evidence:

- `sandbox/_sandbox.py:181-221` types the parameters as `cpu: float | tuple[float, float] | None` and `memory: int | tuple[int, int] | None`.
- Its docstring at `:242-248` says to "pass (request, limit) to additionally specify a hard limit in fractional CPU cores. CPU throttling will prevent a container from exceeding its specified limit." Memory is in MiB.
- `_resources.py:10-52` maps a tuple to `milli_cpu` and `milli_cpu_max`, or to `memory_mb` and `memory_mb_max`, and rejects a limit below the request.
- https://modal.com/docs/guide/sandbox-resources gives the example `cpu=(0.5, 4.0), memory=(512, 2048)`. It says the "`cpu` parameter specifies physical CPU cores (1 core = 2 vCPUs)". It says the default request is 0.125 cores and 128 MiB.
- https://modal.com/docs/guide/resources says the default CPU limit is the request plus 16 cores and that billing uses whichever is higher, request or usage.
- https://modal.com/pricing says sandboxes are "billed at non-preemptible rates of 3x list price". It lists CPU at $0.00003942 per core per second for a "Physical core (2 vCPU equivalent)", with a 0.125-core minimum. It lists memory at $0.00000667 per GiB per second, with a 128 MiB minimum.
- At those rates, 1 core costs $0.1419 an hour and 1 GiB costs $0.0240 an hour. Standby at 0.25 cores and 1 GiB therefore costs $0.0355 plus $0.0240, which is $0.0595 an hour. That matches §4.4.

Still to measure: whether a sandbox reliably gets CPU above its request, and how often it is throttled.

## 8. Tunnel URLs after a sandbox ends

Modal doesn't document what a tunnel URL answers once its sandbox has ended.

Evidence:

- https://modal.com/docs/guide/tunnels says "URLs are cryptographically random, but they are also public". It says nothing about requests after the sandbox ends.
- https://modal.com/docs/guide/sandbox-networking says nothing about ended sandboxes either.

Still to measure: the status code, body and timing for HTTP and WebSocket requests to an ended sandbox's tunnel. Also whether a URL is ever reused.

## 9. gVisor, Docker and `runtime="vm"`

Modal offers two runtimes. The VM runtime has its own Linux kernel and documents Docker support. The docs say nothing about `/proc/stat`, `node:sqlite`, `script` or `sudo` under gVisor.

Evidence:

- `types.py:112` defines `SandboxRuntime = Literal["gvisor", "vm"]`.
- `sandbox/_common.py:499-540` sends `runtime=a.runtime or config.get("function_runtime")`, so with no runtime set, the config or server default applies.
- https://modal.com/docs/guide/sandboxes says "VMs run the Sandbox in a virtual machine with its own Linux kernel. Use the VM runtime for workloads that expect a full Linux environment, such as running Docker inside the Sandbox, mounting FUSE filesystems, or managing resources with nested cgroups."
- The same page has a section "Running Docker in a Sandbox" that uses `runtime="vm"` and `readiness_probe=modal.Probe.with_exec("docker", "info")`. It says "`/var/lib/docker` is included in Filesystem Snapshots."
- The same page says GPUs run only on gVisor and nested virtualization needs the Enterprise plan.

Still to measure: `/proc/stat`, `node:sqlite`, `script` with a terminal and `sudo` on both runtimes. Docker on `runtime="vm"` from a Starter workspace.

## 10. `t3 serve`

`t3 serve` runs T3 headless with no browser. The T3 environment binds to a fixed address only when both `--host` and `--port` are given. State lives under `~/.t3/userdata/`. systemd is optional.

Evidence, from T3 Code at `f3b168b9`:

- `apps/server/src/cli/server.ts:85-99` defines `Command.make("serve", serveCommandFlags)` with the text "Run T3 Code in this terminal without opening a browser, and print pairing details. The background service runs this." It sets `startupPresentation: "headless"` and doesn't bootstrap a project from the working directory.
- `apps/server/src/cli/server.ts:76` shows that `t3 start` refuses to run beside a running server. `serve` has no such check.
- `apps/server/src/cli/config.ts:35` defines `--port`, "Port for the HTTP/WebSocket server". `:40` defines `--host`, "Host/interface to bind (for example 127.0.0.1, 0.0.0.0, or a Tailnet IP)". `:45` defines `--base-dir`. The matching env vars are `T3CODE_PORT`, `T3CODE_HOST` and `T3CODE_HOME`.
- In `resolveServerConfig` in the same file, an unset port in web mode goes to `selectAutomaticPort(DEFAULT_PORT)`, which picks the first free port from 3773. An unset host is `0.0.0.0` only with `T3CODE_LAN` or a managed launcher. Otherwise it is undefined.
- `apps/server/src/config.ts:30` sets `DEFAULT_PORT = 3773`.
- `apps/server/src/os-jank.ts:248-254` sets the default base directory to `~/.t3`.
- `apps/server/src/config.ts:141-177` puts state in `<baseDir>/userdata/`. That directory holds `statev2.sqlite`, `settings.json`, `keybindings.json`, `attachments/`, `logs/`, `environment-id`, `server-runtime.json`, `secrets/` and `anonymous-id`. Worktrees go to `<baseDir>/worktrees` and caches to `<baseDir>/caches`.
- `apps/server/src/background/KeepAwake.ts:64-79` runs `systemd-inhibit --what=idle:sleep` on Linux. A failure only logs "keep-awake stopped" at `:250`.
- `apps/server/src/background/serverOwnership.ts:123` reads `~/.config/systemd/user/<unit>` and returns nothing if it is absent.
- `serviceLauncher.ts` uses systemd only for `t3 service install`.

Still to measure: a full boot under `init.sh` with no systemd, and how long the log warning from KeepAwake repeats.

## 11. Web endpoints

Every web function has a 150-second request limit. A container boots in about 1 second, but the FastAPI app's import and startup time come on top of that and aren't documented.

Evidence:

- https://modal.com/docs/guide/webhook-timeouts says web functions have a "maximum HTTP request timeout of 150 seconds". After that, Modal answers with a 303 redirect to a result URL, up to 20 times, which is about 50 minutes in total. The page says this doesn't work with CORS. Its suggested pattern is to spawn the work and poll it with `FunctionCall.from_id(...).get(timeout=0)`, answering 202 until it finishes. This matches §6.3's `202 {opId}` pattern.
- https://modal.com/docs/guide/cold-start says "Containers boot in about one second", before imports and `@modal.enter`. `scaledown_window` defaults to 60 seconds and accepts 2 seconds to 20 minutes, so 300 seconds is valid. `min_containers` and `buffer_containers` keep warm containers.
- https://modal.com/docs/guide/concurrent-inputs documents `@modal.concurrent(max_inputs=..., target_inputs=...)`.
- https://modal.com/docs/guide/webhooks limits each function to 200 requests a second and each workspace to 2,000 a second. It says each WebSocket connection uses one function call.
- https://modal.com/docs/guide/webhook-proxy-auth says web functions are public by default unless `requires_proxy_auth=True`.

Still to measure: the controller's real cold start with FastAPI and the Modal client loaded.

## 12. Usage and cost APIs

Modal has two APIs for this. Both estimate cost at the workspace's rates and ignore credits.

Evidence:

- `app.py:139-207` defines `App.sandboxes.stats(since, until=None, group_by_tags=None, tags=None)`. Its docstring says "The maximum time range is 7 days." `since` must fall on a UTC hour, and more than 10,000 groups raises an error.
- `types.py:581-594` shows each group has `tags`, `cost` and `cost_by_resource`.
- https://modal.com/docs/guide/sandbox-resources documents the same API for cost by tag. It says "Costs don't account for credits or reservations."
- `sandbox/_sandbox.py:660-719` defines `Sandbox._experimental_stats(since, until)`. It returns `duration`, p50, p90 and p99 CPU and memory use, `cost`, `cost_by_resource`, `billed_cpu_core_seconds` and `billed_memory_gib_seconds`. Each sample "counts the greater of the measured and requested cores". The docstring says stats may lag by a few minutes.
- `_workspace.py:378` defines `Workspace.billing.report(start, end, resolution, tag_names)`. https://modal.com/docs/guide/billing says these reports need the Team or Enterprise plan.
- `sandbox/_impl_persistent.py` says persistent sandboxes don't support stats.

Still to measure: how close the per-tag cost comes to the month's bill, and how long the lag is.

## 13. Claude Code sign-in

`claude setup-token` prints a 1-year token that goes in `CLAUDE_CODE_OAUTH_TOKEN`. T3 asks Claude Code itself whether it is signed in, so the env var alone probably counts. The browser sign-in has a "Paste code here if prompted" step.

Evidence from Claude Code's docs:

- https://code.claude.com/docs/en/cli-reference says `claude setup-token` "Prints the token to the terminal without saving it. Requires a Claude subscription."
- https://code.claude.com/docs/en/authentication says to "generate a one-year OAuth token with `claude setup-token`". It says the command "does not save the token anywhere; copy it and set it as the `CLAUDE_CODE_OAUTH_TOKEN` environment variable."
- The same page says this token "can only make model requests". It can't start Remote Control sessions or fetch claude.ai connectors.
- The same page ranks credentials from highest to lowest: cloud provider credentials, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `apiKeyHelper`, `CLAUDE_CODE_OAUTH_TOKEN`, then "Subscription OAuth credentials from `/login`". An API key set in the image would therefore win over the token.
- The same page says "If your browser shows a login code instead of redirecting back after you sign in, paste it into the terminal at the `Paste code here if prompted` prompt."
- The same page says "On Linux, credentials are stored in `~/.claude/.credentials.json` with file mode `0600`". `CLAUDE_CONFIG_DIR` moves that file.
- The CLI reference documents `claude auth status`, which prints JSON with an `authMethod` such as `oauth_token` and exits 0 when signed in.
- https://code.claude.com/docs/en/github-actions warns that "an OAuth token is tied to the subscription of the person who ran `claude setup-token`".

Evidence from T3 Code at `f3b168b9`:

- `apps/server/src/provider/ClaudeProvider.ts:585-599` reads `email`, `subscriptionType`, `tokenSource`, `apiKeySource` and `apiProvider` from the Claude Agent SDK's initialize result.
- `ClaudeProvider.ts:213-228` decides sign-in. The comment says "Only tokenSource: "none" disproves auth." Any other `tokenSource` counts as signed in, and so do an API key, an email or a subscription type.
- `ClaudeProvider.ts:834-856` marks the provider unauthenticated otherwise and shows "Run `claude auth login`".
- `apps/server/src/provider/Drivers/ClaudeSkills.ts:450` is T3's only use of `CLAUDE_CODE_OAUTH_TOKEN`. It handles "an injected token" when choosing which account's plugins to load, which shows T3 expects this setup.

Still to measure: the `tokenSource` Claude reports with only the env var set, and whether T3 then shows Claude as signed in. Whether the code page appears when nothing answers on the localhost callback port.

## 14. Antigravity

Google publishes the Antigravity CLI for Linux. T3 downloads and manages its own copy, so the image doesn't need to install it. T3's sign-in on a remote machine finishes when the user pastes the redirect URL. T3 stores the token in a file under its own state directory.

Evidence from Google's docs:

- https://antigravity.google/download?os=linux lists the CLI for Linux. The glibc 2.28 requirement on that page is stated for the desktop app, not the CLI.
- https://antigravity.google/docs/getting-started?tab=cli installs the CLI with `curl -fsSL https://antigravity.google/cli/install.sh | bash` into `~/.local/bin/agy`.
- https://antigravity.google/docs/install?tab=cli describes a remote flow that prints a URL and takes back a code: "Because it cannot launch a local web browser, the CLI initiates a manual URL loop."
- https://antigravity.google/docs/cli/troubleshooting/ says the CLI stores credentials in "Linux secret-service through D-Bus" by default.

Evidence from T3 Code at `f3b168b9`:

- `apps/server/src/provider/antigravityCliRelease.ts:4-5` fetches release manifests from Google's updater at `.../manifests`. `:22-27` maps Linux x64 to the target `linux_amd64`, with `_musl` on musl systems. `:45` downloads from `storage.googleapis.com/antigravity-public/antigravity-cli/`.
- `apps/server/src/provider/AntigravityInstallation.ts:201-208` installs into `<baseDir>/tools/antigravity-acp/<platform>-<arch>/versions`. `:646` checks the download's SHA-512.
- `apps/server/src/provider/AntigravityAuth.ts:271` shows the message "Open the Google sign-in link. If you are remote, paste the redirect URL here."
- `apps/server/src/provider/antigravityCallback.ts:12-60` accepts only an `http://127.0.0.1` URL that matches the pending sign-in. `:64-81` forwards it to the CLI's local callback on the machine. This is a pasted redirect URL, not the code shown in Google's own CLI flow.
- `apps/server/src/provider/antigravityAuthSupport.ts:192` puts each provider profile at `<stateDir>/providers/antigravity/<sha256 of instance id>`. `:229-230` sets `GEMINI_HOME` to that profile and `AGY_ACP_FORCE_FILE_STORAGE=1`. `:385` keeps the token at `<profile>/antigravity-acp/acp_token.json`. With the default base directory, that is under `~/.t3/userdata/`.
- `antigravityAuthSupport.ts:219` notes "Each launch unpacks about 1 GB" into a T3-owned temp directory at `<stateDir>/antigravity-tmp/`.

Still to measure: a full install and sign-in on a sandbox, whether the token survives a disk sleep and a memory restore, and how much the 1 GB unpack adds to each snapshot.

## 15. T3 Code reads on connect

The sequence below is traced from the client source. A recorded session is still needed to list every read.

Evidence from T3 Code at `f3b168b9`:

- `packages/client-runtime/src/authorization/remote.ts:181-185` calls `GET /api/auth/session` with `authorization: Bearer <token>`.
- `remote.ts:192` and `:224` request a WebSocket ticket at `/api/auth/websocket-ticket/rpc` with the same header.
- `remote.ts:270-295` opens `/ws` with the ticket in a `wsTicket` query parameter. The client also adds parameters such as `clientSurface` and `connectionMethod` at `remote.ts:72-109`.
- `packages/client-runtime/src/rpc/session.ts:314` subscribes to `subscribeServerConfig` first. Its first event is the initial config.
- `session.ts:382-392` rejects that config unless `config.environment.environmentId` matches the paired environment. A capture must keep the same environment ID.
- `session.ts:462-464` then calls `server.probe`.
- `packages/client-runtime/src/state/shell.ts:636` subscribes to `orchestration.subscribeShell`, which loads the projects and thread list.
- Other streams the client opens include `subscribeServerLifecycle` at `state/server.ts:763`, `subscribeAuthAccess` at `state/auth.ts:163` and `subscribeTerminalMetadata` at `state/terminal.ts:92`. Whether each opens at connect or on demand was not traced.
- `packages/contracts/src/rpc.ts:441` and `:656-670` list the WebSocket method names. `packages/contracts/src/orchestrationV2.ts:3849-3873` lists the orchestration methods.
- `apps/server/src/rpcInitialItems.ts` shows subscriptions send their current state first and then live updates. A capture must replay the first part and keep the stream open.
- `packages/contracts/package.json` has `"name": "@t3tools/contracts"`, `"version": "0.1.10"` and `"private": true`. Its exports point at TypeScript source in `./src/`.
- The npm registry answers "Not found" for `@t3tools/contracts`.
- The `t3` package on npm is T3's server, at latest version 0.1.10. Its 9,526-byte tarball has 7 files, so it doesn't include the contracts source.

Still to measure: record one connect from T3 Code through the laptop proxy and list every method and HTTP path in order.

## 16. Connect tokens

A connect token is a different route from a tunnel. It protects a separate URL that Modal returns with the token, and it doesn't protect an `encrypted_ports` tunnel URL. Modal accepts the token in the `Authorization` header, a query parameter or a cookie.

Evidence:

- `sandbox/_sandbox.py:1016-1046` defines `create_connect_token(user_metadata=None, port=8080)`. It returns `SandboxConnectCredentials(url, token)`.
- https://modal.com/docs/guide/sandbox-networking says "The token may be sent in an `Authorization` header, in a `_modal_connect_token` query param, or in a `_modal_connect_token` cookie."
- The same page says the query parameter sets a cookie and requests go to port 8080 by default. The app receives an `X-Verified-User-Data` header. The page's examples use `Authorization: Bearer` for HTTP and the query parameter for `wss`. `user_metadata` must be under 512 characters.
- The same page documents `inbound_cidr_allowlist`, which applies to encrypted tunnels and connect tokens but not to unencrypted ports.
- https://modal.com/docs/guide/tunnels says tunnel URLs "are also public".
- From the T3 trace in question 15: T3 sends its own `Authorization: Bearer` on HTTP, at `remote.ts:185` and `:224`, and uses a `wsTicket` query parameter for `/ws`. Sending Modal's token in `Authorization` would therefore collide with T3's token on HTTP. The query parameter or cookie avoids the collision. Because the laptop proxy builds every upstream request, it can add `_modal_connect_token` itself. This is inferred.

Still to measure: whether Modal strips `_modal_connect_token` before forwarding, whether T3 accepts the extra query parameter, and whether WebSockets work through the connect-token URL. Also whether T3 must listen on 8080 or the token can name port 3773.

## 17. Starter plan

Starter costs $0 a month plus usage and includes $30 of compute credits each month. It allows 100 containers at once. It also limits crons, apps and log retention.

Evidence:

- https://modal.com/pricing lists Starter at "$0" plus compute, with "$30 / month free credits", 3 seats, "100 containers + 10 GPU concurrency", "Scheduled and Web Functions (limited)", 200 deployed apps, "5 deployed crons", 1 day of log retention and no environment-level budgets.
- The pricing FAQ says Starter "includes $30 of compute" per month. https://modal.com/llms.txt also says "$30/month of credits".
- https://modal.com/docs/guide/billing puts workspace billing reports on Team and Enterprise only.

At sandbox rates, $30 buys:

| Size | Cost per hour | Hours for $30 |
|---|---|---|
| 2 cores, 4 GiB | $0.380 | 79 |
| 1 core, 2 GiB | $0.190 | 158 |
| Standby, 0.25 cores and 1 GiB | $0.0595 | 504 |

The $20 default budget in §19 decision 13 sits inside the credits. The controller's web endpoint and its every-minute cron use list-price function rates of $0.0000131 per core-second, which are lower than sandbox rates.

Still to measure: whether the 100-container limit counts sandboxes, and what "limited" means for web functions on Starter.

## Contradictions with implementation.md

1. §1 and §3.2 row 5 say Docker is "probably" unavailable. Modal documents Docker on `runtime="vm"`, and `/var/lib/docker` is kept in filesystem snapshots. Question 9's fallback, "Document that Docker is unavailable", is probably wrong for VM machines.
2. §15's T3 tunnel row calls connect tokens "a second check" on the T3 tunnel. A connect token protects a separate URL that Modal returns with it, and an `encrypted_ports` tunnel stays public. Using connect tokens means routing T3 through that URL instead. Sending the token in `Authorization` would collide with T3's bearer token, so the proxy should use the query parameter.
3. §3.2 row 10 says no per-sandbox cost report was found, and §13 computes costs from runtime only. `App.sandboxes.stats(group_by_tags=...)` and `Sandbox._experimental_stats()` both report cost and billed usage. They cover at most 7 days and ignore credits.
4. §7.2 runs `t3 serve --host 0.0.0.0 --port 3773`, which is correct. Both flags are required. Without `--port`, T3 picks the first free port from 3773. Without `--host`, T3 doesn't bind to `0.0.0.0`.
5. The plan doesn't mention exit snapshots. With `experimental_options={"enable_exit_snapshot": True}`, Modal snapshots the filesystem on `terminate()`, idle timeout, lifetime expiry and OOM, and keeps it for 30 days. This beta feature, documented at https://modal.com/docs/guide/sandbox-exit-snapshots, could cover a sandbox that ends without a sleep. Its docs warn "Snapshot failure loses that Sandbox's filesystem state."
6. The plan doesn't mention persistent sandboxes. `_experimental_persistent` in `sandbox/_impl_persistent.py` chains sessions of 1 hour with a 5-minute idle timeout through exit snapshots. It doesn't support names, tags, memory snapshots or stats, so it doesn't fit t3modal now.
7. §4.4 rejects CRIU because "It needs kernel access that gVisor doesn't give." On `runtime="vm"`, the sandbox has its own kernel, so that reason no longer holds. Whether CRIU works there is unknown.
8. §6.4 rolls over a sandbox older than 23 hours 30 minutes and assumes each sandbox has its own 24-hour clock. The SDK's restore request has no timeout field, so t3modal can't set one on a memory restore. If a restore keeps the original deadline, the controller must count age from the first create in the chain, or rollovers will come too late.
9. §11's Claude Code row doesn't mention that a `setup-token` token "can only make model requests". It also doesn't mention that `ANTHROPIC_API_KEY` outranks it if both are set. The image and `init.sh` must leave `ANTHROPIC_API_KEY` unset.
10. §11 says T3's Google sign-in finishes by pasting the redirect URL. T3 does this, but Google's own CLI flow uses a pasted code instead. Since T3 forces file storage under `~/.t3/userdata/`, the token should survive a disk sleep, which question 14 asks about. The 1 GB unpack per launch also adds to snapshot size.
11. §3.3 lists "Free monthly credits on the Starter plan" without the limits that matter here. Starter allows 5 deployed crons, "limited" web functions, 1 day of logs and 100 containers. Workspace billing reports need Team.
12. §13 assumes "a Modal core equals 2 virtual CPUs". The docs and pricing page confirm this.
13. §6.1 says a cold start adds 1 to 3 seconds. Modal documents only "about one second" for container boot. The rest depends on the app's imports and is unmeasured.
14. §16 says Phase 0 results go into `docs/phase-0-findings.md`. This reading half is in `docs/phase-0-research.md`, so the plan should name both files or merge them later.
