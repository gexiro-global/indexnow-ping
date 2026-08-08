#!/usr/bin/env python3
"""indexnow-ping: notify search engines that a site's URLs changed.

Two layers, both optional and independent:
  A. Google Search Console -- resubmit the sitemap (needs a service-account key).
  C. IndexNow -- ping Bing / Yandex / Seznam with the URL list.

Safety feature (the reason this exists): a *launch guard*. Before pinging anything
it reads the target's robots.txt and ABORTS if the site still blocks all crawlers
(`User-agent: *` + `Disallow: /`), so you cannot accidentally announce a
pre-launch/staging site. Override with --force.

Hardening: URLs come only from the site's own sitemap; every URL (and every child
sitemap in a sitemap-index) must be an http(s) URL on the target host before it is
followed or submitted; and HTTP redirects are constrained to the same site, so a
redirecting robots.txt/sitemap cannot turn this into blind SSRF or a guard bypass.

The parsing/validation logic is pure and import-light; network access is confined
to small, injectable functions so the whole thing is testable offline.
"""
import argparse
import json
import os
import sys
import urllib.error
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler, Request, build_opener, urlopen,
)

MAX_FETCH_BYTES = 5_000_000
DEFAULT_LIMIT = 200
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- pure helpers

def _localname(tag: str) -> str:
    """Strip an XML namespace: '{ns}loc' -> 'loc'."""
    return tag.rsplit("}", 1)[-1]


def same_site(url: str, domain: str) -> bool:
    """True if *url* is an http(s) URL whose host is *domain* or a subdomain.

    Requires an http/https scheme (rejects ftp:, httpx:, etc.) and a hostname that
    equals *domain* or ends with '.'+domain -- so 'example.com.evil.com' and bare
    non-URLs are rejected.
    """
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    domain = domain.lower().strip().rstrip(".")
    return bool(host) and (host == domain or host.endswith("." + domain))


def robots_allows_launch(robots_text: str) -> bool:
    """Launch guard: abort when a group covering `*` has `Disallow: /`.

    Models robots.txt grouping: consecutive `User-agent` lines form ONE group, so
    `User-agent: *` + `User-agent: Bot` + `Disallow: /` blocks `*` too. A rule line
    ends the agent list; the next `User-agent` starts a new group. Robust to inline
    comments and arbitrary whitespace. Any other configuration (including none) is
    treated as launched.
    """
    star_group = False      # current group's agents include '*'
    reading_agents = False  # still accumulating User-agent lines for this group
    robots_text = robots_text.lstrip("\ufeff")  # drop a UTF-8 BOM before the first field
    for raw in robots_text.splitlines():
        line = raw.split("#", 1)[0].strip()  # drop inline comments
        if not line or ":" not in line:
            continue
        field, value = line.split(":", 1)
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if not reading_agents:      # a rule closed the previous group -> new one
                star_group = False
                reading_agents = True
            if value == "*":
                star_group = True
        else:
            reading_agents = False      # first rule line fixes this group's agents
            if field == "disallow" and star_group and value == "/":
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

