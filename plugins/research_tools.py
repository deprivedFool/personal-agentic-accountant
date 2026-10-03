"""
Live lookups on official sources, for rules the jurisdiction pack doesn't cover.

Results are always labelled UNVERIFIED: they come from a web page fetched at run time, not from
the tested pack. Only official domains are allowed (the pack's ``official_sources`` plus
government domains), so agents can't wander to arbitrary sites.
"""

import re
import urllib.request
from datetime import date
from html import unescape
from typing import Any, Dict, List
from urllib.parse import urlparse

from plugins.base_tool import canvas_tool
from plugins.tax_tools import active_pack

MAX_BYTES = 2_000_000
MAX_CHARS = 8_000
TIMEOUT_SECONDS = 15
GOVERNMENT_SUFFIXES = (".gov", ".gov.pt", ".gouv.fr", ".gob.es", ".gov.uk", ".europa.eu", ".bund.de", ".admin.ch")


def allowed_domains() -> List[str]:
    return list(active_pack().get("official_sources", []))


def _is_allowed(host: str) -> bool:
    host = host.lower().split(":")[0]
    for domain in allowed_domains():
        if host == domain or host.endswith("." + domain):
            return True
    return any(host.endswith(suffix) or host == suffix.lstrip(".") for suffix in GOVERNMENT_SUFFIXES)


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg|head).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|table|section)>", "\n", html)
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _download(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "personal-agentic-accountant/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_BYTES)
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


@canvas_tool(param_descriptions={
    "url": "https URL of an official page (tax authority, social security, central bank, official gazette)",
    "look_for": "Optional keywords; only the passages containing them are returned",
})
def fetch_official_page(url: str, look_for: str = "") -> Dict[str, Any]:
    """Read an official web page for a rule the verified pack doesn't cover. The result is UNVERIFIED."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return {"status": "REFUSED", "message": "Only https URLs are allowed."}
    if not _is_allowed(parsed.netloc):
        return {"status": "REFUSED", "message": f"{parsed.netloc} is not an official source.",
                "allowed_domains": allowed_domains() or "government domains only"}
    try:
        text = html_to_text(_download(url))
    except Exception as exc:
        return {"status": "FAILED", "message": f"Couldn't fetch the page ({type(exc).__name__}: {exc})."}
    if look_for:
        keywords = [k.lower() for k in look_for.split() if len(k) > 2]
        paragraphs = [p for p in text.split("\n") if any(k in p.lower() for k in keywords)]
        text = "\n".join(paragraphs) or "(no passage matched the keywords)"
    return {
        "status": "UNVERIFIED",
        "source": url,
        "retrieved": date.today().isoformat(),
        "text": text[:MAX_CHARS],
        "truncated": len(text) > MAX_CHARS,
        "how_to_use": "Quote it as 'unverified (source, date)', keep it separate from the verified pack figures, "
                      "and recommend the client confirm it before acting.",
    }
