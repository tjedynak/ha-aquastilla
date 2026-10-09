# ha-aquastilla

Home Assistant custom integration for the **Aquastilla DUO Smart** water softener
(Waterlife brand, Amii control board, AMC Tech mobile app and cloud backend).

> **Status: not implemented yet.** The repository holds the conventions and the
> layout; the integration itself is still ahead.

## Why this exists

The device has no public API and no existing Home Assistant integration. The
vendor app is cloud-only — it never talks to the device directly, even on the
local network. Everything known about the protocol comes from analysing that app
and probing the device on the owner's own network.

## How it works

Cloud integration against the vendor backend: REST polling for state plus a
WebSocket channel for push updates.

This means the integration depends on the vendor's servers. If they go down or
change the API, entities stop updating — so a connectivity `binary_sensor` is a
first-class entity rather than an afterthought.

Commands are asynchronous: a write reaches the cloud, which relays it to the
device, which acknowledges later over the push channel. Entities model a pending
state rather than assuming the write took effect.

## Scope

Every endpoint whose subject is the **device** is implemented — readings and
configuration alike. Account operations (registration, password reset, social
login, consents, account deletion) are out of scope: they act on the vendor
account rather than the softener, and several require a browser.

Two functions ship deliberately disabled, to be enabled by hand if wanted: the
head-cycle advance and the water shut-off. Home Assistant has no confirmation
dialogs, so a disabled entity is the only real protection against an automation
firing something irreversible.

> **Water shut-off is one-way.** The vendor app can close the flow but states
> plainly that reopening is only possible at the device itself. The integration
> never exposes it as a switch or valve, and never calls it from tests.

### Authentication

Sign-in uses email and password. Google, Apple and Facebook sign-in cannot be
supported: the backend validates a Google ID token whose audience must be the
vendor's own OAuth client, which a third-party integration cannot obtain. If an
account was created through social sign-in, set a password on it via the app's
password-reset flow first.

## Installation

> **Not published yet.** The repository is private. HACS installs only from public
> GitHub repositories, so this route opens once the code is published. The layout is
> already HACS-shaped and checked by tests.

Via [HACS](https://hacs.xyz/) as a custom repository — HACS → Integrations →
custom repositories → add this repository as an Integration, then install and
restart Home Assistant. Configure through Settings → Devices & services → Add
integration → Aquastilla.

Manual installation into `/config/custom_components/` works but has no update
path. HACS is the supported route.

## Development

Read [CLAUDE.md](CLAUDE.md) first — it carries the hard constraints and points at
the project documentation, which lives outside this repository by design.
[CONTRIBUTING.md](CONTRIBUTING.md) covers the issue, branch, commit and pull
request conventions.

Planning documents and reverse-engineering material are deliberately kept out of
the repository: no vendor code is redistributed here, and the repository stays a
clean engineering artefact rather than a record of how it was researched.

## Legal

Reverse engineering performed on the owner's own device, for interoperability.
No vendor code is included in or distributed with this repository.
