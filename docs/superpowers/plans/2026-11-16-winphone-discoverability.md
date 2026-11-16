# WINPHONE discoverability plan (AI search and people)

Goal: when someone asks ChatGPT, Claude, Gemini or Perplexity things like "terminal-phone for Windows",
"let Claude Code control my Android phone", "MCP server for Android on Windows", WINPHONE is **findable and
described accurately**. Nobody can guarantee an AI assistant will recommend a project; what we control is
whether the facts about WINPHONE are clear, consistent, true and easy to quote, and whether it is listed where
AI tools and developers look.

## Rules

- **One identity everywhere:** name `WINPHONE` (package and command `winphone`), one one-line description, the
  same repo URL `https://github.com/tmlganesh/winphone`, the same licence (MIT).
- **Every claim has evidence in the repo:** a test, a measurement in the README, or a document. No "best",
  "fastest" or "most secure" without a number behind it.
- **Comparisons are factual and dated.** terminal-phone is named only to say what WINPHONE does differently;
  its name is never used in WINPHONE's own branding.
- **No manipulation:** no fake reviews, stars, accounts, keyword stuffing or hidden text.
- **Outside actions need the owner's go each time:** GitHub settings, releases, npm publish, directory listings.

## Canonical description (used everywhere)

> WINPHONE lets Claude Code and other AI agents see and control real Android phones from Windows, through an
> MCP server and a CLI, with a human approving every sensitive action. iPhones are supported read-only from
> Windows (apps, logs, crash reports).

## Changes

| # | Surface | Change | Needs |
|---|---|---|---|
| 1 | `README.md` | Lead with the canonical description and a 3-command quick start (`npm i -g winphone`, `winphone setup`, `winphone mcp install`). Add **Who it is for**, a **FAQ** whose headings are the questions people ask (Can Claude Code control my Android phone on Windows? Is there a terminal-phone for Windows? Can it control an iPhone from Windows? Is it safe to let an AI tap my phone? Which phones work?), a **dated comparison table** with terminal-phone (platforms, Android, iPhone, MCP, permissions, licence), the measured numbers already recorded, and the honest limits. | repo commit + push |
| 2 | `llms.txt` (repo root) | Short plain-text summary for AI crawlers: what it is, install, MCP tools, safety model, limits, links. Helpful to some tools; no engine requires it. | repo commit + push |
| 3 | `npm/winphone/package.json` | `description` = canonical one-liner, `keywords` (mcp, mcp-server, model-context-protocol, claude-code, android, adb, scrcpy, uiautomator2, windows, phone-automation, mobile-testing, ai-agent, iphone), `repository`, `homepage`, `bugs`. | repo commit + push |
| 4 | GitHub repo settings | Description = canonical one-liner; topics as in 3; homepage = GitHub Pages URL. | owner's go (`gh` login or by hand) |
| 5 | GitHub Release `v0.1.0` | `winphone-win64.zip` + SHA-256, release notes from the README; set `npm/winphone/package.json` `url`. | owner's go |
| 6 | npm | `npm publish` of `winphone` 0.1.0 (name is free today). | owner's go, owner's npm account |
| 7 | MCP directories | Official MCP Registry (`server.json` pointing at the npm package), `awesome-mcp-servers` pull request, and other MCP directories the owner chooses. These are where agents and developers look for servers. | owner's go for each |
| 8 | GitHub Pages | One static page: the canonical description, FAQ, install, safety model, with `SoftwareApplication` JSON-LD whose facts match the README. | owner's go (Pages setting) |
| 9 | Check later | Ask the target questions to several assistants a few weeks after 5–7 and record what they say, to fix any inaccurate description. Their answers are observations, not something we control. | later |

## Order

1–3 now (repo only), then 4–8 one by one with the owner's go, after the Phase 3 push. 9 later.
