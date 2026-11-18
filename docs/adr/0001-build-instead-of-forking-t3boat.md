# Build t3modal from scratch instead of forking t3boat

t3boat already does most of what t3modal needs, and about 70% of its Rust code doesn't depend on Boat. We still chose to write our own code. A fork would bring code shaped around Boat: a sandbox that stops and resumes in place, disks deleted after 14 days, and keep-alive wakes. That code would confuse a design built on Modal, where every sleep ends the sandbox. We use t3boat only as a reference, mainly its notes on T3's WebSocket protocol, and credit it in the README. Any piece copied from it keeps its MIT notice.

## Consequences

- The proxy, setup and machine lifecycle are new work. The plan grows by roughly 3 to 4 weeks.
- We don't take on t3boat's stack, Rust and Tauri. See ADR 0002.
