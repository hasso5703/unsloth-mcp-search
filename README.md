# unsloth-mcp-search

Stdio MCP server exposing Unsloth Studio's `web_search` tool to Claude Code (or any MCP client).

Single tool, two modes:
- `web_search({"query": "..."})` → DuckDuckGo search via `ddgs`, returns 5 snippets
- `web_search({"url": "..."})`   → fetch URL with SSRF protection + HTML→Markdown

Code is vendored from [unslothai/unsloth `studio/backend/core/inference/`](https://github.com/unslothai/unsloth/blob/main/studio/backend/core/inference/tools.py) — the web bits of `tools.py` (`web_search` / page fetch) plus `_html_to_md.py` verbatim. Same anti-DNS-rebinding IP pinning, non-global/SSRF address rejection, random User-Agent rotation, redirect handling, and minimal HTML→MD converter (no `html2text` dependency).

Synced as of upstream commit `a895d1c` (2026-06-01). `_html_to_md.py` is byte-identical to upstream; the web code tracks `tools.py` with only the standalone-glue changes (public function names, no sandbox/exec machinery).

## Install (macOS)

Using `uv` (recommended):

```bash
# From the project directory:
uv tool install --from . unsloth-mcp-search
```

Or directly from a git remote once pushed:

```bash
uv tool install --from git+https://github.com/hasso5703/unsloth-mcp-search unsloth-mcp-search
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

AGPL-3.0-only — matching the upstream Unsloth Studio license (`studio/LICENSE.AGPL-3.0`). The full text is in [`LICENSE`](./LICENSE). Because this is a derivative of AGPL-3.0 code, any distribution or network use must remain AGPL-3.0 and keep the source available; upstream copyright notices are preserved in each file.

## Trademark / affiliation

Not affiliated with, endorsed by, or sponsored by Unsloth AI Inc. "Unsloth" is a trademark of its respective owner; it is used here only to accurately describe the origin of the vendored code. This is an independent derivative work.
