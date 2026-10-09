"""Constants shared across the Aquastilla integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "aquastilla"

# The backend is the vendor's production infrastructure, so polling is
# deliberately conservative. Fresher data cannot be had by asking more often.
DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=5)
MIN_SCAN_INTERVAL: Final = timedelta(minutes=1)