class _SameSiteRedirectHandler(HTTPRedirectHandler):
    """Follow redirects only when they stay on the target site (http/https).

    A cross-origin (or non-web) redirect is turned into an error, so a same-site
    robots.txt/sitemap that 30x-redirects to attacker content cannot smuggle a
    permissive guard result or make us fetch an arbitrary host (SSRF).
    """
    def __init__(self, domain):
        super().__init__()
        self._domain = domain

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not same_site(newurl, self._domain):
            raise urllib.error.HTTPError(
                newurl, code, f"blocked cross-origin/non-web redirect to {newurl}",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def make_fetcher(user_agent: str, timeout: float, domain: str,
                 max_bytes: int = MAX_FETCH_BYTES):
    opener = build_opener(_SameSiteRedirectHandler(domain))

    def fetch(url: str) -> str:
        req = Request(url, headers={"User-Agent": user_agent})
        with opener.open(req, timeout=timeout) as resp:
            return resp.read(max_bytes).decode("utf-8", "replace")
    return fetch


def collect_sitemap_urls(fetch, sitemap_url, domain, limit=DEFAULT_LIMIT, max_depth=1):
    """Return same-site page URLs from a sitemap, or None if the top-level sitemap
    could not be retrieved/parsed.

    Distinguishes a *valid empty* sitemap ([]) from an *unavailable* one (None) so
    the caller never invents URLs on a failed fetch. Follows one level of
    sitemap-index nesting; child sitemaps must be same-site, and an index's <loc>s
    (which are sitemaps, not pages) are never submitted as page URLs.
    """
    out = []
    top_ok = [True]

    def _walk(url, depth):
        if len(out) >= limit:
            return
        try:
            xml = fetch(url)
        except Exception as exc:
            log(f"sitemap fetch FAIL {url} ({type(exc).__name__})")
            if depth == 0:
                top_ok[0] = False
            return
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            log(f"sitemap parse FAIL {url}")
            if depth == 0:  # an unparseable top sitemap is unavailable, not empty
                top_ok[0] = False
            return
        locs = [(e.text or "").strip() for e in root.iter()
                if _localname(e.tag) == "loc" and (e.text or "").strip()]
        if _localname(root.tag) == "sitemapindex":
            if depth < max_depth:
                for child in locs:
                    if same_site(child, domain):
                        _walk(child, depth + 1)
                    if len(out) >= limit:
                        return
            return
        for loc in locs:
            if same_site(loc, domain):
                out.append(loc)
                if len(out) >= limit:
                    return

    _walk(sitemap_url, 0)
    if not top_ok[0]:
        return None
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
        log(f"GSC sitemap resubmit FAIL: {type(e).__name__}")
        return False


# ------------------------------------------------------------------------- CLI

def _resolve_key(args) -> str:
    """Resolve the IndexNow key with strict precedence and no surprising fallback.

    Order: explicit --indexnow-key value, then an explicitly selected key file
    (--indexnow-key-file or INDEXNOW_KEY_FILE), then the INDEXNOW_KEY env value.
    If an explicitly selected file is unreadable we DISABLE IndexNow rather than
    silently falling back to an inherited environment key.
    """
    if args.indexnow_key:
        return args.indexnow_key.strip()
    path = args.indexnow_key_file or os.environ.get("INDEXNOW_KEY_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError as exc:
            log(f"IndexNow key file unreadable ({exc}) -> IndexNow disabled")
            return ""
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
    if args.limit < 0:
        ap.error("--limit must be >= 0")
    sitemap_url = args.sitemap or f"https://{domain}/sitemap.xml"
    if not same_site(sitemap_url, domain):
        ap.error("--sitemap must be an http(s) URL on the target domain")
    ua = args.user_agent or f"indexnow-ping (+https://{domain})"
    fetch = make_fetcher(ua, args.timeout, domain)

    if not args.force and not preflight_guard(fetch, domain):
        return 1  # pre-launch guard aborted

    urls = collect_sitemap_urls(fetch, sitemap_url, domain, limit=args.limit)
    if urls is None:
        # Could not retrieve/parse the sitemap at all -> do not tell any search
        # engine to look at it (neither IndexNow nor a GSC resubmit).
        log("sitemap unavailable -> aborting (no IndexNow, no GSC resubmit)")
        return 3
    log(f"sitemap URLs on {domain}: {len(urls)}")

    did_any = False
    if args.gsc_key_file:
        did_any |= submit_gsc_sitemap(domain, sitemap_url, args.gsc_key_file)

    key = _resolve_key(args)
    if key and urls:
        status = submit_indexnow(domain, key, urls, args.timeout)
        did_any |= status in (200, 202)
    elif key and not urls:
        log("IndexNow SKIP: no sitemap URLs to submit")
    else:
        log("IndexNow SKIP: no key provided (--indexnow-key / --indexnow-key-file / env)")

    if not did_any:
        log("nothing submitted")
        return 3  # ran, but nothing was submitted (misconfig / empty / all failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
