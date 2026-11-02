# Iris Chat

[Iris Chat](https://irischat.org) is an open-source, local-first messenger, similar
to Signal, with end-to-end encryption over decentralized Nostr relays. It needs
no signup, phone number or email. An agent creates its identity locally, without
a platform account or API key. Telegram bot chats are not end-to-end encrypted;
Iris encrypts messages between your device and this client.

## Set up

Install the [Iris CLI](https://irischat.org) and `uv` on
the machine running herdr-remote (macOS or Linux). Keep the relay running on its
default loopback address. Then:

```sh
uv run relay/herdr_iris.py setup
uv run relay/herdr_iris.py
```

Setup creates a separate, persistent agent identity and asks for **your public
Iris user ID** and a **private invite link** from your Iris app. It never needs
your secret key. Keep your app online while pairing. Repeating setup preserves
the existing identity and approved contact.

If the relay uses authentication, give this process the same `HERDR_RELAY_TOKEN`.
Managed installations store it in `~/.config/herdr-remote/secrets.env`; load that
file into the environment of your service. No tunnel or public listening port is
needed. The client talks to the local relay and Iris handles message delivery.

For unattended use, run `uv run /path/to/herdr-remote/relay/herdr_iris.py` with your
usual process supervisor alongside the relay. Keep the same environment and state
directory on every start.

## From your phone

| Message | Action |
| --- | --- |
| `/agents` | List agents and their pane IDs |
| `/read w1:p1` | Read recent agent output |
| `/send w1:p1 run the tests` | Submit a prompt to that agent |
| `/interrupt w1:p1` | Send Ctrl+C |
| `/help` | Show commands |

Use the exact pane ID from `/agents`. Completion and blocked notifications link
you to the appropriate `/read` command. Approvals and questions stay in the
terminal or web app in this version; `/send` refuses an agent reported as blocked.
Replies acknowledge submission to the relay; use `/read` to check what happened.

Only the paired direct contact can issue commands or receive output. That person
can control the agents exposed by your relay, including configured SSH hosts.
Shell panes are excluded. Commands older than five minutes expire; interrupted
requests are not automatically re-executed after a restart. A timeout can mean
delivery is uncertain: inspect the agent before sending a command again.

## Configuration

| Variable | Default |
| --- | --- |
| `HERDR_IRIS_STATE_DIR` | `~/.local/share/herdr-remote/iris` |
| `HERDR_IRIS_BINARY` | `iris` |
| `HERDR_RELAY` | `ws://127.0.0.1:8375` (loopback only) |
| `HERDR_RELAY_TOKEN` | Your existing relay token, if enabled |

The state directory contains keys, message history and processed request IDs.
Keep it private and backed up; use a new directory for a different owner. Never
point it at your personal Iris profile. Iris creates local keys without account
registration; approving the owner remains a deliberate setup step.

Iris can use your own message servers, including a local relay. Its apps also
offer nearby Wi-Fi/Bluetooth messaging where supported; this integration's tests
cover local Nostr relay delivery, not Bluetooth or mobile push notifications.

For noninteractive setup, pipe `{"owner":"npub…","invite":"…"}` to
`uv run relay/herdr_iris.py setup --json`. Keep the invite out of shell history
and public logs.

## Transport and tests

`iris_transport.py` is the unmodified MIT-licensed transport from
[`irislib/iris-chat-channels`](https://github.com/irislib/iris-chat-channels/blob/ccc5020d3622652ba9a606e8f15484dcc877be63/hermes/transport.py),
pinned at `ccc5020d3622652ba9a606e8f15484dcc877be63`. Its license is alongside it.
It owns CLI lifecycle, pairing, receipts and persistent request/delivery tracking.
Cryptography stays in the Iris CLI. The client adds only the already-used
`websockets` Python dependency; Telegram is not required to run it.

See [the Docker test recipe](../tests/iris/README.md) for isolated tests using
throwaway identities and no external network access.
