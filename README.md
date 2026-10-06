# ha-aquastilla

Home Assistant custom integration for the **Aquastilla DUO Smart** water softener
(Waterlife brand, Amii control board, AMC Tech mobile app and cloud backend).

> **Status: planning.** Nothing is implemented yet. This repository currently holds
> the implementation plan, the reverse-engineered API map, and the material needed
> for the code-analysis phase.

## Why this exists

The device has no public API and no existing Home Assistant integration. The
vendor app is cloud-only — it never talks to the device directly, even on the
local network. Everything known about the protocol comes from analysing the
Android app and probing the device.

## How it will work

Cloud integration against `https://backend.waterlife.pl:15880`: REST polling for
state plus a WebSocket channel for push updates. See [docs/plan.md](docs/plan.md).

This means the integration depends on the vendor's servers. If they go down or
change the API, entities stop updating. A connectivity `binary_sensor` is a
first-class entity, not an afterthought.

## Planned entities

Salt level and days remaining, water volume to next regeneration, daily and
monthly consumption with comparative statistics, current and next head cycle,
regeneration history, leak and head-error alarms, firmware update progress —
plus controls for forcing or delaying regeneration, vacation mode, alarm
thresholds and device settings.

The authoritative list is the parity matrix in
[docs/parity-matrix.md](docs/parity-matrix.md), produced by the analysis phase.

## Safety notes

Remote water shut-off is **one-way**: the app can close the flow, but it can only
be reopened physically at the device. The integration will never expose this as a
switch or valve. See [docs/plan.md](docs/plan.md), "Exposure policy".

## Development

Read [CLAUDE.md](CLAUDE.md) first — it carries the working rules, including the
hard constraints around the vendor backend and the water shut-off.

Work proceeds milestone by milestone. The current step is **M-1**: distributed
analysis of the vendor app, described in [analysis/README.md](analysis/README.md).

## Legal

Reverse engineering performed on the owner's own device for interoperability
purposes. No vendor code is redistributed — `analysis/bundle/` is gitignored and
regenerated locally via `scripts/fetch_bundle.sh`.
