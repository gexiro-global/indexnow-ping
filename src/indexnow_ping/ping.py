#!/usr/bin/env python3
"""indexnow-ping: notify search engines that a site's URLs changed.

Two layers, both optional and independent:
  A. Google Search Console -- resubmit the sitemap (needs a service-account key).
  C. IndexNow -- ping Bing / Yandex / Seznam with the URL list.

Safety feature (the reason this exists): a *launch guard*. Before pinging anything
it reads the target's robots.txt and ABORTS if the site still blocks all crawlers
(`User-agent: *` + `Disallow: /`), so you cannot accidentally announce a
pre-launch/staging site to the search engines. Override with --force.

URLs come only from the site's own sitemap, and every URL is checked to belong to
the target host before it is submitted -- a sitemap cannot make this tool ping
arbitrary third-party hosts.

The parsing/validation logic is pure and import-light; network access is confined
to small, injectable functions so the whole thing is testable offline.
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import urllib.error

MAX_FETCH_BYTES = 5_000_000
DEFAULT_LIMIT = 200
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"


# ---------------------------------------------------------------- pure helpers

def _localname(tag: str) -> str:
    """Strip an XML namespace: '{ns}loc' -> 'loc'."""
    return tag.rsplit("}", 1)[-1]


def same_site(url: str, domain: str) -> bool:
    """True if *url*'s host is *domain* or a subdomain of it (case-insensitive)."""
    host = (urlparse(url).hostname or "").lower()
    domain = domain.lower().strip().rstrip(".")
    return bool(host) and (host == domain or host.endswith("." + domain))


def robots_allows_launch(robots_text: str) -> bool:
    """Replicate the launch guard: only `User-agent: *` + `Disallow: /` aborts.

    A blanket block for all agents means the site is not launched yet. Any other
    robots configuration (including no rules) is treated as launched.
    """
    in_star = False
    for raw in robots_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("user-agent:"):
            in_star = low.split(":", 1)[1].strip() == "*"
            continue
        if in_star and low.replace(" ", "") == "disallow:/":
            return False
    return True


def is_sitemap_index(xml_text: str) -> bool:
    try:
        return _localname(ET.fromstring(xml_text).tag) == "sitemapindex"
    except ET.ParseError:
        return False


def extract_locs(xml_text: str) -> list:
    """Return every <loc> text value in the document (namespace-agnostic)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return [(e.text or "").strip() for e in root.iter()
            if _localname(e.tag) == "loc" and (e.text or "").strip()]


def build_indexnow_payload(host: str, key: str, urls: list) -> dict:
    return {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": urls,
    }


# ------------------------------------------------------------------ network IO

def make_fetcher(user_agent: str, timeout: float, max_bytes: int = MAX_FETCH_BYTES):
    def fetch(url: str) -> str:
        req = Request(url, headers={"User-Agent": user_agent})
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 (http(s) only, validated)
            return resp.read(max_bytes).decode("utf-8", "replace")
    return fetch


def collect_sitemap_urls(fetch, sitemap_url, domain, limit=DEFAULT_LIMIT, max_depth=1):
    """Fetch *sitemap_url* and return page URLs on *domain*, following one level
    of sitemap-index nesting. Only same-site URLs are kept (anti-SSRF).
    """
    out = []

    def _walk(url, depth):
        if len(out) >= limit:
            return
        try:
            xml = fetch(url)
        except Exception as exc:  # a missing/broken child sitemap is not fatal
            log(f"sitemap fetch FAIL {url} ({type(exc).__name__})")
            return
        if is_sitemap_index(xml):
            # An index's <loc>s are child sitemaps, never pages. Recurse within
            # the depth budget; at the limit, stop (do NOT submit sitemap URLs).
            if depth < max_depth:
                for child in extract_locs(xml):
                    if same_site(child, domain):
                        _walk(child, depth + 1)
                    if len(out) >= limit:
                        return
            return
        for loc in extract_locs(xml):
            if loc.startswith("http") and same_site(loc, domain):
                out.append(loc)
                if len(out) >= limit:
                    return

    _walk(sitemap_url, 0)
    return out[:limit]


def preflight_guard(fetch, domain) -> bool:
    try:
        robots = fetch(f"https://{domain}/robots.txt")
    except Exception as exc:
        log(f"GUARD: cannot fetch robots.txt ({type(exc).__name__}) -> abort")
        return False
    if robots_allows_launch(robots):
        log("GUARD: launch state OK (robots allow).")
        return True
    log("GUARD: User-agent:* denies all -> abort (pre-launch). Use --force to override.")
    return False


def submit_indexnow(host, key, urls, timeout) -> int:
    payload = json.dumps(build_indexnow_payload(host, key, urls)).encode()
    req = Request(INDEXNOW_ENDPOINT, data=payload,
                  headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urlopen(req, timeout=timeout) as r:
            log(f"IndexNow HTTP {r.status} for {len(urls)} URLs (200/202 = accepted)")
            return r.status
    except urllib.error.HTTPError as e:
        log(f"IndexNow HTTP {e.code} (403 = key file not served at keyLocation yet)")
        return e.code
    except Exception as e:
        log(f"IndexNow FAIL: {type(e).__name__}")
        return 0


def submit_gsc_sitemap(domain, sitemap_url, gsc_key_file) -> bool:
    """Resubmit the sitemap via Google Search Console. Google client is optional."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        log("GSC SKIP: google client not installed (pip install 'indexnow-ping[gsc]')")
        return False
    try:
        creds = service_account.Credentials.from_service_account_file(
            gsc_key_file, scopes=["https://www.googleapis.com/auth/webmasters"])
        build("searchconsole", "v1", credentials=creds).sitemaps().submit(
            siteUrl=f"sc-domain:{domain}", feedpath=sitemap_url).execute()
        log("GSC sitemap resubmit OK")
        return True
    except Exception as e:
        log(f"GSC sitemap resubmit FAIL: {type(e).__name__}: {e}")
        return False


