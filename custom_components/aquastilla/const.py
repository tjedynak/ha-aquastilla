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
