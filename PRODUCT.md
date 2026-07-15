# Product context

## Product

Membership Media MCP is a local MCP server for generating website-ready images
and video through paid ChatGPT and Grok membership clients. It intentionally
does not implement an OpenAI or xAI developer API adapter.

## Users

- Claude Code and Codex developers building websites on macOS.
- Agent-tool authors who need a persistent, inspectable media job contract.
- Paid ChatGPT and Grok members who want to use the quota they already have.

## Primary job

From a natural-language request such as “make a hero video for this website,”
produce verified local media files without asking the agent to hold a browser
session open for the full generation time.

## Personality

Precise, technical, candid, and calm. Evidence comes before claims.

## Product principles

1. Membership-only policy is visible and enforced in code.
2. Unknown submission state stops the job instead of risking a second charge.
3. One installation path works for Claude and Codex clients.
4. Generated media is decoded, normalized, hashed, and re-verified.
5. Public examples contain no local paths, session identifiers, or credentials.

## Anti-references

- Generic AI-purple SaaS pages.
- Fake browser, terminal, or IDE chrome.
- Invented adoption metrics, testimonials, or compatibility claims.
- Vague “no API” language that hides the difference between membership clients
  and developer APIs.

## Accessibility target

WCAG 2.2 AA: keyboard-operable navigation, visible focus, 44px touch targets,
reduced-motion support, descriptive video text, and tested layouts at 320, 375,
414, and 768 CSS pixels.
