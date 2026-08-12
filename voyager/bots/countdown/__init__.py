"""Countdown bot — event-driven trigger from Clearance resolved verdicts."""

from .routing import COUNTDOWN_AGENT_SLUG, route_countdown_trigger

__all__ = [
    "COUNTDOWN_AGENT_SLUG",
    "route_countdown_trigger",
]
