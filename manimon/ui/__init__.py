"""
GTK front end: palette, widgets, the docked window base, and the two panels.

Importing this package is cheap — it deliberately pulls in no GTK. Only the
panel modules themselves do that, so `manimon report` on a headless box never
touches a display.
"""

from .launcher import launch, start, stop, status, restart, ensure

__all__ = ['launch', 'start', 'stop', 'status', 'restart', 'ensure']
