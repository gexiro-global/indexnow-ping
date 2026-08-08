"""indexnow-ping: notify search engines of sitemap URLs, with a pre-launch guard.

The robots.txt launch guard, sitemap parsing (including one level of sitemap-index
nesting), and same-site URL validation are pure and testable without network
access; the Google client for the optional GSC layer is imported lazily.
"""

__version__ = "0.1.0"
