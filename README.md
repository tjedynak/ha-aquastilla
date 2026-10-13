# ha-aquastilla

Home Assistant custom integration for the **Aquastilla DUO Smart** water softener
(Waterlife brand, Amii control board, AMC Tech mobile app and cloud backend).

> **Status: feature complete, 0.9.** Everything below exists and is covered by tests.
> It runs on one real device: all readings, statistics, notifications, the vacation
> mode switch, the salt threshold and the leak alarm path were checked there. The
> other controls follow the vendor app's payloads exactly but have not all been
> exercised on a device yet (see Limitations).

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

## Requirements

Home Assistant **2026.8.3 or newer**. CI runs the whole suite on 2026.8.3 as well as on the
current release the project is locked to.

## Configuration

Add the integration (Settings, Devices & services, Add integration, Aquastilla) and
sign in with the e-mail and password of the vendor app.

Options (the cog on the integration):

| Option | Default | What it does |
|---|---|---|
| Poll interval | 5 minutes | How often the state is read, 1 to 60 minutes. The backend belongs to the vendor, so asking more often does not give fresher data. Other data is read less often: settings every 30 minutes, the device list and consumption every hour. |
| Device management | off | Registers the `pair_device` and `delete_device` actions. Both cannot be undone. |

## What you get

One device per softener on the account. Entities marked *off* are disabled when
the integration is added and have to be enabled by hand.

| Platform | Key | What it is | Default |
|---|---|---|---|
| `sensor` | `salt_level` | Salt level in percent | on |
| `sensor` | `salt_days_left` | Days of salt left | on |
| `sensor` | `water_left` | Water until the next regeneration | on |
| `sensor` | `water_left_max` | Regeneration capacity | off |
| `sensor` | `cycle` | Cycle the head is in | on |
| `sensor` | `next_cycle` | Cycle the head moves to next | on |
| `sensor` | `last_regeneration` | Time of the last regeneration | on |
| `sensor` | `next_regeneration` | Expected time of the next one | on |
| `sensor` | `last_salt_refill` | Time of the last salt refill | on |
| `sensor` | `last_inspection` | Time of the last inspection | on |
| `sensor` | `last_leak` | Time of the last leak alarm | on |
| `sensor` | `last_head_error` | Time of the last head error | on |
| `sensor` | `water_today` | Water used today, in the device's volume unit | on |
| `sensor` | `water_31_days` | Water used over the last 31 days | on |
| `sensor` | `water_hardness` | Configured water hardness (read only) | on |
| `sensor` | `device_clock` | The device's own clock | on |
| `sensor` | `last_update` | When the data was last confirmed | on |
| `sensor` | `ip_address` | Address of the device | off |
| `sensor` | `firmware_update_progress` | Update progress, 0 when idle | on |
| `sensor` | `last_notification` | Newest vendor notification, recent ones as attributes | on |
| `sensor` | `regeneration_progress` | Regeneration progress, only meaningful while one runs | on |
| `sensor` | `remote_service_end` | When the vendor's remote service access ends | on |
| `sensor` | `unread_notifications` | Notifications not yet seen in the vendor app | off |
| `binary_sensor` | `firmware_updating` | A firmware update is running | on |
| `binary_sensor` | `remote_service` | The vendor's remote service access is active | on |
| `binary_sensor` | `cloud_connection` | The push channel is up | off |
| `binary_sensor` | `online` | The device is connected to the cloud | on |
| `binary_sensor` | `regenerating` | A regeneration is running | on |
| `binary_sensor` | `water_flow` | The water flow is open | on |
| `binary_sensor` | `salt_low` | Salt is at or below the alarm level | on |
| `binary_sensor` | `leak` | A leak alarm in the last day | on |
| `binary_sensor` | `head_error` | A head error in the last day | on |
| `event` | `notification` | Fires for each vendor notification, by type | on |
| `button` | `force_regeneration` | Start a regeneration now | off |
| `button` | `delay_regeneration` | Postpone the regeneration by 48 hours | off |
| `button` | `advance_cycle` | Move the head to the next cycle at once | off |
| `button` | `close_water_flow` | Close the water flow. Cannot be undone remotely | off |
| `switch` | `vacation_mode` | Vacation mode | on |
| `switch` | `default_device` | Default device in the vendor app | off |
| `switch` | `salt_alarm_push` | Vendor push notification for the salt alarm | off |
| `switch` | `salt_alarm_email` | Vendor e-mail for the salt alarm | off |
| `switch` | `flood_alarm_push` | Vendor push notification for the leak alarm | off |
| `switch` | `flood_alarm_email` | Vendor e-mail for the leak alarm | off |
| `number` | `salt_alarm_level` | Salt alarm threshold, 1 to 99 % | on |
| `number` | `leak_continuous_flow_time` | Leak alarm: continuous flow, 1 to 14400 min | off |
| `number` | `leak_single_outflow` | Leak alarm: single outflow, 1 to 10000 l | off |
| `number` | `leak_max_flow` | Leak alarm: maximum flow, 1 to 500 l/min | off |
| `time` | `regeneration_time` | Preferred regeneration time | off |
| `select` | `volume_unit` | Litres or cubic metres | off |
| `text` | `note` | Note on the device | off |

