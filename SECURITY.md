# Security policy

## Supported version

Security fixes target the latest commit on `main` until tagged releases are
introduced.

## Reporting a vulnerability

Use the repository's private GitHub security advisory flow:

<https://github.com/Zhao73/grok-membership-media-mcp/security/advisories/new>

Do not paste these materials into a public issue:

- Grok or ChatGPT browser cookies;
- Grok session files or session identifiers;
- raw generated manifests;
- local SQLite state;
- local absolute paths that reveal account or machine details.

Include the affected commit, a minimal reproduction, and the expected security
boundary. Remove all authentication and local-session data first.
