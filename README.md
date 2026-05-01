# unsloth-mcp-search

Stdio MCP server exposing Unsloth Studio's `web_search` tool to Claude Code (or any MCP client).

Single tool, two modes:
- `web_search({"query": "..."})` → DuckDuckGo search via `ddgs`, returns 5 snippets
- `web_search({"url": "..."})`   → fetch URL with SSRF protection + HTML→Markdown

Code is extracted verbatim from [unslothai/unsloth `studio/backend/core/inference/`](https://github.com/unslothai/unsloth/blob/main/studio/backend/core/inference/tools.py). Same anti-DNS-rebinding IP pinning, random User-Agent rotation, redirect handling, and minimal HTML→MD converter (no `html2text` dependency).

## Install (macOS)

Using `uv` (recommended):

```bash
# From the project directory:
uv tool install --from . unsloth-mcp-search
```

Or directly from a git remote once pushed:

```bash
uv tool install --from git+https://github.com/<you>/unsloth-mcp-search unsloth-mcp-search
```

## Register in Claude Code

```bash
claude mcp add unsloth-search unsloth-mcp-search
```

Or, if you prefer running ephemerally without install:

```bash
claude mcp add unsloth-search -- uvx --from /path/to/unsloth-mcp-search unsloth-mcp-search
```

## Verify

```bash
claude mcp list
# should show: unsloth-search → connected
```

Then in a Claude Code session: ask the model to search the web. It will call `web_search` automatically.

## License

AGPL-3.0-only (matching the upstream Unsloth Studio license).