Write entities stay available when the device cannot take a command. Their
`blocked_reason` attribute says why, and calling them raises a readable error.

### Statistics

Consumption is imported into long-term statistics as `aquastilla:<device>_water`,
always in litres, with a running total. Add it to the water section of the
energy dashboard. The history comes from the device's hourly, daily and monthly
series and the previous year, once, on the first import. A gap in the device's
data stays a gap.

### Service actions

| Action | Needs | What it does |
|---|---|---|
| `aquastilla.rename_device` | device, name | Renames the device at the vendor |
| `aquastilla.start_remote_service` | device, hours (1 to 23), `confirm: true` | Gives the vendor's service access for a number of hours. Owner controls are locked meanwhile |
| `aquastilla.end_remote_service` | device | Ends that access |
| `aquastilla.pair_device` | account, code, `confirm: true` | Attaches a device. Only with device management on |
| `aquastilla.delete_device` | device, `confirm: true` | Removes a device and its statistics. Cannot be undone. Only with device management on |

### Events

`event.<device>_notification` fires once per vendor notification with the type
as the event type: `normal`, `critical`, `forced_by_service`, `forced_by_user`
or `other`. Filter on the type, not on the text. The attributes carry `title`,
`body`, `date` and `event_id`. Notifications are only read: nothing is marked as
seen or hidden, so the phone app keeps its own unread count.

## Limitations

- **Cloud only.** There is no local protocol. When the vendor's servers are
  down, entities become unavailable and come back by themselves.
- **Commands are confirmed by the device, not by the cloud.** A command that is
  not confirmed within 90 seconds is logged as unconfirmed and the data is read
  again, so entities show what the device reports. It may still have worked.
- **An offline device refuses** advancing the cycle, closing the water and
  forcing a regeneration: the cloud would keep the command and run it whenever
  the device comes back.
- **Alarms are derived.** The service has no "alarm is active" field. Salt low
  compares the level with your threshold; leak and head error are on for a day
  after the matching event in the device history.
- **Hardness is read only** and a leak threshold cannot be set to 0, until the
  backend's limits are verified.
- **Writes not yet exercised on a device:** the payloads follow the vendor app
  exactly and are tested against a fake backend. Verified on a real device so far:
  the vacation mode switch and the salt threshold.

## Installation

Via [HACS](https://hacs.xyz/) as a custom repository: HACS, the three dots, Custom
repositories, add `https://github.com/tjedynak/ha-aquastilla` as an Integration, then
install and restart Home Assistant. Configure through Settings, Devices & services,
Add integration, Aquastilla.

Manual installation (copy `custom_components/aquastilla` into your configuration's
`custom_components` folder) works but has no update path.

## Development

[CONTRIBUTING.md](CONTRIBUTING.md) covers the setup, the tests and the safety rules.
No vendor code is redistributed here, and captured responses are anonymised before
they are committed.

## Legal

Reverse engineering performed on the owner's own device, for interoperability.
No vendor code is included in or distributed with this repository.
