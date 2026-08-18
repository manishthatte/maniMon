"""Privileged sensor reading, and the unprivileged side that consumes it.

`daemon` runs as root on a timer and publishes JSON; `published` reads it back.
Nothing in the panels ever needs privileges of its own.
"""
