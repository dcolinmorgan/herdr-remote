# Agent Instructions

## Guidelines

- Read the project README and any existing docs before making changes
- Run the project's build/test commands before committing (check package.json, Makefile, pyproject.toml, Cargo.toml)
- Keep changes minimal and focused on the task
- Prefer early returns over nested conditionals
- Handle error states explicitly
- Use semantic HTML and ARIA attributes for accessibility in frontend code
- Follow existing code style and conventions in the repo
- Do not introduce new dependencies without justification

## Verification

- Run linting and type checks before committing
- Run tests relevant to changed code
- Verify the build passes

## Git

- Write clear, concise commit messages
- Stage only files related to the current task
- Do not push to main/master without explicit permission

## Remote access tunnel backends

`HERDR_TUNNEL_MODE` (in `~/.config/herdr-remote/config.env`) selects how
the relay is reached remotely: `temp`/`named` (Cloudflare Tunnel, the
default, driven by `relay/install-service.sh`) or `aws` (a reverse SSH
tunnel to a self-hosted EC2 host, driven by `relay/tunnel-aws.sh`; see
`infra/aws-tunnel/README.md` for the host definition, cost, and deploy
steps). `relay/herdr-remote` is the canonical `start/stop/status` wrapper
and supports both. The relay itself always stays bound to loopback
(`relay/herdr_relay.py`) regardless of tunnel backend.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
