---
name: grok-membership-media
description: Use the local paid ChatGPT and Grok memberships to create website first frames and real MP4 video without developer APIs or API keys.
---

# Grok Membership Media

Use the `grok-membership-media` MCP whenever the user requests a website hero
video, product video, animated visual, Grok video, or a GPT-first-frame plus
Grok-video workflow.

## Required workflow

1. Inspect the current website and choose the target aspect ratio and output
   directory.
2. Call `media_doctor`. Never start generation unless it reports Grok
   `auth_mode = grok.com_membership`, `api_key_auth_disabled = true`, and
   `developer_api_enabled = false`.
3. Call `start_website_video`. Use `first_frame_provider = auto` unless the user
   explicitly chooses ChatGPT, Grok, or supplies an existing source image.
4. Poll `get_media_job`; do not start another job while the first is queued,
   running, or `submitted_unknown`.
5. On completion, verify the MP4, web MP4, poster, first frame, manifest, and
   SHA-256 fields before editing the website.
6. Only modify the website if the user requested integration. Use the muted
   web MP4 for autoplay and preserve the master MP4 with audio.

## Hard constraints

- Never request, configure, or use `XAI_API_KEY`, `OPENAI_API_KEY`, or any
  developer API.
- ChatGPT generation must remain `--backend web`; never use `auto` or `codex`.
- Grok generation must remain the locally installed Grok Build CLI with
  `GROK_DISABLE_API_KEY_AUTH=1`.
- Grok supports video duration 6 or 10 seconds and resolution 480p or 720p.
- Video starts from an image. GPT web is preferred for the first frame; Grok
  membership `image_gen` is used only if GPT is unavailable before submission.
- Never auto-retry `submitted_unknown` or any task that already emitted a Grok
  media `tool_call`.
- Never place MP4 bytes/base64 in model context; return and use local paths.
