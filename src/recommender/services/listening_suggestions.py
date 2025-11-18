"""Helpers for building dashboard listening suggestion prompts."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .stats_service import get_genre_breakdown, summarize_generation_stats

GenreEntry = Dict[str, object]
ProfileCache = Optional[Dict[str, object]]


def _format_genre_label(value: str | None) -> str:
    """Return a human-friendly label for stored genre keys."""
    if not value:
        return ""
    cleaned = str(value).replace("-", " ").strip()
    return cleaned.title() if cleaned else ""


def _merge_unique(items: Iterable[str]) -> List[str]:
    """Deduplicate items while preserving order."""
    seen: set[str] = set()
    merged: List[str] = []
    for item in items:
        label = item.strip()
        lowered = label.lower()
        if not label or lowered in seen:
            continue
        seen.add(lowered)
        merged.append(label)
    return merged


def _top_genres_from_profile(profile_cache: ProfileCache, limit: int = 5) -> List[str]:
    """Pull the highest volume genres from the cached Spotify snapshot."""
    if not profile_cache:
        return []

    genre_buckets = profile_cache.get("genre_buckets")
    if not isinstance(genre_buckets, dict):
        return []

    ranked: List[tuple[str, int]] = []
    for genre, bucket in genre_buckets.items():
        if not isinstance(bucket, dict):
            continue
        weight = int(bucket.get("track_count") or len(bucket.get("track_ids") or []))
        ranked.append((genre, weight))

    ranked.sort(key=lambda item: item[1], reverse=True)

    labels: List[str] = []
    for genre, _ in ranked:
        label = _format_genre_label(genre)
        if label:
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _top_artists_from_profile(profile_cache: ProfileCache, limit: int = 4) -> List[str]:
    """Return a list of artist names ordered by recent play counts."""
    if not profile_cache:
        return []

    artists = profile_cache.get("artists")
    if not isinstance(artists, dict):
        return []

    ranked = sorted(
        (
            info
            for info in artists.values()
            if isinstance(info, dict) and (info.get("name") or "").strip()
        ),
        key=lambda entry: int(entry.get("play_count") or 0),
        reverse=True,
    )

    names: List[str] = []
    seen: set[str] = set()
    for entry in ranked:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        names.append(name)
        if len(names) >= limit:
            break
    return names


def _collect_genres(
    user_identifier: str,
    profile_cache: ProfileCache,
    summary: Dict[str, object],
    sample_size: int,
) -> List[str]:
    """Aggregate the best-available genres from stats + profile cache."""
    breakdown = get_genre_breakdown(user_identifier, sample_size=sample_size)
    breakdown_genres = [
        _format_genre_label(entry.get("genre"))
        for entry in breakdown
        if entry.get("genre")
    ]
    summary_genre = _format_genre_label(summary.get("top_genre"))
    profile_genres = _top_genres_from_profile(profile_cache)
    merged = _merge_unique([*breakdown_genres, summary_genre, *profile_genres])
    return [genre for genre in merged if genre]


def _add_prompt(prompts: List[str], prompt: str, seen: set[str], limit: int) -> None:
    """Append a prompt if unique and still under the configured limit."""
    if len(prompts) >= limit:
        return
    normalized = prompt.strip()
    if not normalized:
        return
    key = normalized.lower()
    if key in seen:
        return
    seen.add(key)
    prompts.append(normalized)


def generate_listening_suggestions(
    user_identifier: str | None,
    *,
    profile_cache: ProfileCache = None,
    max_prompts: int = 9,
    genre_sample_size: int = 25,
) -> List[str]:
    """
    Build short-form playlist prompt suggestions using recent listening signals.

    Suggestions blend genre stats, cached Spotify profile data, and playlist
    history summary metrics. Returns an empty list when insufficient data is
    available so the UI can show a gentle fallback.
    """
    # pylint: disable=too-many-branches
    if not user_identifier:
        return []

    summary = summarize_generation_stats(user_identifier) or {}
    genres = _collect_genres(user_identifier, profile_cache, summary, genre_sample_size)
    artists = _top_artists_from_profile(profile_cache)
    has_history = bool(summary.get("total_playlists")) or bool(profile_cache)
    if not (genres or artists or has_history):
        return []

    prompts: List[str] = []
    seen: set[str] = set()

    for genre in genres[:3]:
        _add_prompt(prompts, f"My go-to {genre} tracks lately", seen, max_prompts)
    if len(genres) >= 2:
        _add_prompt(
            prompts,
            f"Blend {genres[0]} and {genres[1]} like my recent listening",
            seen,
            max_prompts,
        )
    if len(genres) >= 3:
        _add_prompt(
            prompts,
            f"Chill {genres[2]} session inspired by my stats",
            seen,
            max_prompts,
        )

    for artist in artists[:3]:
        _add_prompt(
            prompts,
            f"Something like {artist} with fresh finds",
            seen,
            max_prompts,
        )
        _add_prompt(prompts, f"Deep cuts inspired by {artist}", seen, max_prompts)

    if genres and artists:
        _add_prompt(
            prompts,
            f"{genres[0]} vibes featuring {artists[0]} influences",
            seen,
            max_prompts,
        )

    profile_source = (profile_cache or {}).get("source")
    if profile_source == "recently_played":
        _add_prompt(
            prompts,
            "Replay my recent listens with new discoveries",
            seen,
            max_prompts,
        )
    elif profile_source == "top_tracks":
        _add_prompt(
            prompts,
            "High-energy mix from my top tracks",
            seen,
            max_prompts,
        )

    avg_novelty = summary.get("avg_novelty")
    if isinstance(avg_novelty, (int, float)):
        if avg_novelty < 70:
            _add_prompt(
                prompts,
                "Blend familiar favorites with deeper cuts I've missed",
                seen,
                max_prompts,
            )
        else:
            _add_prompt(
                prompts,
                "Keep the discovery streak from my recent playlists",
                seen,
                max_prompts,
            )
    elif summary.get("total_playlists"):
        _add_prompt(
            prompts,
            "Remix what I've been generating lately",
            seen,
            max_prompts,
        )

    return prompts[:max_prompts]
