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
