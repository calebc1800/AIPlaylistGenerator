"""AI-powered artist suggestion helpers."""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Sequence, Tuple

import requests
from spotipy import Spotify, SpotifyException

from .artist_recommendation_service import fetch_seed_artists
from .llm_handler import dispatch_llm_query, _parse_json_response
from .spotify_handler import _normalize_artist_key, _primary_image_url


def _top_genres_from_profile(profile_cache: Optional[Dict[str, object]], limit: int = 5) -> List[str]:
    if not isinstance(profile_cache, dict):
        return []
    buckets = profile_cache.get("genre_buckets")
    if not isinstance(buckets, dict):
        return []
    ranked = sorted(
        (
            (genre, int(bucket.get("track_count") or len(bucket.get("track_ids") or [])))
            for genre, bucket in buckets.items()
            if isinstance(bucket, dict)
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    genres: List[str] = []
    for genre, _ in ranked:
        cleaned = (genre or "").replace("-", " ").title()
        if cleaned:
            genres.append(cleaned)
        if len(genres) >= limit:
            break
    return genres


def _render_prompt(top_artists: Sequence[Dict[str, object]], genres: Sequence[str], limit: int) -> str:
    artist_lines = []
    for artist in top_artists:
        name = artist.get("name") or "Unknown"
        artist_genres = artist.get("genres") or []
        tagged = ", ".join(artist_genres[:2]) if artist_genres else ""
        if tagged:
            artist_lines.append(f"- {name} ({tagged})")
        else:
            artist_lines.append(f"- {name}")

    genre_line = ", ".join(genres) if genres else "varied styles"
    artist_summary = "\n".join(artist_lines) or "No artists provided"
    return (
        "You are an AI music curator helping a Spotify power user discover new artists.\n"
        f"Their current top artists are:\n{artist_summary}\n\n"
        f"Their favorite genres lean toward {genre_line}.\n"
        f"Suggest {limit} fresh artists that complement their taste but aren't obvious duplicates.\n"
        "Return strictly JSON: an array where each entry is {\"name\": \"Artist\", \"reason\": \"Short why\"}.\n"
        "Prioritize globally available artists with enough discography to build playlists."
    )


def _parse_ai_candidates(raw_response: str) -> List[Dict[str, str]]:
    parsed = _parse_json_response(raw_response)
    candidates: List[Dict[str, str]] = []
    if isinstance(parsed, list):
        for entry in parsed:
            if isinstance(entry, dict) and entry.get("name"):
                candidates.append(
                    {
                        "name": str(entry.get("name")).strip(),
                        "reason": str(entry.get("reason") or "").strip() or "AI discovery pick",
                    }
                )
            elif isinstance(entry, str):
                candidates.append({"name": entry.strip(), "reason": "AI discovery pick"})
    return [candidate for candidate in candidates if candidate["name"]]


def _artist_lookup_from_cache(profile_cache: Optional[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    lookup: Dict[str, Dict[str, object]] = {}
    if not isinstance(profile_cache, dict):
        return lookup
    artists = profile_cache.get("artists")
    if not isinstance(artists, dict):
        return lookup
    for artist in artists.values():
        if not isinstance(artist, dict):
            continue
        normalized = _normalize_artist_key(artist.get("name", ""))
        if normalized:
            lookup[normalized] = artist
    return lookup


def _search_artist(sp: Optional[Spotify], name: str) -> Optional[Dict[str, object]]:
    if not sp or not name:
        return None
    try:
        response = sp.search(q=f'artist:"{name}"', type="artist", limit=1)
    except SpotifyException:
        return None
    except requests.exceptions.RequestException:
        return None
    artists = response.get("artists", {}).get("items", []) if isinstance(response, dict) else []
    if not artists:
        return None
    artist = artists[0]
    return {
        "id": artist.get("id"),
        "name": artist.get("name"),
        "image": _primary_image_url(artist.get("images")),
        "genres": artist.get("genres", []),
        "popularity": int(artist.get("popularity") or 0),
        "followers": int((artist.get("followers") or {}).get("total") or 0),
        "url": (artist.get("external_urls") or {}).get("spotify", ""),
    }


def _prepare_card(artist: Dict[str, object], *, reason: str) -> Dict[str, object]:
    return {
        "id": artist.get("id"),
        "name": artist.get("name"),
        "image": artist.get("image", ""),
        "genres": artist.get("genres", []),
        "popularity": int(artist.get("popularity") or 0),
        "followers": int(artist.get("followers") or 0),
        "url": artist.get("url", ""),
        "seed_artist_ids": [artist.get("id")] if artist.get("id") else [],
        "seed_artist_names": [],
        "reason": reason,
        "score": artist.get("popularity") or 0,
    }


def generate_ai_artist_cards(
    user_identifier: str,
    *,
    sp: Optional[Spotify],
    profile_cache: Optional[Dict[str, object]],
    limit: int = 6,
    provider: str = "openai",
) -> List[Dict[str, object]]:
    """Return AI-curated artist cards enriched with Spotify metadata."""
    if not user_identifier or limit <= 0:
        return []

    seed_artists = fetch_seed_artists(user_identifier, limit=8)
    top_genres = _top_genres_from_profile(profile_cache)
    prompt = _render_prompt(seed_artists, top_genres, limit + 2)
    try:
        response = dispatch_llm_query(prompt, provider=provider)
    except Exception:  # pragma: no cover - defensive
        response = ""
    candidates = _parse_ai_candidates(response or "")
    if not candidates:
        fallback_names = [artist.get("name") for artist in seed_artists] or ["New Artist Discovery"]
        candidates = [{"name": name, "reason": "From your listening history"} for name in fallback_names]

    cache_lookup = _artist_lookup_from_cache(profile_cache)
    cards: List[Dict[str, object]] = []
    seen_ids: set[str] = set()

    for candidate in candidates:
        if len(cards) >= limit:
            break
        normalized = _normalize_artist_key(candidate["name"])
        artist_meta = cache_lookup.get(normalized)
        if not artist_meta:
            artist_meta = _search_artist(sp, candidate["name"])
        if not artist_meta or not artist_meta.get("id"):
            continue
        artist_id = artist_meta["id"]
        if artist_id in seen_ids:
            continue
        seen_ids.add(artist_id)
        cards.append(_prepare_card(artist_meta, reason=candidate["reason"]))

    if len(cards) < limit and seed_artists:
        random.shuffle(seed_artists)
        for seed in seed_artists:
            if len(cards) >= limit:
                break
            artist_id = seed.get("id")
            if not artist_id or artist_id in seen_ids:
                continue
            seen_ids.add(artist_id)
            cards.append(
                _prepare_card(
                    {
                        "id": seed.get("id"),
                        "name": seed.get("name"),
                        "image": seed.get("image", ""),
                        "genres": seed.get("genres", []),
                        "popularity": seed.get("popularity", 0),
                        "followers": seed.get("followers", 0),
                        "url": seed.get("url", ""),
                    },
                    reason="From your listening history",
                )
            )

    return cards[:limit]

