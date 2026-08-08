import pytest
from urllib.request import Request
import urllib.error

from indexnow_ping import ping


# ------------------------------------------------------------- same_site()

def test_same_site_exact_and_subdomain():
    assert ping.same_site("https://example.com/a", "example.com")
    assert ping.same_site("https://www.example.com/a", "example.com")
    assert ping.same_site("http://deep.sub.example.com/", "example.com")


def test_same_site_rejects_foreign_lookalike_and_bad_scheme():
    assert not ping.same_site("https://evil.com/", "example.com")
    assert not ping.same_site("https://notexample.com/", "example.com")
    assert not ping.same_site("https://example.com.evil.com/", "example.com")
    assert not ping.same_site("not a url", "example.com")
    assert not ping.same_site("ftp://example.com/x", "example.com")      # scheme
    assert not ping.same_site("httpx://example.com/page", "example.com")  # look-alike scheme


# --------------------------------------------------------- robots guard

def test_robots_blanket_block_aborts_various_whitespace():
    assert ping.robots_allows_launch("User-agent: *\nDisallow: /") is False
    assert ping.robots_allows_launch("User-agent: *\nDisallow:/") is False
    assert ping.robots_allows_launch("User-agent: *\nDisallow:\t/") is False       # tab
    assert ping.robots_allows_launch("User-agent: * # all\nDisallow: /") is False   # inline comment
    assert ping.robots_allows_launch("User-agent: *\nDisallow: / # pre-launch") is False


def test_robots_allows_when_not_blanket():
    assert ping.robots_allows_launch("") is True
    assert ping.robots_allows_launch("User-agent: *\nDisallow: /admin/") is True
    assert ping.robots_allows_launch("User-agent: BadBot\nDisallow: /") is True  # scoped bot


# ----------------------------------------------------- sitemap parsing

URLSET = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc></url>
  <url><loc>https://www.example.com/b</loc></url>
  <url><loc>https://evil.com/x</loc></url>
  <url><loc>ftp://example.com/f</loc></url>
</urlset>"""

INDEX = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
  <sitemap><loc>https://evil.com/sitemap-2.xml</loc></sitemap>
</sitemapindex>"""


def test_extract_locs_and_index_detection():
    assert ping.is_sitemap_index(INDEX) is True
    assert ping.is_sitemap_index(URLSET) is False
    assert ping.extract_locs("not xml <<<") == []


def test_collect_filters_foreign_scheme_and_caps():
    fetch = lambda url: URLSET
    urls = ping.collect_sitemap_urls(fetch, "https://example.com/sitemap.xml", "example.com")
    assert urls == ["https://example.com/a", "https://www.example.com/b"]  # evil + ftp dropped
    capped = ping.collect_sitemap_urls(fetch, "https://example.com/sitemap.xml",
                                       "example.com", limit=1)
    assert capped == ["https://example.com/a"]


def test_collect_follows_sitemap_index_same_site_only():
    pages = {
        "https://example.com/sitemap.xml": INDEX,
        "https://example.com/sitemap-1.xml": URLSET,
        "https://evil.com/sitemap-2.xml": "<urlset><url><loc>https://evil.com/p</loc></url></urlset>",
    }
    fetched = []
    def fetch(url):
        fetched.append(url)
        return pages[url]
    urls = ping.collect_sitemap_urls(fetch, "https://example.com/sitemap.xml", "example.com")
    assert "https://evil.com/sitemap-2.xml" not in fetched
    assert urls == ["https://example.com/a", "https://www.example.com/b"]


def test_collect_index_respects_depth():
    pages = {
        "https://example.com/s.xml": INDEX.replace("sitemap-1.xml", "s2.xml"),
        "https://example.com/s2.xml": INDEX,  # another index at depth 1
    }
    fetch = lambda url: pages.get(url, "<urlset></urlset>")
    urls = ping.collect_sitemap_urls(fetch, "https://example.com/s.xml", "example.com")
    assert urls == []  # index links never submitted as pages, depth budget respected


def test_collect_top_failure_returns_none():
    def boom(url):
        raise OSError("dns")
    assert ping.collect_sitemap_urls(boom, "https://example.com/sitemap.xml", "example.com") is None


def test_collect_child_failure_is_not_fatal():
    idx = ("<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
           "<sitemap><loc>https://example.com/ok.xml</loc></sitemap>"
           "<sitemap><loc>https://example.com/bad.xml</loc></sitemap></sitemapindex>")
    def fetch(url):
        if url.endswith("sitemap.xml"):
            return idx
        if url.endswith("ok.xml"):
            return URLSET
        raise OSError("child down")
    urls = ping.collect_sitemap_urls(fetch, "https://example.com/sitemap.xml", "example.com")
    assert urls == ["https://example.com/a", "https://www.example.com/b"]  # not None


# ------------------------------------------- redirect handler (SSRF / guard)

