"""Constants shared across the Aquastilla integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "aquastilla"

# The backend is the vendor's production infrastructure, so polling is
# deliberately conservative. Fresher data cannot be had by asking more often.
DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=5)
MIN_SCAN_INTERVAL: Final = timedelta(minutes=1)

BASE_URL: Final = "https://backend.waterlife.pl:15880"
WS_URL: Final = "wss://backend.waterlife.pl:15880/websocket"

# Identifies the white-label build to the account endpoints. The vendor app has it
# compiled in as a constant and never reads it from the server, so a third-party
# client has to send the same value. It is a public identifier, not a secret.
COMPANY_ID: Final = "67adc541461d62305c8168c8"

# Applies to every request, login included. The vendor app sets no timeout of its own.
REQUEST_TIMEOUT: Final = 30.0

CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"  # noqa: S105  # config key name, not a secret
# Options: the poll interval in minutes.
CONF_SCAN_INTERVAL: Final = "scan_interval"
MAX_SCAN_INTERVAL: Final = timedelta(hours=1)

# Polling budget. The state interval is the one the user sets (DEFAULT_SCAN_INTERVAL
# and up); the rest change rarely, so they are fetched far less often.
DEVICE_LIST_INTERVAL: Final = timedelta(hours=1)
SETTINGS_INTERVAL: Final = timedelta(minutes=30)
# The consumption series are hourly buckets, so asking more often adds nothing.
WATER_DATA_INTERVAL: Final = timedelta(hours=1)
# After failures the poll interval doubles, up to this cap, and resets on success.
MAX_BACKOFF: Final = timedelta(hours=1)
# A push from the device triggers a targeted refresh, but never more often than this
# per device and dataset, so a burst of pushes cannot become a burst of requests.
PUSH_REFRESH_COOLDOWN: Final = timedelta(seconds=10)

# WebSocket push channel.
TOPIC_EVENT: Final = "/device/event"
TOPIC_EVENT_SEEN: Final = "/device/event_seen"
TOPIC_PERCENTAGE: Final = "/device/percentage"
# The vendor app waits ten seconds for a reply, then reconnects after one second, with no
# backoff and no limit. We use the same ack timeout and back off instead.
WS_ACK_TIMEOUT: Final = 10.0
WS_INITIAL_DELAY: Final = 1.0
WS_MAX_DELAY: Final = 300.0
# A connection that lasted this long counts as healthy: the backoff starts over.
WS_STABLE_AFTER: Final = 60.0
# The service pings about once a minute (seen on a real device). Two and a half
# intervals without any frame mean the connection is dead, though the socket looks open.
WS_IDLE_TIMEOUT: Final = 150.0

# How long a command may wait for the device's acknowledgement before it counts as
# unconfirmed. The vendor app has no timeout at all and lets a control stay disabled for
# up to ten minutes. The value is a guess until real traffic shows the usual latency.
COMMAND_TIMEOUT: Final = 90.0
