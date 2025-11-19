"""AI-powered artist suggestion helpers."""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Sequence

import requests
from spotipy import Spotify, SpotifyException

from .artist_card_utils import basic_artist_payload, build_artist_card
from .artist_recommendation_service import fetch_seed_artists
from .llm_handler import _parse_json_response, dispatch_llm_query
from .spotify_handler import _normalize_artist_key, _primary_image_url

try:
    from django.conf import settings
except ImportError:  # pragma: no cover - when Django isn't configured
    settings = None

MIN_AI_ARTIST_FOLLOWERS = (
    getattr(settings, "AI_ARTIST_MIN_FOLLOWERS", 1000) if settings else 1000
)
MIN_AI_ARTIST_POPULARITY = (
    getattr(settings, "AI_ARTIST_MIN_POPULARITY", 15) if settings else 15
)


def _top_genres_from_profile(
    profile_cache: Optional[Dict[str, object]],
    limit: int = 5,
) -> List[str]:
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


def _render_prompt(
    top_artists: Sequence[Dict[str, object]],
    genres: Sequence[str],
    limit: int,
) -> str:
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
        f"Suggest {limit} fresh artists that complement their taste "
        "but aren't obvious duplicates.\n"
        "Return strictly JSON: an array where each entry is "
        '{"name": "Artist", "reason": "Short why"}.\n'
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


def _artist_lookup_from_cache(
    profile_cache: Optional[Dict[str, object]],
) -> Dict[str, Dict[str, object]]:
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


def _has_listenable_tracks(sp: Optional[Spotify], artist_id: Optional[str]) -> bool:
    if not sp or not artist_id:
        return True
    try:
        response = sp.artist_top_tracks(artist_id, country="US")
    except SpotifyException:
        return True
    except requests.exceptions.RequestException:
        return True
    tracks = response.get("tracks", []) if isinstance(response, dict) else []
    return any(isinstance(track, dict) and track.get("id") for track in tracks)


def _artist_is_valid(sp: Optional[Spotify], artist: Dict[str, object]) -> bool:
    followers = int(artist.get("followers") or 0)
    popularity = int(artist.get("popularity") or 0)
    if followers < MIN_AI_ARTIST_FOLLOWERS or popularity < MIN_AI_ARTIST_POPULARITY:
        return False
    return _has_listenable_tracks(sp, artist.get("id"))

def _ai_candidates_for_user(
    seed_artists: Sequence[Dict[str, object]],
    top_genres: Sequence[str],
    limit: int,
    provider: str,
) -> List[Dict[str, str]]:
    prompt_limit = max(limit + 6, limit * 2)
    prompt = _render_prompt(seed_artists, top_genres, prompt_limit)
    try:
        response = dispatch_llm_query(prompt, provider=provider)
    except Exception:  # pragma: no cover - defensive fallback  # pylint: disable=broad-exception-caught
        response = ""

    candidates = _parse_ai_candidates(response or "")
    if candidates:
        return candidates

    fallback_names = [artist.get("name") for artist in seed_artists] or ["New Artist Discovery"]
    return [
        {"name": name, "reason": "From your listening history"}
        for name in fallback_names
        if name
    ]


def _resolve_artist_metadata(
    candidate_name: str,
    cache_lookup: Dict[str, Dict[str, object]],
    sp: Optional[Spotify],
) -> Optional[Dict[str, object]]:
    normalized = _normalize_artist_key(candidate_name)
    artist_meta = cache_lookup.get(normalized)
    if not artist_meta:
        artist_meta = _search_artist(sp, candidate_name)
    if not artist_meta or not artist_meta.get("id"):
        return None
    return artist_meta


def _append_seed_fallbacks(
    cards: List[Dict[str, object]],
    seed_artists: Sequence[Dict[str, object]],
    seen_ids: set[str],
    *,
    limit: int,
    sp: Optional[Spotify],
) -> None:
    if not seed_artists or len(cards) >= limit:
        return
    random.shuffle(seed_artists)
    for seed in seed_artists:
        if len(cards) >= limit:
            break
        artist_id = seed.get("id")
        if not artist_id or artist_id in seen_ids:
            continue
        fallback_meta = basic_artist_payload(seed)
        if not _artist_is_valid(sp, fallback_meta):
            continue
        seen_ids.add(artist_id)
        cards.append(build_artist_card(fallback_meta, reason="From your listening history"))


def generate_ai_artist_cards(
    user_identifier: str,
    *,
    sp: Optional[Spotify],
    profile_cache: Optional[Dict[str, object]],
    limit: int = 8,
    provider: str = "openai",
) -> List[Dict[str, object]]:
    """Return AI-curated artist cards enriched with Spotify metadata."""
    if not user_identifier or limit <= 0:
        return []

    seed_artists = fetch_seed_artists(user_identifier, limit=max(10, limit + 2))
    top_genres = _top_genres_from_profile(profile_cache)
    candidates = _ai_candidates_for_user(seed_artists, top_genres, limit, provider)
    cache_lookup = _artist_lookup_from_cache(profile_cache)
    cards: List[Dict[str, object]] = []
    seen_ids: set[str] = set()

    for candidate in candidates:
        if len(cards) >= limit:
            break
        artist_meta = _resolve_artist_metadata(candidate["name"], cache_lookup, sp)
        if not artist_meta:
            continue
        artist_id = str(artist_meta["id"])
        if artist_id in seen_ids:
            continue
        if not _artist_is_valid(sp, artist_meta):
            continue
        seen_ids.add(artist_id)
        cards.append(build_artist_card(artist_meta, reason=candidate["reason"]))

    _append_seed_fallbacks(cards, seed_artists, seen_ids, limit=limit, sp=sp)

    return cards[:limit]
