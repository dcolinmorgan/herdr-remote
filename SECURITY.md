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
