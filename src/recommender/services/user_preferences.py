from dataclasses import dataclass
from typing import List, Optional, Dict

from django.conf import settings


@dataclass
class UserPlaylistPreferences:
    """
    Placeholder container for user-level playlist settings.

    These values are currently sourced from project defaults and will be
    overridden once the user settings UI is implemented.
    """

    track_count: int
    enforce_unique_tracks: bool
    allow_seed_only_playlists: bool

    @property
    def is_customized(self) -> bool:
        """Flag indicating whether preferences deviate from defaults."""
        return False


def _clamp_track_count(value: int) -> int:
    lower = getattr(settings, "RECOMMENDER_MIN_PLAYLIST_LENGTH", 1)
    upper = getattr(settings, "RECOMMENDER_MAX_PLAYLIST_LENGTH", 50)
    return max(lower, min(upper, value))


def get_default_preferences() -> UserPlaylistPreferences:
    """
    Return application-level defaults for playlist preferences.

    This is meant to be shared by both anonymous sessions and authenticated
    users until individualized settings are wired up.
    """
    return UserPlaylistPreferences(
        track_count=_clamp_track_count(
            getattr(settings, "RECOMMENDER_DEFAULT_PLAYLIST_LENGTH", 20)
        ),
        enforce_unique_tracks=settings.RECOMMENDER_EXPERIMENTAL_FLAGS.get(
            "enforce_unique_tracks", True
        ),
        allow_seed_only_playlists=settings.RECOMMENDER_EXPERIMENTAL_FLAGS.get(
            "allow_seed_only_playlists", False
        ),
    )


def get_preferences_for_request(request) -> UserPlaylistPreferences:
    """
    Placeholder hook for retrieving user preferences from the request.

    For now this simply returns the defaults. When the settings page is built,
    this is the seam where per-user overrides (session, profile, etc.) should
    be applied.
    """
    _ = request  # reserved for future use
    return get_default_preferences()


def describe_pending_options() -> List[Dict[str, Optional[str]]]:
    """
    Provide a lightweight manifest of customizable fields for future UIs.

    This allows the settings dashboard to be scaffolded before backing logic
    is implemented. Each value is intentionally descriptive only.
    """
    return [
        {
            "key": "track_count",
            "label": "Track Count",
            "description": "Number of tracks to include in generated playlists.",
        },
        {
            "key": "enforce_unique_tracks",
            "label": "Enforce Unique Tracks",
            "description": "Prevent duplicate songs across the generated list.",
        },
        {
            "key": "allow_seed_only_playlists",
            "label": "Allow Seed-Only Playlists",
            "description": "Permit saving playlists comprised solely of seed tracks.",
        },
    ]
