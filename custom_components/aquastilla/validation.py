"""The schema library of the Home Assistant that is running.

Home Assistant replaced ``voluptuous`` with ``probatio`` in 2026.10. The two have the
same API for what this integration uses, so the one that is installed is picked here
and the rest of the code imports ``vol`` from this module.
"""

try:
    import probatio as vol
except ImportError:  # pragma: no cover  # Home Assistant before 2026.10
    import voluptuous as vol  # type: ignore[no-redef,import-not-found,unused-ignore]

__all__ = ["vol"]
