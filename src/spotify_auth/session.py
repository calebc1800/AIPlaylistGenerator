"""Utilities for storing and refreshing Spotify OAuth tokens in the session."""

import logging
import time
from typing import Any, MutableMapping, Optional, Tuple

import requests
from django.conf import settings
from requests import RequestException

logger = logging.getLogger(__name__)

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_HTTP_TIMEOUT = int(getattr(settings, "SPOTIFY_HTTP_TIMEOUT", 15))
TOKEN_EXPIRY_LEEWAY_SECONDS = 60

_ACCESS_TOKEN_KEY = "spotify_access_token"
_REFRESH_TOKEN_KEY = "spotify_refresh_token"
_EXPIRES_IN_KEY = "spotify_expires_in"
_EXPIRES_AT_KEY = "spotify_token_expires_at"


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _token_is_expired(expires_at: Optional[int], *, now: Optional[float] = None) -> bool:
    if not expires_at:
        return False
    current_time = now or time.time()
    return current_time >= (expires_at - TOKEN_EXPIRY_LEEWAY_SECONDS)


def store_token(session: MutableMapping[str, Any], token_data: MutableMapping[str, Any]) -> None:
    """
    Persist Spotify token details into the user's session.

    Args:
        session: The Django session-like mapping to store values in.
        token_data: Token payload returned from Spotify.
    """
    access_token = token_data.get("access_token")
    if access_token:
        session[_ACCESS_TOKEN_KEY] = access_token

    refresh_token = token_data.get("refresh_token")
    if refresh_token:
        session[_REFRESH_TOKEN_KEY] = refresh_token

    expires_in = _coerce_int(token_data.get("expires_in"))
    if expires_in is not None:
        session[_EXPIRES_IN_KEY] = expires_in
        session[_EXPIRES_AT_KEY] = int(time.time() + expires_in)


def clear_spotify_session(session: MutableMapping[str, Any]) -> None:
    """Remove Spotify authentication details from the session."""
    for key in (_ACCESS_TOKEN_KEY, _REFRESH_TOKEN_KEY, _EXPIRES_IN_KEY, _EXPIRES_AT_KEY):
        if key in session:
            session.pop(key, None)

    for key in ("spotify_user_id", "spotify_display_name"):
        if key in session:
            session.pop(key, None)


def has_valid_token(session: MutableMapping[str, Any], *, now: Optional[float] = None) -> bool:
    """Return True if the session holds a non-expired Spotify access token."""
    if not session.get(_ACCESS_TOKEN_KEY):
        return False
    expires_at = _coerce_int(session.get(_EXPIRES_AT_KEY))
    if expires_at is None:
        return True
    return not _token_is_expired(expires_at, now=now)


def refresh_access_token(
    session: MutableMapping[str, Any],
) -> Tuple[bool, Optional[str]]:
    """Attempt to refresh the Spotify access token stored in the session.

    Returns:
        Tuple of (success flag, error reason). When unsuccessful, reason is one of:
        - "missing_refresh_token": session does not contain a refresh token.
        - "network": Spotify could not be reached.
        - "bad_response": Spotify rejected the refresh request.
    """
    refresh_token = session.get(_REFRESH_TOKEN_KEY)
    if not refresh_token:
        return False, "missing_refresh_token"

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.SPOTIFY_CLIENT_ID,
        "client_secret": settings.SPOTIFY_CLIENT_SECRET,
    }

    try:
        response = requests.post(SPOTIFY_TOKEN_URL, data=data, timeout=SPOTIFY_HTTP_TIMEOUT)
    except RequestException as exc:
        logger.warning("Spotify token refresh failed due to network error: %s", exc)
        return False, "network"

    if response.status_code != 200:
        logger.info("Spotify token refresh failed with status %s", response.status_code)
        return False, "bad_response"

    token_payload = response.json()
    store_token(session, token_payload)
    return bool(session.get(_ACCESS_TOKEN_KEY)), None


def ensure_valid_spotify_session(request) -> bool:
    """
    Ensure the provided request has a valid Spotify access token.

    Returns:
        True when a valid (possibly refreshed) token is available, False otherwise.
    """
    session = request.session
    if has_valid_token(session):
        return True

    refreshed, _ = refresh_access_token(session)
    if refreshed and has_valid_token(session):
        return True
    return False
