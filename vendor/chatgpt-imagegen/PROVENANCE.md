# Vendored dependency

- Upstream: https://github.com/leeguooooo/chatgpt-imagegen
- Tag: `v0.21.0`
- Commit: `9cb67e3c6e3579bdff1aabda42d5f5828d2c83d2`
- CLI SHA-256: `75c749b7962f86c2811925868af8f905cd767b4e0862dbe364db46b3a9675030`
- License: MIT; preserved in `LICENSE`

The upstream file remains byte-for-byte unchanged. This project invokes it only
through `scripts/chatgpt-imagegen-web-only`, which rejects every generation
command unless it contains exactly `--backend web`. The Python adapter also
hardcodes `web`; `auto` and `codex` cannot be selected by this MCP.
