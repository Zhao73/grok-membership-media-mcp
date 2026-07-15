# Grok Membership Media MCP

Local MCP server that turns paid ChatGPT and Grok memberships into a reusable
website media tool for Claude Code, Claude Desktop, Codex CLI, and Codex
Desktop.

**[Open the live project page](https://zhao73.github.io/grok-membership-media-mcp/)** ·
**[Read the Chinese architecture](docs/PLAN.zh-CN.md)**

[![Real six-second membership output](site/media/demo-poster.jpg)](https://zhao73.github.io/grok-membership-media-mcp/#demo)

The implementation has produced and re-verified a real 6.04-second 720p MP4
through a Grok paid-member session. The public demo is a metadata-cleaned copy;
raw job manifests and local session data are not published.

## Non-negotiable policy

The MCP implementation contains no xAI/OpenAI developer API adapter and its
runtime cannot select one:

- no `XAI_API_KEY`;
- no `OPENAI_API_KEY`;
- no direct xAI/OpenAI REST request;
- no xAI public API fallback;
- no `chatgpt-imagegen` Codex backend.

The pinned upstream `chatgpt-imagegen` file is vendored unchanged for
provenance and still contains its upstream Codex implementation. This MCP never
invokes it: `scripts/chatgpt-imagegen-web-only` rejects every generation command
unless it contains exactly `--backend web`, and the Python adapter independently
hardcodes the same value.

GPT first frames are created by the vendored `chatgpt-imagegen v0.21.0` with
`--backend web`, using the logged-in ChatGPT browser. Grok images and videos are
created by the locally installed Grok Build CLI using its cached grok.com paid
membership. Every Grok subprocess gets `GROK_DISABLE_API_KEY_AUTH=1` and all
known API-key environment variables are removed.

Grok Build itself must communicate with grok.com; “no API” here means no
developer API, no developer API key, and no direct REST implementation in this
plugin.

## Tools

- `media_doctor`: verifies membership auth and the no-API policy without
  generating media.
- `start_website_video`: starts a detached, persistent job and returns a job ID.
- `get_media_job`: polls status and returns verified artifacts.
- `list_media_jobs`: lists recent jobs.
- `cancel_media_job`: stops the local worker without pretending the upstream
  generation was cancelled.

## Routing

```text
ChatGPT browser membership first frame
  -> Grok Build membership image_to_video

ChatGPT unavailable before submission
  -> Grok Build membership image_gen
  -> Grok Build membership image_to_video
```

If ChatGPT or Grok may already have received a request, the job is not retried.

## Setup

```bash
git clone https://github.com/Zhao73/grok-membership-media-mcp.git
cd grok-membership-media-mcp
./scripts/setup.sh
```

Install the same launcher for Codex/Codex Desktop and Claude Code:

```bash
codex mcp add grok-membership-media -- \
  "$PWD/scripts/run-mcp.sh"

claude mcp add --scope user grok-membership-media -- \
  "$PWD/scripts/run-mcp.sh"
```

Codex Desktop uses the Codex MCP configuration. Claude Desktop uses the same
command in `~/Library/Application Support/Claude/claude_desktop_config.json`.

The routing skill can be symlinked into both clients:

```bash
ln -s "$PWD/skills/grok-membership-media" \
  "$HOME/.codex/skills/grok-membership-media"
ln -s "$PWD/skills/grok-membership-media" \
  "$HOME/.claude/skills/grok-membership-media"
```

The MCP writes job state to:

```text
~/.local/share/grok-membership-media-mcp/
```

Generated files are written into the absolute `output_dir` supplied to
`start_website_video`:

```text
name.mp4
name-web.mp4
name-poster.jpg
name-first-frame.png
name-manifest.json
```

`name.mp4` preserves the Grok audio. `name-web.mp4` is muted and fast-started
for website autoplay and is always encoded as H.264 8-bit `yuv420p`. Every
user-supplied source image is hash-checked, copied into an immutable per-job
snapshot, decoded, cropped/scaled to the requested aspect ratio, and normalized
to PNG before Grok video generation.

Before quota can be spent, SQLite atomically reserves `output_dir + name`.
Publishing uses no-replace hard links from a same-filesystem staging directory,
so a file that appears concurrently is never overwritten. Completed-job reads
recompute every artifact SHA-256, verify the manifest's own SHA-256, and compare
its request, output map, and no-developer-API policy with the database record.
The manifest is published last. An uncatchable power loss or `SIGKILL` can leave
partial media files, but cannot create a valid completed bundle or trigger an
automatic retry.

## Example MCP call

```json
{
  "prompt": "A refined black-and-gold product scene matching the current website",
  "motion_prompt": "Slow cinematic push-in, subtle dust and light movement, product remains stable.",
  "output_dir": "/absolute/site/public/media/hero",
  "name": "hero",
  "first_frame_provider": "auto",
  "duration_seconds": 6,
  "resolution": "720p",
  "aspect_ratio": "16:9"
}
```

## Limits

- Grok membership video accepts 6 or 10 seconds.
- Video resolution is 480p or 720p.
- Grok Build has no machine-readable remaining-quota command; quota exhaustion
  is reported only when the media tool is submitted.
- All Grok video tasks should be treated as machine-wide single-concurrency;
  the job store prevents MCP-client restarts from losing status.

## ChatGPT browser relay

The GPT route requires `chrome-use` and its Chrome Web Store extension in the
ChatGPT logged-in profile. The CLI/native host can be installed automatically;
Chrome itself requires the user to confirm the extension once:

<https://chromewebstore.google.com/detail/chrome-use/knfcmbamhjmaonkfnjhldjedeobeafmk>

After that confirmation, connect without restarting Chrome:

```bash
~/.local/bin/chrome-use reconnect --keep-banner
~/.local/bin/chrome-use extension status --json
~/.local/bin/chrome-use browsers --json
```

Until the relay is connected, `first_frame_provider=auto` safely uses Grok
membership image generation only when ChatGPT failed before submission. Grok
video generation remains fully available.

## Verified real output

The demo was submitted through the Grok Build grok.com membership mode and
completed with `submission=confirmed`:

- [`site/media/demo.mp4`](site/media/demo.mp4): 6.041667 seconds, 1280x720,
  silent H.264/yuv420p, fast-started for the web;
- [`site/media/demo-evidence.json`](site/media/demo-evidence.json): sanitized
  codec, policy, provider, and SHA-256 evidence;
- full decode, duration, aspect, stream, output hash, manifest hash, state, and
  policy checks pass in the local completed bundle;
- the automated suite covers provider policy, job persistence, retry safety,
  path containment, publication races, artifact verification, and the public
  site contract.

## Requirements

- macOS and zsh (the currently verified platform);
- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/);
- FFmpeg and ffprobe;
- a locally installed, logged-in Grok Build client with paid video quota;
- optionally, Chrome logged into ChatGPT plus the `chrome-use` relay for GPT
  first frames.

Linux and Windows support is not claimed yet.

## Development and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and pull-request rules, and
[SECURITY.md](SECURITY.md) for private vulnerability reporting. Never attach
raw manifests, cookies, local job databases, or provider session files to a
public issue.

## License and attribution

This project is MIT licensed. The vendored `chatgpt-imagegen` launcher retains
its upstream MIT license and pinned provenance; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This is an independent, unofficial project. It is not affiliated with,
endorsed by, or sponsored by xAI, OpenAI, or Anthropic.
