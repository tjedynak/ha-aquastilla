# Contributing

Thank you for looking at this integration. It talks to a vendor's production cloud
and to a device that sits on a water line, so a few rules are stricter than usual.

## Setting up

```bash
uv sync --frozen --python 3.14
uv run pre-commit install
uv run pytest            # the whole suite, with the coverage gate
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

The suite is offline. It runs against recorded, anonymised and synthetic fixtures
and a fake backend on loopback (`tests/fake_backend.py`).

## Home Assistant versions

`hacs.json` names the oldest supported Home Assistant. CI runs the suite on the
locked release and on that oldest version. A call that exists only in a newer
release has to be guarded the way `custom_components/aquastilla/validation.py` does
it for the schema library.

## Branches, commits and pull requests

- Branch from `main`: `<type>/<short-description>`, for example `fix/leak-window`.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/):
  `<type>(<scope>): <subject>` with the type one of `feat`, `fix`, `docs`, `style`,
  `refactor`, `perf`, `test`, `build`, `ci`, `chore`, and a subject of at most 72
  characters. The `commit-msg` hook checks it.
- Keep commits logical: a refactor and the feature that needs it are two commits.
- A pull request says what changed and why, and how it was tested.

## Tests

- New behaviour comes with tests; the coverage gate is 100 %.
- Tests never reach the vendor's backend, and CI never does. That is enforced by
  `tests/test_no_live_traffic.py`.
- Endpoints that cannot be undone (closing the water flow, remote service access,
  pairing and removing a device) are never called by a test. The fake backend refuses
  them, and tests for the code that would call them replace the transport.
- Fixtures contain no token, e-mail address, serial number, device id, MAC address
  or IP address. `tests/test_fixtures.py` checks it. Captured responses are
  anonymised before they are committed, and the raw ones stay out of the repository.

## Safety

- The water shut-off is a button, off by default, never a switch or a valve. The
  device can only be opened again by hand at the device.
- Commands are asynchronous: the device confirms with a push, so entities show what
  the device reports and never what was asked for.
- Poll conservatively. The default is five minutes, and asking more often does not
  give fresher data.
- Do not commit secrets or personal data, and do not add code or documents of the
  vendor's app. The repository holds the integration, its tests and its documentation.
