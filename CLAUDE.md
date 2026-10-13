# Notes for coding agents

Home Assistant custom integration for the **Aquastilla DUO Smart** water softener
(Waterlife brand, Amii control board, AMC Tech app and cloud backend). Read
[CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and [README.md](README.md) for
what the integration does.

## Hard rules

- **Closing the water flow cannot be undone remotely.** `POST /device/{id}/water_flow`
  closes it; only a person at the device can open it again. It is a `button`, off by
  default, never a `switch` or a `valve`, and no test ever calls the endpoint.
- **Do not query the vendor's backend** (`backend.waterlife.pl`) without the owner's
  explicit consent at that moment. It is production infrastructure. No traffic from CI.
- **Poll conservatively.** Five minutes by default.
- **Commands are asynchronous.** The device confirms with a WebSocket push. Never
  assume a state change, and never show a state that was only requested.
- **Sign-in is e-mail and password only.** Social sign-in cannot be reproduced.
- **No secrets, no personal data, no vendor code** in the repository. Fixtures are
  anonymised: no token, e-mail, serial number, device id, MAC or IP address.

## Working here

- `uv sync --frozen --python 3.14`, then `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format .`, `uv run mypy`. The pre-commit hooks run the same checks.
- Supports Home Assistant 2026.8.3 and newer; CI runs both ends.
- Commit messages: Conventional Commits in English, logical commits, no attribution
  trailers.
- Private notes, hostnames and paths of a local setup go in `CLAUDE.local.md`, which
  is ignored by git.
