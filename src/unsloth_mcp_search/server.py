# SPDX-License-Identifier: AGPL-3.0-only
"""Stdio MCP server exposing Unsloth Studio's `web_search` tool.

Connect from Claude Code:
    claude mcp add unsloth-search uvx -- unsloth-mcp-search

The exposed tool matches Unsloth Studio's behavior verbatim: a single
`web_search` tool that does DuckDuckGo search when given `query`, or fetches
a URL to clean Markdown when given `url`.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .web import web_search as _web_search

mcp = FastMCP("unsloth-mcp-search")


@mcp.tool()
def web_search(query: str = "", url: str = "") -> str:
    """Search the web and fetch page content.

    Returns snippets for all results when called with `query`. Use the `url`
    parameter to fetch full page text from a specific URL (typically picked
    from a previous search result).

    Args:
        query: The search query (DuckDuckGo). Ignored if `url` is set.
        url:   A URL to fetch full page content from. When set, performs a
               direct fetch with HTML→Markdown conversion instead of searching.
    """
    return _web_search(query=query, url=url or None)


def main() -> None:
    """Stdio entry point — run with `unsloth-mcp-search` after install."""
    mcp.run()


if __name__ == "__main__":
    main()
