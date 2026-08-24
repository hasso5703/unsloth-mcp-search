# unsloth-mcp-search

Stdio MCP server exposing Unsloth Studio's `web_search` tool to Claude Code (or any MCP client).

Single tool, two modes:
- `web_search({"query": "..."})` → DuckDuckGo search via `ddgs`, returns 5 snippets
- `web_search({"url": "..."})`   → fetch URL with SSRF protection + HTML→Markdown

Code is vendored from [unslothai/unsloth `studio/backend/core/inference/`](https://github.com/unslothai/unsloth/blob/main/studio/backend/core/inference/tools.py): the web bits of `tools.py` (`web_search` / page fetch) plus `_html_to_md.py` verbatim. Same anti-DNS-rebinding IP pinning, non-global/SSRF address rejection, random User-Agent rotation, redirect handling, and minimal HTML→MD converter (no `html2text` dependency).

Synced as of upstream commit `a8be2a8` (2026-08-14). `_html_to_md.py` is byte-identical to upstream; the web code tracks `tools.py` with only the standalone-glue changes (public function names, no sandbox/exec machinery). Checked against upstream `main` through `f2fa54f` (2026-08-24): nothing to re-port, the only web-path changes since were Studio-frontend features (model-sized page budgets, inline image search) that are out of scope here.

Carried over from upstream in this sync:

- **`UnicodeError` on resolve**: IDNA rejects a bad hostname with `UnicodeError`, not `OSError`, which escaped as an unhandled exception.
- **Bare hostnames**: `example.com` / `example.com:8443` are fetched as `https://`, instead of being refused.
- **Binary rejection**: known-binary MIME types and magic signatures return a placeholder rather than being decoded into replacement chars.
- **PDF extraction**: page-delimited text, capped at 50 pages. Upstream uses Studio's RAG parser (pymupdf); this server uses `pypdf` (pure Python, text-only).
- **Main-content extraction**: `html_to_markdown(..., main_content=True)`: `<article>`/`<main>` scoping, boilerplate stripping (skip-links, cookie/session banners), `<header>` dropped, `<aside>` kept for docs admonitions, plus the `_VOID_TAGS` fix so void elements no longer corrupt hidden-subtree bounding.
- **GitHub repo roots**: routed to the README API, so the README is read instead of the page's UI chrome.
- **One fetch deadline**: a single wall-clock budget covers the README attempt, the HTML fallback and every redirect hop, so a slow step cannot hand its fallback a fresh full timeout.
- **Enterprise proxies**: set `UNSLOTH_MCP_DISABLE_DNS_PINNING=1` to send the hostname (not the pinned IP) when a proxy needs it for CONNECT/TLS interception. The opt-out only takes effect when urllib actually routes the request through a configured proxy (the proxy resolves the hostname, so nothing rebinds behind us); a direct fetch stays pinned either way. A request whose host matches `NO_PROXY` is also forced direct, since the pinned-IP request URL would never match the entry on its own.
- **Classified search failures**: ddgs raising on an empty sweep now reads `No results found.` instead of an error; rate limiting and engine timeouts return actionable text (wait a minute, or fetch a known page directly with `{"url": "<URL>"}`).

Deliberately not carried over (Studio-only): `website_policy` allow/block domain lists and the `cancel_event` cancellation plumbing.

## Local hardening (not from upstream): throttle, cache, budget

A burst of agent searches makes the search engines rate limit this machine (slow or empty sweeps). `ddgs` then walks several slow engines within one call and the total runs past the MCP client's request timeout, which the client reports as an opaque `-32001 Request timed out`. `governor.py` sits in front of the `ddgs` call to prevent that:

- **Adaptive throttle** (additive-increase / multiplicative-decrease): near-zero spacing while sweeps succeed, growing automatically on each throttle signal, relaxing again as calls recover.
- **Hard per-call budget**: a search always returns well under the client timeout, with an actionable message instead of `-32001`.
- **Admission control**: past a concurrency cap, extra searches are refused fast with a busy message rather than queued into the timeout.
- **Short TTL cache**: repeated queries and refetched URLs return instantly. Only substantial page text is cached, so a transient error or bot-blocked page is not served for the whole TTL.

URL fetches pass straight through (they already carry their own deadline) but their result is cached. All of this is standalone glue in `governor.py`; `web.py` stays a near-verbatim mirror of upstream.

Tunable via environment (defaults shown):

| Variable | Default | Meaning |
|---|---|---|
| `UNSLOTH_MCP_SEARCH_BUDGET_S` | `18` | Hard ceiling per search. Keep under the client's MCP request timeout. |
| `UNSLOTH_MCP_SEARCH_MIN_INTERVAL_S` | `0.3` | Delay floor between search starts when healthy. |
| `UNSLOTH_MCP_SEARCH_PENALTY_STEP_S` | `1.5` | Extra spacing added on each throttle signal. |
| `UNSLOTH_MCP_SEARCH_PENALTY_CAP_S` | `6` | Cap on that spacing. `cap + budget` must stay under the client timeout. |
| `UNSLOTH_MCP_SEARCH_PENALTY_DECAY` | `0.5` | Fraction of the penalty kept per clean success. |
| `UNSLOTH_MCP_SEARCH_MAX_INFLIGHT` | `4` | Concurrent searches past which new ones are shed. |
| `UNSLOTH_MCP_SEARCH_CACHE_TTL_S` | `600` | Result cache lifetime (`0` disables). |
| `UNSLOTH_MCP_SEARCH_CACHE_MAX_ENTRIES` | `512` | Cache size cap. |

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

## Register in opencode

opencode reads MCP servers from `~/.config/opencode/opencode.json`. Add a local (stdio) server under `mcp`:

```json
{
  "mcp": {
    "web_search": {
      "type": "local",
      "command": ["uvx", "--from", "git+https://github.com/hasso5703/unsloth-mcp-search", "unsloth-mcp-search"],
      "enabled": true
    }
  }
}
```

Reload with `/mcp` (or start a new session) after editing; the model then calls `web_search` on its own.

## Run from an editable checkout (development)

`uvx` caches its build per package **version**, so an edit under `src/` is not picked up until `version` in `pyproject.toml` changes. During active development, point the client at an editable virtualenv instead, which follows `src/` directly with no cache and no version bump:

```bash
uv venv
uv pip install -e .
# entry point is now .venv/bin/unsloth-mcp-search
```

Use that binary as the MCP command. For opencode, set `mcp.web_search.command` to it:

```json
{
  "mcp": {
    "web_search": {
      "type": "local",
      "command": ["/path/to/unsloth-mcp-search/.venv/bin/unsloth-mcp-search"],
      "enabled": true
    }
  }
}
```

For Claude Code the same binary works: `claude mcp add unsloth-search -- /path/to/unsloth-mcp-search/.venv/bin/unsloth-mcp-search`. Edits take effect on the next `/mcp` reload.

## Verify

```bash
claude mcp list
# should show: unsloth-search → connected
```

Then in a Claude Code session: ask the model to search the web. It will call `web_search` automatically.

## License

AGPL-3.0-only: matching the upstream Unsloth Studio license (`studio/LICENSE.AGPL-3.0`). The full text is in [`LICENSE`](./LICENSE). Because this is a derivative of AGPL-3.0 code, any distribution or network use must remain AGPL-3.0 and keep the source available; upstream copyright notices are preserved in each file.

## Trademark / affiliation

Not affiliated with, endorsed by, or sponsored by Unsloth AI Inc. "Unsloth" is a trademark of its respective owner; it is used here only to accurately describe the origin of the vendored code. This is an independent derivative work.
