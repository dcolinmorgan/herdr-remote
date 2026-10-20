# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in herdr-remote, please report it privately:

1. **GitHub Private Vulnerability Reporting** (preferred): Use the "Report a vulnerability" button in the Security tab of this repository.

2. **Email**: Contact the maintainer directly at the email address in the git commit history.

Please do **not** open a public issue for security vulnerabilities.

## What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if you have one)

## Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial assessment**: Within 7 days
- **Fix timeline**: Depends on severity, typically within 30 days for critical issues

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.6.x   | :white_check_mark: |
| < 0.6   | :x:                |

## Security Best Practices

When running herdr-remote:

- Use a unique `HERDR_RELAY_TOKEN` (not the example token)
- Run the relay behind a reverse proxy with TLS (Cloudflare Tunnel, nginx, etc.)
- Keep the relay on a private network when possible
- On a public tunnel, put a login page in front of the token with Cloudflare Access ([REMOTE_ACCESS.md](REMOTE_ACCESS.md))
- Regularly update to the latest version

## What the relay can read

Three capabilities are worth knowing about before you expose a relay:

**Terminal contents.** Any connected client can ask for a pane's rendered output and can send keys
and text to the agent in it. That is the point of the tool, but it means relay access is equivalent
to sitting at the keyboard.

**Agent conversation transcripts.** `get_history` reads the transcript the agent writes for itself
(for Claude, `~/.claude/projects/<project>/<session-uuid>.jsonl`) and sends its turns to the client.
Those files contain everything said in the session: prompts, file contents the agent read, command
output, and any secret that passed through the conversation. For a pane on a host listed in
`HERDR_REMOTES`, the relay reads that file over SSH from the remote home directory, so the relay
also carries that host's transcripts to the client.

- The path shape is fixed: `<root>/*/<session-uuid>.jsonl`, where the roots come from the relay's
  own environment (`HERDR_CLAUDE_ROOTS`, `HERDR_REMOTE_CLAUDE_ROOTS`) and the uuid must match
  `^[0-9a-f]{8}-...-[0-9a-f]{12}$`. Clients send a `pane_id`, never a path or a session id, and the
  relay resolves it through state it built from `herdr pane list`.
- Codex UUID refs locate `rollout-*.jsonl` files under the configured session roots
  (`HERDR_CODEX_ROOTS`, default `$CODEX_HOME/sessions` or `~/.codex/sessions`).
  Local symlinks cannot escape those roots. SSH reads use `HERDR_REMOTE_CODEX_ROOTS`
  (default `$HOME/.codex/sessions`), with the same UUID and remote-root validation.
  Reasoning and developer/system messages are not sent to clients.
- Set `HERDR_TRANSCRIPT=0` to switch the whole capability off. `get_history` then answers
  `unavailable: "disabled"` and no transcript is opened, locally or remotely.

**Shell panes (off by default).** `HERDR_SHELL_PANES` makes the relay list, read and write the
panes that have no agent in them -- two thirds of the panes on a typical host. The read half is the
same exposure as above. The write half is not: text sent to a shell pane is a **command**, and the
relay follows it with Enter. There is no harness in between to detect a question, refuse a
free-text answer or show an approval prompt, which is exactly what the agent-pane path relies on.

- With the switch off (the default, and the behaviour of every release before it), non-agent panes
  are not listed, are not in `known_panes`, and every message naming one is refused as an unknown
  pane. Turning it on is the only way in.
- With it on, relay access is shell access to every host the relay polls, including everything in
  `HERDR_REMOTES`. Treat the relay token as you would an SSH key.
- Shell commands are audited under `respond_shell` with the client IP, the device string and the
  full text, separately from agent responses.

## What the relay can write

**Uploaded files (on by default).** The web app can attach a file to a reply. The relay writes the
file to the machine where the pane's agent runs and puts the file's absolute path in the reply box.
The agent then reads whatever the reader uploaded. A hostile file, for example a screenshot or a
document that holds instructions, reaches the agent as content it reads, the same as text typed into
the pane. Relay access already lets a client type to the agent, so uploads give no new control of
the agent. They do give a new write: a file of the client's choosing lands on every host the relay
polls, including everything in `HERDR_REMOTES`.

- Files land in `~/.cache/herdr-remote/uploads/` on this host, or in
  `$HOME/.cache/herdr-remote/uploads/` on a remote host. Both are outside every project, so an
  upload never shows in `git status`.
- The client never names the folder. Each agent conversation gets one folder, `s-<16 hex>`, from
  the SHA-256 of the pane's agent session ref. A pane with no ref gets `p-<16 hex>`, from the
  SHA-256 of its pane id.
- The client's file name keeps only `A-Z a-z 0-9 . _ -`. Every other character becomes `_`, leading
  dots go, and the result stops at 80 characters. An empty result becomes `file`. The relay puts 8
  random hex characters and a dash in front, so `../../.zshrc` lands as `<hex>-_.._.zshrc` inside
  the folder. For a remote host the relay checks the folder and the name against the same character
  set again before it builds the shell command, so no part of a client's name reaches the remote
  shell as code.
- Folders are mode 0700 and files 0600. A partial upload collects in `upload-spool/` under
  `HERDR_LOG_DIR` as a 0600 file, and the relay empties that folder before its first poll.
- One file is at most 20 MiB (`HERDR_UPLOAD_MAX_BYTES`). The relay refuses a larger declared size at
  `upload_start`, and refuses again when the chunks pass the declared size. A connection holds one
  upload at a time, a chunk holds at most 256 KiB, and the client waits for the ack of each chunk
  before it sends the next. An upload with no chunk for 60 seconds is dropped and its partial file
  deleted, and so is an upload whose connection closes. The web app sends at most 5 files from one
  pick or one paste.
- The pane gate is `known_panes`, as for every other message. With `HERDR_SHELL_PANES` off, only
  agent panes take uploads.
- Two rules delete uploads:
  1. **The conversation ends.** The relay records each folder it writes in `uploads.json` in
     `HERDR_LOG_DIR`. After a clean `pane list` read of a host, it deletes a recorded folder when the
     pane is gone, or when the pane now reports a different agent session ref, or none. It deletes
     only folders it recorded, so the uploads of a second relay on the same host are safe. It
     judges an entry only against the herdr session it was written under, so a `session_switch`
     does not delete the other session's uploads. A failed `pane list`, or one that answers with an
     error, deletes nothing for that host, because an empty list reads as "every pane closed".
  2. **The file is older than 72 hours** (`HERDR_UPLOAD_TTL_HOURS`). At start-up and then every
     hour, the relay deletes old files from every folder whose name has the exact `s-` or `p-`
     plus 16 hex shape, recorded or not, and then every empty folder of that shape. This rule
     covers the uploads of every relay, because every relay applies the same age limit. Neither
     rule follows a symlink, and neither rule touches any other name under the root.
- Each finished upload writes an `upload` audit line with the client IP, the device string, the
  pane id, the path and the size.
- Set `HERDR_UPLOADS=0` to switch the capability off. The relay then sends no `upload_max_bytes`,
  the web app shows no attach button, and the relay refuses every `upload_start`.
