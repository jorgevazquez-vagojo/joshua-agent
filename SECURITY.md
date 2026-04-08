# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.8.x   | Yes       |
| < 0.8   | No        |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report them privately via [GitHub Security Advisories](https://github.com/jorgevazquez-vagojo/joshua-agent/security/advisories/new) or by emailing the maintainer directly.

Include:
- A description of the vulnerability and its potential impact
- Steps to reproduce
- Affected versions
- Any suggested fix (optional)

You will receive an acknowledgement within 48 hours and a resolution or mitigation within 14 days for critical issues.

## Threat model

joshua-agent runs LLM CLI tools with filesystem access to your project directory. Key trust boundaries:

- **Runner process**: executes as the user who launched joshua. Use OS-level sandboxing (Docker, `firejail`, etc.) for untrusted projects.
- **Deploy commands**: validated against an allowlist (`safe_cmd.py`). Shell metacharacters are rejected. Wrap complex pipelines in script files.
- **Protected files**: configure `project.protected_files` globs to prevent agents from touching secrets, config, or infra files.
- **HTTP server**: requires `JOSHUA_INTERNAL_TOKEN` (min 16 chars). Bind to loopback (`127.0.0.1`) unless behind a trusted reverse proxy.
- **LLM output**: treated as untrusted data in cross-agent context (prompt injection markers applied).

## Security features

- No `shell=True` anywhere — all subprocesses use `shlex.split()` + allowlist
- SSRF protection on health checks, webhooks, and Jira URLs
- Secret redaction in logs and error output
- SIGTERM + SIGKILL grace period to prevent zombie processes
- Rate limiting (30 req/min/token) on the HTTP API
- CORS locked down by default (`JOSHUA_ALLOWED_ORIGINS` to enable)
- SQLite DB restricted to `0600` permissions
