"""Lightweight web search via DuckDuckGo HTML — no API key required."""

import logging
import re
from typing import Any

import httpx

log = logging.getLogger("web_search")

_DDG_URL = "https://html.duckduckgo.com/html/"
_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


async def web_search(query: str, *, limit: int = 5) -> dict[str, Any]:
    """Search DuckDuckGo and return top results."""
    if not query.strip():
        return {"error": "empty query"}

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AnyliseBot/1.0)",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.post(
                _DDG_URL,
                data={"q": query, "b": ""},
                headers=headers,
            )
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        log.warning("DuckDuckGo search failed: %s", e)
        return {"error": f"search failed: {e}", "results": []}

    results: list[dict[str, str]] = []
    for match in _RESULT_RE.finditer(html):
        if len(results) >= limit:
            break
        url = match.group(1)
        title = _strip_html(match.group(2))
        snippet = _strip_html(match.group(3))
        if url and title:
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
            })

    return {
        "query": query,
        "results": results,
        "count": len(results),
    }
