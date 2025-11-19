"""Reusable helpers for shaping artist recommendation payloads."""

from __future__ import annotations

from typing import Mapping, Sequence


def build_artist_card(
    artist: Mapping[str, object],
    *,
    reason: str,
    score: float | int = 0,
    seed_artist_ids: Sequence[str] | None = None,
    seed_artist_names: Sequence[str] | None = None,
) -> dict[str, object]:
    """Normalize artist metadata into the shape expected by the UI."""
    artist_id = artist.get("id")
    seed_ids = list(seed_artist_ids or [])
    if not seed_ids and artist_id:
        seed_ids = [artist_id]
    seed_names = list(seed_artist_names or [])
    return {
        "id": artist_id,
        "name": artist.get("name"),
        "image": artist.get("image", ""),
        "genres": artist.get("genres", []),
        "popularity": int(artist.get("popularity") or 0),
        "followers": int(artist.get("followers") or 0),
        "url": artist.get("url", ""),
        "seed_artist_ids": seed_ids,
        "seed_artist_names": seed_names,
        "reason": reason,
        "score": score,
    }


def basic_artist_payload(source: Mapping[str, object]) -> dict[str, object]:
    """Return a simplified snapshot used for fallback meta generation."""
    return {
        "id": source.get("id"),
        "name": source.get("name"),
        "image": source.get("image", ""),
        "genres": source.get("genres", []),
        "popularity": int(source.get("popularity") or 0),
        "followers": int(source.get("followers") or 0),
        "url": source.get("url", ""),
    }
