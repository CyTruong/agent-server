"""Server-side web search for the agent (runs on the Linux box, NOT on the PC).

The model calls the `web_search` tool; the agent runs the query here and feeds
the results back as plain text. No relay / device round-trip is involved.

Backends (SEARCH_BACKEND):
  ddgs    - DuckDuckGo via the `ddgs` package. No API key. Free but unofficial;
            can rate-limit under load.
  tavily  - Tavily API (TAVILY_API_KEY). Built for LLMs; more reliable.

Everything is best-effort: on any failure we return a short error string so the
model can tell the user it couldn't search, rather than crashing the task.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

import config  # noqa: F401 - loads .env before the settings below are read

SEARCH_BACKEND = os.environ.get("SEARCH_BACKEND", "ddgs").lower()
SEARCH_REGION = os.environ.get("SEARCH_REGION", "vn-vi")   # DuckDuckGo region
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
SEARCH_TIMEOUT = float(os.environ.get("SEARCH_TIMEOUT", "15"))
# After searching, fetch the top few result pages and pull their readable text
# so the model can answer in detail (not just from snippets).
SEARCH_FETCH = os.environ.get("SEARCH_FETCH", "true").lower() in ("1", "true", "yes")
SEARCH_FETCH_TOP = int(os.environ.get("SEARCH_FETCH_TOP", "2"))     # how many pages
SEARCH_FETCH_CHARS = int(os.environ.get("SEARCH_FETCH_CHARS", "2000"))  # per page

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _clip(s: str, n: int = 300) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n] + "…"


def _html_to_text(html: str) -> str:
    """Best-effort readable text. Uses trafilatura if installed (much cleaner),
    else strips tags with a regex. Either way the model does the final sifting."""
    try:
        import trafilatura  # optional, better main-content extraction
        got = trafilatura.extract(html, include_comments=False,
                                  include_tables=False, favor_precision=True)
        if got:
            return got
    except Exception:  # noqa: BLE001 - not installed or failed; fall back
        pass
    import re
    html = re.sub(r"(?is)<(script|style|noscript|head|nav|footer|svg)[^>]*>.*?</\1>",
                  " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    import html as _h
    text = _h.unescape(text)
    return " ".join(text.split())


def _blocked_reason(url: str) -> str:
    """Why this URL must not be fetched, or "" if it is public. The model picks
    these URLs, and pages it read can steer it, so keep it off this server's
    localhost/LAN. Every address the host resolves to must be public (a DNS
    change between this check and the connect is not covered)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return "url không hợp lệ"
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError) as e:
        return f"không phân giải được tên miền ({e})"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        ip = getattr(ip, "ipv4_mapped", None) or ip
        if not ip.is_global:
            return f"địa chỉ nội bộ bị chặn ({ip})"
    return ""


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """Apply the same check to every redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = _blocked_reason(newurl)
        if reason:
            raise urllib.error.URLError(f"redirect bị chặn: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirect)


def fetch_page(url: str, max_chars: int = SEARCH_FETCH_CHARS) -> str:
    """Download a URL and return readable text (truncated). Server-side, text
    only, size/time capped. Returns 'error: ...' on failure."""
    url = (url or "").strip()
    reason = _blocked_reason(url)
    if reason:
        return f"error: {reason}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with _opener.open(req, timeout=SEARCH_TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype:
                return f"error: không phải trang văn bản ({ctype})"
            raw = resp.read(1_500_000)  # cap bytes read
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        html = raw.decode(charset, errors="replace")
    except Exception as e:  # noqa: BLE001
        return f"error: không tải được trang ({e})"
    text = _html_to_text(html)
    if not text:
        return "error: trang không có nội dung đọc được"
    return text if len(text) <= max_chars else text[:max_chars] + "…"


def _format(results: list[dict], fetch_top: int = 0) -> str:
    """results: [{title, body, href}] -> numbered text block for the model.
    For the first `fetch_top` results, also download and append page text."""
    if not results:
        return "web_search: không có kết quả."
    lines = []
    for i, r in enumerate(results, 1):
        title = _clip(r.get("title", ""), 160)
        body = _clip(r.get("body", ""), 300)
        href = r.get("href", "")
        block = f"{i}. {title}\n   {body}\n   nguồn: {href}"
        if i <= fetch_top and href:
            page = fetch_page(href)
            if not page.startswith("error:"):
                block += f"\n   nội dung trang: {page}"
        lines.append(block)
    return "Kết quả tìm kiếm web:\n" + "\n".join(lines)


def _search_ddgs(query: str, k: int) -> list[dict]:
    # Package was renamed duckduckgo_search -> ddgs; support both.
    try:
        from ddgs import DDGS
    except Exception:  # noqa: BLE001
        from duckduckgo_search import DDGS  # type: ignore
    out = []
    with DDGS(timeout=SEARCH_TIMEOUT) as ddg:
        for r in ddg.text(query, region=SEARCH_REGION, max_results=k):
            out.append({
                "title": r.get("title", ""),
                "body": r.get("body", "") or r.get("description", ""),
                "href": r.get("href", "") or r.get("url", ""),
            })
    return out


def _search_tavily(query: str, k: int) -> list[dict]:
    import json
    if not TAVILY_API_KEY:
        raise RuntimeError("thiếu TAVILY_API_KEY")
    payload = json.dumps({
        "api_key": TAVILY_API_KEY, "query": query,
        "max_results": k, "search_depth": "basic",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/search", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for r in data.get("results", []):
        out.append({
            "title": r.get("title", ""),
            "body": r.get("content", ""),
            "href": r.get("url", ""),
        })
    return out


def search(query: str, max_results: int = 5, fetch: bool | None = None) -> str:
    """Run a web search and return a text block (or an 'error:'/note string).
    When `fetch` (defaults to SEARCH_FETCH), also download the top few pages and
    include their text. Blocking - call via asyncio.to_thread from the agent."""
    query = (query or "").strip()
    if not query:
        return "error: thiếu query"
    k = max(1, min(int(max_results or 5), 10))
    try:
        if SEARCH_BACKEND == "tavily":
            results = _search_tavily(query, k)
        else:
            results = _search_ddgs(query, k)
    except ImportError:
        return ("error: chưa cài gói tìm kiếm. Cài: pip install ddgs "
                "(hoặc đặt SEARCH_BACKEND=tavily + TAVILY_API_KEY).")
    except Exception as e:  # noqa: BLE001
        return f"error: tìm kiếm thất bại ({e})"
    do_fetch = SEARCH_FETCH if fetch is None else fetch
    # Tavily already returns page content, so don't re-download.
    top = SEARCH_FETCH_TOP if (do_fetch and SEARCH_BACKEND != "tavily") else 0
    return _format(results, fetch_top=top)


if __name__ == "__main__":
    # Quick manual test:  python web_search.py "giá bitcoin hôm nay"
    import sys
    q = " ".join(sys.argv[1:]) or "giá bitcoin hôm nay"
    print(f"backend={SEARCH_BACKEND} region={SEARCH_REGION}\nquery={q!r}\n")
    print(search(q, 5))
