# Run every machine on Modal's VM runtime

Every sandbox runs with `runtime="vm"`, and there is no fallback to Modal's default gVisor runtime. On the VM runtime, `sudo`, Docker and cgroup CPU stats work, and Modal recommends it for memory snapshots. On the default runtime, Phase 0 found that `sudo` fails because the container sets "no new privileges", and Docker fails on iptables. A fallback would be a second kind of machine where projects that need `sudo` or Docker break in ways that are hard to see, so a VM sandbox that can't start fails the wake with a clear error.

## Consequences

- No GPU machines. Modal supports GPU sandboxes only on the default runtime.
- The root disk is a fixed 512 GiB, so t3modal can't offer disk sizes.
- Modal shows no separate price for the VM runtime. The sandbox rates apply.
- Docker is supported, and `init.sh` starts `dockerd` at boot.
