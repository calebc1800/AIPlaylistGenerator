"""Artist recommendations derived from cached Spotify profile snapshots."""

from __future__ import annotations

from typing import Dict, List, Sequence

from django.conf import settings
from django.core.cache import cache

from .artist_card_utils import build_artist_card


DEFAULT_RECOMMENDATION_LIMIT = getattr(
    settings,
    "RECOMMENDER_RECOMMENDATION_LIMIT",
    10,
)
SEED_ARTIST_LIMIT = getattr(
    settings,
    "RECOMMENDER_SEED_ARTIST_LIMIT",
    12,
)


def _profile_cache_key(user_identifier: str) -> str:
    return f"recommender:user-profile:{user_identifier}"


def _load_profile_cache(user_identifier: str) -> Dict[str, object] | None:
    if not user_identifier:
        return None
    cached = cache.get(_profile_cache_key(user_identifier))
    return cached if isinstance(cached, dict) else None


def fetch_seed_artists(
    user_identifier: str,
    *,
    limit: int = SEED_ARTIST_LIMIT,
) -> Sequence[Dict[str, object]]:
    """Return the user's most-heard artists from the cached profile snapshot."""
    profile_cache = _load_profile_cache(user_identifier)
    if not profile_cache or limit <= 0:
        return []
    artists = profile_cache.get("artists")
    if not isinstance(artists, dict) or not artists:
        return []

    ranked = sorted(
        (
            artist
            for artist in artists.values()
            if isinstance(artist, dict) and artist.get("id") and artist.get("name")
        ),
        key=lambda entry: (
            int(entry.get("play_count") or 0),
            int(entry.get("popularity") or 0),
        ),
        reverse=True,
    )
    return ranked[:limit]


def _genre_weights(profile_cache: Dict[str, object]) -> Dict[str, int]:
    """Return a mapping of genre → track density for scoring."""
    buckets = profile_cache.get("genre_buckets")
    if not isinstance(buckets, dict):
        return {}
    weights: Dict[str, int] = {}
    for genre, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        weight = int(bucket.get("track_count") or len(bucket.get("track_ids") or []))
        if genre and weight:
            weights[genre] = weight
    return weights


def _score_artist(
    artist: Dict[str, object],
    *,
    genre_weights: Dict[str, int],
) -> tuple[float, str]:
    """Return a recommendation score and description for the given artist."""
    play_count = int(artist.get("play_count") or 0)
    popularity = int(artist.get("popularity") or 0)
    genres = artist.get("genres") or []
    primary_genre = genres[0] if genres else ""
    genre_weight = genre_weights.get(primary_genre, 0)

    score = play_count * 2 + popularity + genre_weight
    if not score:
        score = popularity or genre_weight or 1

    if primary_genre:
        reason = f"Heavily featured in your {primary_genre.replace('-', ' ')} listening"
    elif play_count:
        reason = "Frequently appears in your recent listening"
    else:
        reason = "Discovered from your recent tracks"
    return score, reason


def generate_recommended_artists(
    user_identifier: str,
    *,
    _sp=None,  # kept for backwards compatibility; not used.
    limit: int = DEFAULT_RECOMMENDATION_LIMIT,
) -> List[Dict[str, object]]:
    """Generate artist recommendations using cached listening history only."""
    profile_cache = _load_profile_cache(user_identifier)
    if not profile_cache or limit <= 0:
        return []

    artists = profile_cache.get("artists")
    if not isinstance(artists, dict) or not artists:
        return []

    genre_weights = _genre_weights(profile_cache)
    recommendations: List[Dict[str, object]] = []

    for artist in artists.values():
        if not isinstance(artist, dict):
            continue
        artist_id = artist.get("id")
        name = artist.get("name")
        if not artist_id or not name:
            continue
        score, reason = _score_artist(artist, genre_weights=genre_weights)
        recommendations.append(
            build_artist_card(
                artist,
                reason=reason,
                score=score,
                seed_artist_ids=[artist_id],
            )
        )

    recommendations.sort(
        key=lambda entry: (
            entry.get("score", 0),
            entry.get("popularity", 0),
            entry.get("play_count", 0),
        ),
        reverse=True,
    )
    return recommendations[:limit]
