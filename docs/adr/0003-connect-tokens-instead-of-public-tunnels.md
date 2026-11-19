# Reach machines through connect tokens, not public tunnels

Modal's `encrypted_ports` tunnels are public. Anyone who learns a tunnel URL can reach T3's login page and the daemon. Each machine instead gets a connect token for each of its two ports, 3773 for T3 and 3774 for the daemon, and Modal answers 401 to any request without the token. The proxy sends the token as the `_modal_connect_token` query parameter, not in the `Authorization` header, because T3 reads that header for its own bearer token. In Phase 0, a request with Modal's token in the header reached T3 with no session.

## Consequences

- Each wake gets new URLs and tokens, because a memory restore creates a new sandbox. The controller makes the tokens at wake and keeps them in the machine's state file. The laptop keeps them in memory only. On a 401 from Modal, the laptop asks the controller for fresh tokens and retries once. Phase 0 saw tokens still valid after 90 minutes. Longer lifetimes are untested.
- The proxy tells Modal's answers apart from T3's. A 404 "Sandbox not found" means the sandbox has ended. A 401 means the token needs a refresh. No answer gets one retry after 2 s, then counts as an ended sandbox.
- Modal sets a `_modal_connect_token` cookie on answers to requests that carry the query parameter. The proxy removes that `Set-Cookie`, so T3 Code never holds Modal's token.
- The proxy passes T3 Code's own query parameters through next to the token.

## Considered options

- **Public tunnels.** Rejected for the exposure above. An ended sandbox's tunnel also closes the connection with no answer, so the proxy couldn't tell an ended sandbox from a network error.
- **Token in the `Authorization` header.** Rejected because of the clash with T3's bearer token.
