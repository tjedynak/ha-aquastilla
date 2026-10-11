# Fixtures

Two kinds of files live here. The JSON files in this directory are **synthetic**. The shapes follow the static
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

## Captured

`captured/` holds responses of a real device, recorded in one read-only session
(a login, GET requests and a minute of WebSocket listening) and anonymised:

- identifiers are placeholders (object ids are zeros, UUID-like ids start with `00000000-`),
  the address and MAC are in the documentation ranges, the coordinates are zero and the
  free text is neutral;
- vendor product names stay, they are public;
- the **values are invented, round numbers**: only the shapes are real. Real readings and
  event times say when someone was on site and how much water a house used;
- dates, keys included, sit in an invented window in summer, which keeps the two-hour
  lead the device clock has on UTC in that season. A test fails for a date outside it;
- the login response is stood in for by a placeholder token.

What the capture showed that the synthetic files get wrong or leave out:

- the device clock, the expected regeneration and the history are local time labelled `+00:00`;
- four water series (`WATER_HOUR_DATA`, `WATER_DAY_DATA`, `WATER_MONTH_DATA`,
  `WATER_12_MONTH_DATA`), no `FLOW_*` series, and the year endpoint answers with one series
  under a date;
- the mode list has `deviceStateVacation`, `deviceStateClosed` and `deviceStateNum`;
- push timestamps are epoch seconds, `params` is text, the authenticate reply is
  `/authenticate/response` and a ping has a `null` body.

The synthetic files were brought to the same structure, with invented values, so tests
that depend on particular numbers keep working.
