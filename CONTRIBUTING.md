# Contributing

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Run tests

```bash
pytest -q
```

The suite is fully offline: network access is confined to small injectable
functions, and tests pass a fake fetcher / stub the HTTP calls. Keep new logic in
pure functions (parsing, same-site validation, the guard) so it stays testable
without the network.

## Style & principles

- **Fail closed and stay same-site.** The pre-launch guard must abort on an
  unreachable/broken robots.txt; every fetched URL (and redirect hop) must be an
  http(s) URL on the target host. New behaviour that could bypass either needs a
  regression test.
- The tool never invents URLs — it submits what the sitemap lists.
- Match the surrounding code; keep functions small.