def test_redirect_handler_blocks_cross_origin():
    h = ping._SameSiteRedirectHandler("example.com")
    with pytest.raises(urllib.error.HTTPError):
        h.redirect_request(Request("https://example.com/robots.txt"), None, 301,
                           "moved", {}, "https://evil.com/x")


def test_redirect_handler_allows_same_site():
    h = ping._SameSiteRedirectHandler("example.com")
    req = h.redirect_request(Request("https://example.com/a"), None, 301,
                             "moved", {}, "https://www.example.com/b")
    assert req is not None


# ------------------------------------------------------- payload / guard IO

def test_build_payload():
    p = ping.build_indexnow_payload("example.com", "KEY123", ["https://example.com/a"])
    assert p["keyLocation"] == "https://example.com/KEY123.txt"
    assert p["urlList"] == ["https://example.com/a"]


def test_preflight_guard_paths():
    assert ping.preflight_guard(lambda u: "User-agent: *\nDisallow: /admin", "example.com") is True
    assert ping.preflight_guard(lambda u: "User-agent: *\nDisallow: /", "example.com") is False
    def boom(u):
        raise OSError("dns")
    assert ping.preflight_guard(boom, "example.com") is False  # unreachable robots = abort


def test_resolve_key_precedence_and_no_env_fallback(tmp_path, monkeypatch):
    kf = tmp_path / "k.txt"
    kf.write_text("FILEKEY\n")
    ns = lambda **kw: type("NS", (), {"indexnow_key": None, "indexnow_key_file": None, **kw})
    monkeypatch.delenv("INDEXNOW_KEY", raising=False)
    monkeypatch.delenv("INDEXNOW_KEY_FILE", raising=False)
    assert ping._resolve_key(ns(indexnow_key="DIRECT")) == "DIRECT"
    assert ping._resolve_key(ns(indexnow_key_file=str(kf))) == "FILEKEY"
    monkeypatch.setenv("INDEXNOW_KEY", "ENVKEY")
    assert ping._resolve_key(ns()) == "ENVKEY"
    # an explicitly chosen but unreadable file must NOT fall back to the env key
    assert ping._resolve_key(ns(indexnow_key_file=str(tmp_path / "missing.txt"))) == ""


def test_submit_indexnow_status(monkeypatch):
    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(ping, "urlopen", lambda req, timeout=None: FakeResp())
    assert ping.submit_indexnow("example.com", "K", ["https://example.com/a"], 5) == 200


# --------------------------------------------------------------- main() flow

def test_main_aborts_on_prelaunch(monkeypatch):
    monkeypatch.setattr(ping, "make_fetcher",
                        lambda *a, **k: (lambda url: "User-agent: *\nDisallow: /"))
    assert ping.main(["example.com", "--indexnow-key", "K"]) == 1


def test_main_force_skips_guard_and_pings(monkeypatch):
    calls = {}
    monkeypatch.setattr(ping, "make_fetcher", lambda *a, **k: (lambda url: ""))
    monkeypatch.setattr(ping, "collect_sitemap_urls", lambda *a, **k: ["https://example.com/a"])
    def fake_submit(host, key, urls, timeout):
        calls["indexnow"] = (host, key, tuple(urls))
        return 200
    monkeypatch.setattr(ping, "submit_indexnow", fake_submit)
    rc = ping.main(["example.com", "--force", "--indexnow-key", "K"])
    assert rc == 0
    assert calls["indexnow"] == ("example.com", "K", ("https://example.com/a",))


def test_main_sitemap_unavailable_does_not_invent_root(monkeypatch):
    called = {"n": False}
    monkeypatch.setattr(ping, "make_fetcher", lambda *a, **k: (lambda url: ""))
    monkeypatch.setattr(ping, "collect_sitemap_urls", lambda *a, **k: None)  # unavailable
    monkeypatch.setattr(ping, "submit_indexnow",
                        lambda *a, **k: called.__setitem__("n", True) or 200)
    rc = ping.main(["example.com", "--force", "--indexnow-key", "K"])
    assert rc == 3            # ran, nothing submitted
    assert called["n"] is False  # never pinged with an invented root URL


def test_main_no_key_skips_indexnow(monkeypatch):
    monkeypatch.setattr(ping, "make_fetcher", lambda *a, **k: (lambda url: ""))
    monkeypatch.setattr(ping, "collect_sitemap_urls", lambda *a, **k: ["https://example.com/a"])
    called = {"n": False}
    monkeypatch.setattr(ping, "submit_indexnow",
                        lambda *a, **k: called.__setitem__("n", True))
    rc = ping.main(["example.com", "--force"])
    assert rc == 3
    assert called["n"] is False


def test_main_rejects_bad_domain():
    with pytest.raises(SystemExit) as e:
        ping.main(["http://example.com/path", "--force"])
    assert e.value.code == 2


def test_main_rejects_negative_limit():
    with pytest.raises(SystemExit) as e:
        ping.main(["example.com", "--force", "--limit", "-1"])
    assert e.value.code == 2
