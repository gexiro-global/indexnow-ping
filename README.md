# indexnow-ping

[![CI](https://github.com/gexiro-global/indexnow-ping/actions/workflows/ci.yml/badge.svg)](https://github.com/gexiro-global/indexnow-ping/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/indexnow-ping.svg)](https://pypi.org/project/indexnow-ping/)
[![Python](https://img.shields.io/pypi/pyversions/indexnow-ping.svg)](https://pypi.org/project/indexnow-ping/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Tell the search engines your site changed — **without accidentally announcing a
site that isn't live yet.**

`indexnow-ping` reads a site's own `sitemap.xml`, then submits through IndexNow and optionally Google Search Console:

- **IndexNow** — pings `api.indexnow.org`, which fans out to Bing, Yandex and Seznam.
- **Google Search Console** — resubmits the sitemap via the GSC API.

Before it pings anything it runs a **pre-launch guard**: it fetches `robots.txt`
and aborts if the site still blocks every crawler (`User-agent: *` + `Disallow: /`).
Staging and not-yet-launched sites therefore can't be leaked to the index by a
stray cron run. That guard is the reason this exists.

```bash
pip install indexnow-ping                 # IndexNow only (pure standard library)
pip install "indexnow-ping[gsc]"          # + Google Search Console support
```

## Use

```bash
# IndexNow ping; key read from a file or the environment
indexnow-ping example.com --indexnow-key-file /etc/indexnow/example.key
INDEXNOW_KEY=abc123... indexnow-ping example.com

# also resubmit the sitemap to Google Search Console
indexnow-ping example.com \
  --indexnow-key-file /etc/indexnow/example.key \
  --gsc-key-file /etc/gsc/service-account.json

# a pre-launch site (robots blocks all) exits 1 and pings nothing:
indexnow-ping staging.example.com --indexnow-key K   # -> GUARD: ... abort ; exit 1
#   override deliberately with --force
```

The [IndexNow key](https://www.indexnow.org/documentation) is a token you also
serve at `https://<domain>/<key>.txt`; it is not a secret, but keep it out of your
repo (a `--indexnow-key-file` / `INDEXNOW_KEY` is cleaner than a flag in shell
history). The GSC service-account JSON **is** sensitive — pass a path, never commit it.

### Options

| Flag / env | Meaning |
|---|---|
| `--sitemap URL` | sitemap URL (default `https://<domain>/sitemap.xml`; must be same-site) |
| `--indexnow-key` / `--indexnow-key-file` / `INDEXNOW_KEY` / `INDEXNOW_KEY_FILE` | the IndexNow key |
| `--gsc-key-file` / `GSC_KEY_FILE` | Google service-account JSON (enables the GSC layer) |
| `--limit N` | max URLs to submit (default 200) |
| `--timeout S` | per-request timeout |
| `--force` | skip the pre-launch robots guard |

Exit codes: `0` submitted, `1` pre-launch guard aborted, `2` usage error, `3` ran
but nothing submitted (no key/URLs, unavailable sitemap, or all submissions failed).

## Design notes

- **Guard fails closed.** If `robots.txt` can't be fetched, or blocks all crawlers,
  the run aborts rather than pinging.
- **Same-site only.** Every URL — and every child sitemap in a sitemap-index — is
  checked to belong to the target host before it is followed or submitted, so a
  tampered sitemap can't make the tool ping arbitrary hosts. A sitemap-index is
  followed one level; its child-sitemap links are never mistaken for page URLs.
- **URLs come from the site.** The tool never invents URLs; it submits exactly what
  the sitemap lists (capped by `--limit`). A valid but empty sitemap submits
  nothing, and an *unavailable* sitemap is not treated as an empty one — it aborts
  the IndexNow submission rather than announcing a synthesized URL.
- **Redirects stay on-site.** HTTP redirects for `robots.txt` and sitemaps are
  followed only within the target site; a cross-origin redirect is an error, so a
  redirecting endpoint cannot bypass the guard or point the tool at another host.
- **Optional heavy dep.** IndexNow is standard-library only; the Google client is
  imported lazily and required solely for the GSC layer.

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

The suite injects a fake fetcher and stubs the HTTP calls, so it exercises the
real guard/parsing/same-site/flow logic with no network access.

## License

[Apache-2.0](LICENSE).

Built and maintained by [Gexiro Global Enterprises Ltd](https://gexiro.com).

Third-party service and library attribution: see [NOTICE](NOTICE).

Part of the [Gexiro open-source toolkit](https://github.com/gexiro-global).
