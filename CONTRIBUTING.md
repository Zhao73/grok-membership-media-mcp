# Contributing

Contributions are welcome when they preserve the membership-only security
boundary and the no-duplicate-quota guarantees.

## Local setup

```bash
git clone https://github.com/Zhao73/grok-membership-media-mcp.git
cd grok-membership-media-mcp
./scripts/setup.sh
```

Run the test suite:

```bash
.venv/bin/pytest
```

Preview the static project page:

```bash
python3 -m http.server 4173 --directory site
```

## Pull requests

- Add or update tests for behavior changes.
- Do not add an OpenAI or xAI developer API adapter, API-key fallback, or direct
  REST implementation.
- Never commit browser cookies, Grok session files, SQLite job state, raw
  manifests, or generated artifacts that contain local paths.
- Keep the vendored upstream file unchanged. Update its version, commit, digest,
  license, and provenance together.
- For UI changes, preserve the Hallmark token contract and verify 320, 375, 414,
  768, and desktop widths.

By contributing, you agree that your contribution is licensed under the MIT
License in this repository.
