# Security Policy

## Supported Versions

indexnow-ping 0.x is experimental. Security fixes are handled on the latest 0.x
release line.

## Reporting a Vulnerability

If GitHub private vulnerability reporting is enabled for this repository, use it.
Otherwise, email `admin@gexiro.com`.

Please include:

- A concise description of the issue and its impact.
- A minimal reproduction (a crafted robots.txt / sitemap / redirect and the exact
  command).
- The package version and your Python version.

This tool is designed to fail closed and to stay same-site. The most valuable
reports show a way to bypass the pre-launch guard, or to make the tool fetch or
submit a URL on a host other than the target. Do not include secrets (e.g. a GSC
service-account JSON) in reports.
