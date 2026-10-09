# Synthetic fixtures

Everything in this directory is **synthetic**. The shapes follow the static
analysis of the vendor app; the values are invented. None of it was captured
from the vendor backend, and none of it describes a real device or account.

They exist so the client, models, coordinator and entities can be built and
tested without any traffic to the vendor backend. Real captures replace or
confirm them in the reconciliation story (#63).

Conventions that `tests/test_fixtures.py` enforces:

- identifiers are placeholders: UUIDs start with `00000000-`, e-mail addresses
  end in `@example.test`, IPs are in `192.0.2.0/24` and MACs in
  `00:00:5E:00:53:xx` (the ranges reserved for documentation)
- no JWT-shaped string appears anywhere
- the files are valid JSON

Known guesses, to be settled by real captures:

- the unit of `waterLeft` and of the `WATER_*` series (litres or cubic metres)
- the format of `/mode/all` values compared with `/current_states`
- the key format of `/year/water_data`
- the timestamp format of events
- the body of successful `/authenticate` replies and of command responses

Files named `ws_*.json` hold the decoded body of a push frame. The backend sends
the body as a JSON-encoded string, and the fake server does the same.
