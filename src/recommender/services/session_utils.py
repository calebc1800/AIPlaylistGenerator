"""Shared helpers for managing session metadata in views."""

from __future__ import annotations


def ensure_session_key(request) -> str:
    """Ensure the request has a session key and return it."""
    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key or ""
    return session_key


def resolve_request_user_id(request) -> str:
    """Return a stable identifier for the current user/session."""
    if request.user.is_authenticated:
        return str(request.user.pk)
    return str(request.session.get("spotify_user_id") or "anonymous")
