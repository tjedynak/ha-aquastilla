## What and why

## How it was tested

- [ ] `uv run pytest`, `uv run ruff check .`, `uv run mypy`
- [ ] No token, e-mail, serial number, device id, MAC or IP address in the change or in a fixture
- [ ] Nothing here calls an endpoint that cannot be undone (closing the water flow, remote service, pairing, removal)