def log(msg: str) -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------------- CLI

def _resolve_key(args) -> str:
    if args.indexnow_key:
        return args.indexnow_key.strip()
    path = args.indexnow_key_file or os.environ.get("INDEXNOW_KEY_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError as exc:
            log(f"IndexNow key file unreadable ({exc}) -> IndexNow disabled")
    return os.environ.get("INDEXNOW_KEY", "").strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Notify search engines of sitemap URLs, with a pre-launch guard.")
    ap.add_argument("domain", help="bare domain, e.g. example.com")
    ap.add_argument("--sitemap", help="sitemap URL (default https://<domain>/sitemap.xml)")
    ap.add_argument("--indexnow-key", help="IndexNow key value (or use --indexnow-key-file)")
    ap.add_argument("--indexnow-key-file", help="path to a file holding the IndexNow key "
                    "(or env INDEXNOW_KEY_FILE / INDEXNOW_KEY)")
    ap.add_argument("--gsc-key-file", default=os.environ.get("GSC_KEY_FILE"),
                    help="Google service-account JSON for GSC sitemap resubmit (optional)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="max URLs to submit")
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--user-agent", default=None)
    ap.add_argument("--force", action="store_true", help="skip the pre-launch robots guard")
    args = ap.parse_args(argv)

    domain = args.domain.strip().lower().rstrip("/")
    if not domain or "/" in domain:
        ap.error("domain must be a bare host like example.com")
    sitemap_url = args.sitemap or f"https://{domain}/sitemap.xml"
    if not same_site(sitemap_url, domain):
        ap.error("--sitemap must be on the target domain")
    ua = args.user_agent or f"indexnow-ping (+https://{domain})"
    fetch = make_fetcher(ua, args.timeout)

    if not args.force and not preflight_guard(fetch, domain):
        return 1  # pre-launch: intentionally non-zero so cron/CI notices

    urls = collect_sitemap_urls(fetch, sitemap_url, domain, limit=args.limit)
    log(f"sitemap URLs on {domain}: {len(urls)}")
    if not urls:
        urls = [f"https://{domain}/"]

    did_any = False
    if args.gsc_key_file:
        did_any |= submit_gsc_sitemap(domain, sitemap_url, args.gsc_key_file)

    key = _resolve_key(args)
    if key:
        status = submit_indexnow(domain, key, urls, args.timeout)
        did_any |= status in (200, 202)
    else:
        log("IndexNow SKIP: no key provided (--indexnow-key / --indexnow-key-file / env)")

    if not did_any:
        log("nothing submitted (no GSC key and no IndexNow key, or both failed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
