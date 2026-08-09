# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- No unreleased changes.

## [0.1.0] - 2026-08-08

### Added
- `indexnow-ping` CLI: submit a site's sitemap URLs via IndexNow and, optionally,
  resubmit the sitemap to Google Search Console.
- Pre-launch **robots.txt guard** (fails closed; models user-agent groups; handles
  comments, whitespace, and a UTF-8 BOM).
- Same-site enforcement for the sitemap, sitemap-index children, page URLs, and HTTP
  redirect hops (anti-SSRF); http(s)-only.
- An unavailable/unparseable sitemap aborts rather than inventing a root URL.
- Fully offline test suite and pinned-action CI.

[Unreleased]: https://github.com/gexiro-global/indexnow-ping/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gexiro-global/indexnow-ping/releases/tag/v0.1.0
