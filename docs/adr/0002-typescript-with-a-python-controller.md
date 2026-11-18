# TypeScript everywhere except the controller, which is Python

The local service, CLI, MCP server and daemon are TypeScript, so they can share T3 Code's own protocol types and one language runs on both the laptop and each machine. The controller is the one exception. Only Modal's Python SDK can deploy an app with web endpoints and a schedule, and the controller needs both to sleep and wake machines while the laptop is closed. Modal's JavaScript SDK can manage sandboxes but can't deploy functions.

## Considered options

- Rust with Tauri, like t3boat. Rejected because T3's protocol types would have to be rewritten by hand.
- Calling Modal from the laptop only, with no controller. Rejected because machines couldn't sleep or wake on schedule with the laptop closed.
