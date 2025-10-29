"""Spotify helper utilities for the recommender app."""

import random
import re
import time
import unicodedata
from collections import Counter
from typing import Callable, Dict, Iterable, List, Optional, Set

import requests
from django.conf import settings
import spotipy
from spotipy import SpotifyException


DEFAULT_POPULARITY_THRESHOLD = getattr(settings, "RECOMMENDER_POPULARITY_THRESHOLD", 45)
GENRE_POPULARITY_OVERRIDES = getattr(
    settings,
    "RECOMMENDER_GENRE_POPULARITY_OVERRIDES",
    {
        "ambient": 25,
        "lo-fi": 25,
        "lofi": 25,
        "jazz": 30,
        "classical": 30,
        "folk": 35,
        "singer-songwriter": 35,
    },
)


def _log(
    debug_steps: Optional[List[str]],
    log_step: Optional[Callable[[str], None]],
    message: str,
) -> None:
    """Record a debug message either via callback or mutable list."""
    if log_step:
        log_step(message)
    elif debug_steps is not None:
        debug_steps.append(message)


def _normalize_genre(raw_genre: str) -> str:
    """Normalize a genre string into a lowercase, ascii-safe hyphenated token."""
    if not raw_genre:
        return ""
    normalized = unicodedata.normalize("NFKD", raw_genre)
    ascii_clean = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_clean.strip().lower().replace(" ", "-")


def normalize_genre(raw_genre: str) -> str:
    """Convenience wrapper to expose genre normalization to other modules."""
    return _normalize_genre(raw_genre)


def _normalize_artist_key(name: str) -> str:
    """Return a simplified key for fuzzy artist name matching."""
    if not name:
        return ""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_clean = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_clean.lower())


def _genre_variants(normalized_genre: str) -> Set[str]:
    """Return common permutations of a genre to improve fuzzy matches."""
    if not normalized_genre:
        return set()
    base = normalized_genre.replace("-", " ")
    compact = base.replace(" ", "")
    variants = {normalized_genre, base, compact}
    if normalized_genre.endswith("-music"):
        variants.add(normalized_genre[:-6])
    if normalized_genre in {"r-b", "r&b"}:
        variants.update({"r&b", "rb", "r-b"})
    if normalized_genre == "hip-hop":
        variants.add("hiphop")
    return {v for v in variants if v}


def _tracks_to_strings(tracks: List[Dict]) -> List[str]:
    """Render track dictionaries into human-readable strings."""
    return [
        f"{track['name']} - {track['artists'][0]['name']}"
        for track in tracks
        if track.get("artists")
    ]


def _filter_by_market(tracks: List[Dict], market: str) -> List[Dict]:
    """Filter tracks that are not available in the desired market."""
    filtered: List[Dict] = []
    for track in tracks:
        markets = track.get("available_markets")
        if not markets or market in markets:
            filtered.append(track)
    return filtered


def _should_filter_non_latin() -> bool:
    """Return True when playlist results should prefer latin character sets."""
    return getattr(settings, "RECOMMENDER_REQUIRE_LATIN", False)


def _popularity_threshold_for_genre(normalized_genre: str) -> int:
    """Resolve the minimum track popularity for the supplied genre."""
    return GENRE_POPULARITY_OVERRIDES.get(normalized_genre, DEFAULT_POPULARITY_THRESHOLD)


def _primary_artist_hint(artist: str) -> str:
    """Extract the primary artist name from a formatted credit string."""
    if not artist:
        return ""
    primary = re.split(r"\s*(?:,|&|feat\.?|ft\.?|with)\s*", artist, maxsplit=1)[0]
    return primary.strip()


def _filter_tracks_by_artist_genre(
    sp: spotipy.Spotify,
    tracks: List[Dict],
    normalized_genre: str,
    *,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    popularity_threshold: Optional[int] = None,
) -> List[Dict]:
    """Keep tracks whose artists are strongly associated with the target genre."""
    if not tracks:
        return []

    genre_aliases = _genre_variants(normalized_genre)

    artist_id_map: Dict[str, List[str]] = {}
    artist_ids: List[str] = []
    for track in tracks:
        ids = [artist.get("id") for artist in track.get("artists", []) if artist.get("id")]
        if ids:
            artist_id_map[track["id"]] = ids
            artist_ids.extend(ids)

    if not artist_ids:
        return tracks

    unique_artist_ids = list(dict.fromkeys(artist_ids))
    artist_details: Dict[str, List[str]] = {}

    for start in range(0, len(unique_artist_ids), 50):
        batch = unique_artist_ids[start : start + 50]
        try:
            response = sp.artists(batch)
        except SpotifyException as exc:
            _log(debug_steps, log_step, f"Failed to fetch artist genres: {exc}.")
            continue
        except requests.exceptions.RequestException as exc:
            _log(debug_steps, log_step, f"Network error while fetching artist genres: {exc}.")
            continue
        for artist in response.get("artists", []):
            artist_id = artist.get("id")
            if artist_id:
                artist_details[artist_id] = artist.get("genres", [])

    if not artist_details:
        return tracks

    target = normalized_genre.replace("-", "")
    threshold = popularity_threshold or _popularity_threshold_for_genre(normalized_genre)
    filtered_tracks: List[Dict] = []

    for track in tracks:
        if track.get("popularity", 0) < threshold:
            continue
        matched = False
        for artist_id in artist_id_map.get(track["id"], []):
            genres = artist_details.get(artist_id, [])
            for genre in genres:
                normalized = genre.lower()
                canonical = normalized.replace(" ", "").replace("-", "")
                if (target and target in canonical) or any(
                    alias == normalized or alias == canonical or alias in canonical
                    for alias in genre_aliases
                ):
                    matched = True
                    break
            if matched:
                break
        if matched:
            filtered_tracks.append(track)

    _log(
        debug_steps,
        log_step,
        f"Filtered tracks by artist genre '{normalized_genre}': {len(filtered_tracks)} remaining.",
    )

    return filtered_tracks or tracks


def _is_mostly_latin(text: str) -> bool:
    """Heuristic that checks whether a string primarily uses latin characters."""
    if not text:
        return True
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return True
    latin = sum(1 for c in alpha_chars if "LATIN" in unicodedata.name(c, ""))
    return latin / len(alpha_chars) >= 0.4


def _filter_non_latin_tracks(tracks: Iterable[Dict]) -> List[Dict]:
    """Drop tracks whose names contain mostly non-latin characters."""
    return [track for track in tracks if _is_mostly_latin(track.get("name", ""))]


def _extract_release_year(track: Dict) -> Optional[int]:
    """Return the release year from a track dictionary, if available."""
    album = (track or {}).get("album") or {}
    date = album.get("release_date") or track.get("release_date")
    if not date:
        return None
    match = re.match(r"(\d{4})", date)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _primary_image_url(images: Optional[List[Dict]]) -> str:
    """Return the first available URL from a list of Spotify image dictionaries."""
    if not images:
        return ""
    for image in images:
        url = image.get("url")
        if url:
            return url
    return ""


def _serialize_track_payload(track: Dict) -> Dict[str, object]:
    """Normalize Spotify track metadata for downstream views and caching."""
    album = track.get("album") or {}
    artists = track.get("artists") or []
    artist_names = ", ".join(artist.get("name", "") for artist in artists if artist.get("name"))
    return {
        "id": track.get("id"),
        "name": track.get("name", "Unknown"),
        "artists": artist_names or "Unknown",
        "album_name": album.get("name", ""),
        "album_image_url": _primary_image_url(album.get("images")),
        "duration_ms": int(track.get("duration_ms") or 0),
        "artist_ids": [artist.get("id") for artist in artists if artist.get("id")],
        "year": _extract_release_year(track),
        "popularity": int(track.get("popularity") or 0),
    }


def build_user_profile_seed_snapshot(
    sp: spotipy.Spotify,
    *,
    limit: int = 50,
    recent_limit: int = 50,
    market: str = "US",
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
) -> Optional[Dict[str, object]]:
    """Collect the user's top tracks/artists to prime recommendation seeds."""

    try:
        response = sp.current_user_top_tracks(limit=limit, time_range="medium_term")
        raw_tracks = response.get("items", []) if isinstance(response, dict) else []
        source_label = "top_tracks"
        if raw_tracks:
            _log(debug_steps, log_step, f"Seed snapshot fetched {len(raw_tracks)} top tracks.")
    except SpotifyException as exc:
        _log(debug_steps, log_step, f"Spotify top tracks call failed: {exc}.")
        raw_tracks = []
        source_label = "top_tracks"
    except requests.exceptions.RequestException as exc:
        _log(debug_steps, log_step, f"Network error fetching top tracks: {exc}.")
        raw_tracks = []
        source_label = "top_tracks"

    if not raw_tracks:
        try:
            response = sp.current_user_recently_played(limit=recent_limit)
            items = response.get("items", []) if isinstance(response, dict) else []
            raw_tracks = [entry.get("track") for entry in items if isinstance(entry, dict) and entry.get("track")]
            raw_tracks = [track for track in raw_tracks if track]
            source_label = "recently_played"
            if raw_tracks:
                _log(debug_steps, log_step, f"Seed snapshot fell back to {len(raw_tracks)} recent tracks.")
        except SpotifyException as exc:
            _log(debug_steps, log_step, f"Spotify recently played call failed: {exc}.")
            raw_tracks = []
        except requests.exceptions.RequestException as exc:
            _log(debug_steps, log_step, f"Network error fetching recent tracks: {exc}.")
            raw_tracks = []

    if not raw_tracks:
        _log(debug_steps, log_step, "Seed snapshot unavailable; no tracks retrieved.")
        return None

    seen_track_ids: Set[str] = set()
    track_payloads: List[Dict[str, object]] = []
    artist_counts: Counter[str] = Counter()
    artist_order: List[str] = []

    for track in raw_tracks:
        if not isinstance(track, dict):
            continue
        track_id = track.get("id")
        if not track_id or track_id in seen_track_ids:
            continue
        seen_track_ids.add(track_id)
        payload = _serialize_track_payload(track)
        payload["popularity"] = int(track.get("popularity") or 0)
        payload["source"] = source_label
        payload["genres"] = []  # populated after artist lookup
        track_payloads.append(payload)
        for artist in track.get("artists", []) or []:
            artist_id = artist.get("id")
            if artist_id:
                artist_counts[artist_id] += 1
                artist_order.append(artist_id)

    if not track_payloads:
        _log(debug_steps, log_step, "Seed snapshot stopped; no eligible tracks after dedupe.")
        return None

    unique_artist_ids = list(dict.fromkeys(artist_order))
    if not unique_artist_ids:
        _log(debug_steps, log_step, "Seed snapshot stopped; no artist identifiers available.")
        return None

    artist_details: Dict[str, Dict[str, object]] = {}
    for start in range(0, len(unique_artist_ids), 50):
        batch = unique_artist_ids[start : start + 50]
        try:
            response = sp.artists(batch)
        except SpotifyException as exc:
            _log(debug_steps, log_step, f"Failed to fetch artist metadata: {exc}.")
            continue
        except requests.exceptions.RequestException as exc:
            _log(debug_steps, log_step, f"Network error fetching artist metadata: {exc}.")
            continue

        for artist in response.get("artists", []) or []:
            if not isinstance(artist, dict):
                continue
            artist_id = artist.get("id")
            if not artist_id:
                continue
            normalized_genres = [
                genre
                for genre in {normalize_genre(raw) for raw in artist.get("genres", []) or []}
                if genre
            ]
            artist_details[artist_id] = {
                "id": artist_id,
                "name": artist.get("name", ""),
                "genres": normalized_genres,
            }

    if not artist_details:
        _log(debug_steps, log_step, "Seed snapshot stopped; artist metadata unavailable.")
        return None

    genre_buckets: Dict[str, Dict[str, object]] = {}
    track_lookup: Dict[str, Dict[str, object]] = {}

    for payload in track_payloads:
        track_id = payload.get("id")
        if not track_id:
            continue
        artist_ids = payload.get("artist_ids") or []
        genre_set: Set[str] = set()
        for artist_id in artist_ids:
            artist_info = artist_details.get(artist_id)
            if not artist_info:
                continue
            for genre in artist_info.get("genres", []):
                if genre:
                    genre_set.add(genre)
        payload["genres"] = sorted(genre_set)
        track_lookup[track_id] = payload

        for genre in payload["genres"]:
            if not genre:
                continue
            bucket = genre_buckets.setdefault(
                genre,
                {
                    "track_ids": [],
                    "artist_ids": [],
                    "popularity_total": 0,
                    "year_total": 0,
                    "year_count": 0,
                },
            )
            bucket["track_ids"].append(track_id)
            bucket["artist_ids"].extend(artist_ids)
            bucket["popularity_total"] += payload.get("popularity", 0)
            year = payload.get("year")
            if isinstance(year, int):
                bucket["year_total"] += year
                bucket["year_count"] += 1

    formatted_buckets: Dict[str, Dict[str, object]] = {}
    per_genre_limit = 12
    for genre, bucket in genre_buckets.items():
        track_ids = bucket.get("track_ids", [])
        track_ids = [track_id for track_id in track_ids if track_id in track_lookup]
        sorted_ids = sorted(
            track_ids,
            key=lambda track_id: (
                track_lookup[track_id].get("popularity", 0),
                track_lookup[track_id].get("year") or 0,
            ),
            reverse=True,
        )
        artist_ids = [aid for aid in bucket.get("artist_ids", []) if aid]
        formatted_buckets[genre] = {
            "track_ids": sorted_ids[:per_genre_limit],
            "artist_ids": list(dict.fromkeys(artist_ids))[: per_genre_limit * 2],
            "avg_popularity": bucket["popularity_total"] / max(len(track_ids), 1),
            "avg_year": (
                bucket["year_total"] / bucket["year_count"] if bucket["year_count"] else None
            ),
            "track_count": len(track_ids),
        }

    artist_snapshot: Dict[str, Dict[str, object]] = {}
    artist_name_map: Dict[str, str] = {}
    for artist_id, info in artist_details.items():
        artist_snapshot[artist_id] = {
            "id": artist_id,
            "name": info.get("name", ""),
            "genres": info.get("genres", []),
            "play_count": int(artist_counts.get(artist_id, 0)),
        }
        normalized_key = _normalize_artist_key(info.get("name", ""))
        if normalized_key:
            artist_name_map[normalized_key] = artist_id

    top_tracks_sorted = sorted(
        [payload for payload in track_lookup.values()],
        key=lambda item: (item.get("popularity", 0), item.get("year") or 0),
        reverse=True,
    )
    top_track_ids = [item.get("id") for item in top_tracks_sorted if item.get("id")]

    snapshot: Dict[str, object] = {
        "created_at": time.time(),
        "source": source_label,
        "sample_size": len(track_lookup),
        "tracks": track_lookup,
        "genre_buckets": formatted_buckets,
        "artist_counts": {artist_id: int(count) for artist_id, count in artist_counts.items()},
        "artists": artist_snapshot,
        "artist_name_map": artist_name_map,
        "top_track_ids": top_track_ids[:50],
    }

    return snapshot


def cached_tracks_for_genre(
    profile_cache: Optional[Dict[str, object]],
    normalized_genre: str,
    *,
    limit: int = 5,
) -> List[Dict[str, object]]:
    """Retrieve cached top tracks for a genre from the user profile snapshot."""

    if not profile_cache or not normalized_genre:
        return []

    genre_buckets = profile_cache.get("genre_buckets")
    if not isinstance(genre_buckets, dict):
        return []
    bucket = genre_buckets.get(normalized_genre)
    if not isinstance(bucket, dict):
        return []

    track_ids = bucket.get("track_ids") or []
    track_lookup = profile_cache.get("tracks")
    if not isinstance(track_lookup, dict):
        return []

    results: List[Dict[str, object]] = []
    for track_id in track_ids:
        track = track_lookup.get(track_id)
        if not isinstance(track, dict):
            continue
        results.append(dict(track))
        if len(results) >= limit:
            break

    return results


def cached_tracks_for_artist(
    profile_cache: Optional[Dict[str, object]],
    artist_id: Optional[str],
    *,
    limit: int = 5,
) -> List[Dict[str, object]]:
    """Return cached tracks for a specific artist from the profile snapshot."""

    if not profile_cache or not artist_id:
        return []

    track_lookup = profile_cache.get("tracks")
    if not isinstance(track_lookup, dict):
        return []

    matches: List[Dict[str, object]] = []
    for track in track_lookup.values():
        if not isinstance(track, dict):
            continue
        artist_ids = track.get("artist_ids") or []
        if artist_id in artist_ids:
            matches.append(track)

    matches.sort(key=lambda item: (item.get("popularity", 0), item.get("year") or 0), reverse=True)
    return [dict(track) for track in matches[:limit]]


def cached_artist_id_for_hint(
    profile_cache: Optional[Dict[str, object]],
    artist_hint: str,
) -> Optional[str]:
    """Attempt to resolve an artist identifier using the cached snapshot."""

    if not profile_cache or not artist_hint:
        return None

    normalized_hint = _normalize_artist_key(artist_hint)
    if not normalized_hint:
        return None

    name_map = profile_cache.get("artist_name_map")
    if isinstance(name_map, dict):
        artist_id = name_map.get(normalized_hint)
        if artist_id:
            return artist_id

    artists = profile_cache.get("artists")
    if isinstance(artists, dict):
        for artist_id, info in artists.items():
            name = _normalize_artist_key((info or {}).get("name", ""))
            if name and normalized_hint == name:
                return artist_id
            if name and normalized_hint in name:
                return artist_id

    return None


def ensure_artist_seed(
    artist_hint: str,
    token: str,
    *,
    profile_cache: Optional[Dict[str, object]] = None,
    market: str = "US",
    seed_limit: int = 5,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
) -> Optional[Dict[str, object]]:
    """Guarantee that an explicitly requested artist contributes seeds."""

    if not artist_hint:
        return None

    sp = spotipy.Spotify(auth=token)
    cached_artist_id = cached_artist_id_for_hint(profile_cache, artist_hint)
    resolved_artist_id: Optional[str] = cached_artist_id
    resolved_artist_name: Optional[str] = None

    if cached_artist_id and profile_cache:
        artists = profile_cache.get("artists") or {}
        cached_record = artists.get(cached_artist_id) if isinstance(artists, dict) else None
        if isinstance(cached_record, dict):
            resolved_artist_name = cached_record.get("name")

    if not resolved_artist_id:
        query = f'artist:"{artist_hint}"'
        try:
            search_result = sp.search(q=query, type="artist", limit=3)
            artist_items = search_result.get("artists", {}).get("items", [])
        except SpotifyException as exc:
            _log(debug_steps, log_step, f"Spotify artist search failed for '{artist_hint}': {exc}.")
            artist_items = []
        except requests.exceptions.RequestException as exc:
            _log(debug_steps, log_step, f"Network error during artist search for '{artist_hint}': {exc}.")
            artist_items = []

        for candidate in artist_items:
            if not isinstance(candidate, dict):
                continue
            candidate_id = candidate.get("id")
            candidate_name = candidate.get("name", "")
            if not candidate_id:
                continue
            candidate_key = _normalize_artist_key(candidate_name)
            hint_key = _normalize_artist_key(artist_hint)
            if candidate_key == hint_key or hint_key in candidate_key:
                resolved_artist_id = candidate_id
                resolved_artist_name = candidate_name
                break

        if not resolved_artist_id and artist_items:
            fallback = artist_items[0]
            resolved_artist_id = fallback.get("id")
            resolved_artist_name = fallback.get("name", "")

    if not resolved_artist_id:
        _log(debug_steps, log_step, f"Unable to resolve artist for hint '{artist_hint}'.")
        return None

    cached_tracks = cached_tracks_for_artist(profile_cache, resolved_artist_id, limit=seed_limit)
    if cached_tracks:
        _log(
            debug_steps,
            log_step,
            f"Using {len(cached_tracks)} cached tracks for artist '{resolved_artist_name or artist_hint}'.",
        )
        return {
            "artist_id": resolved_artist_id,
            "artist_name": resolved_artist_name or artist_hint,
            "tracks": cached_tracks,
            "source": "profile_cache",
        }

    try:
        top_tracks_response = sp.artist_top_tracks(resolved_artist_id, country=market)
        top_tracks = top_tracks_response.get("tracks", []) if isinstance(top_tracks_response, dict) else []
    except SpotifyException as exc:
        _log(debug_steps, log_step, f"Spotify top tracks failed for artist '{resolved_artist_id}': {exc}.")
        top_tracks = []
    except requests.exceptions.RequestException as exc:
        _log(debug_steps, log_step, f"Network error fetching top tracks for artist '{resolved_artist_id}': {exc}.")
        top_tracks = []

    if not top_tracks:
        _log(debug_steps, log_step, f"No top tracks returned for artist '{resolved_artist_id}'.")
        return None

    payloads: List[Dict[str, object]] = []
    seen_ids: Set[str] = set()
    for track in top_tracks:
        if not isinstance(track, dict):
            continue
        track_id = track.get("id")
        if not track_id or track_id in seen_ids:
            continue
        seen_ids.add(track_id)
        payload = _serialize_track_payload(track)
        payload["popularity"] = int(track.get("popularity") or 0)
        payload["seed_source"] = "artist_top_tracks"
        payloads.append(payload)
        if len(payloads) >= seed_limit:
            break

    if not payloads:
        return None

    _log(
        debug_steps,
        log_step,
        f"Collected {len(payloads)} top tracks for artist '{resolved_artist_name or artist_hint}'.",
    )

    return {
        "artist_id": resolved_artist_id,
        "artist_name": resolved_artist_name or artist_hint,
        "tracks": payloads,
        "source": "artist_top_tracks",
    }


def _discover_playlist_seeds(
    sp: spotipy.Spotify,
    normalized_genre: str,
    *,
    market: str,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    playlist_limit: int = 3,
    track_limit: int = 40,
) -> List[Dict]:
    """Harvest candidate seed tracks by scanning popular Spotify playlists."""
    base_label = normalized_genre.replace("-", " ").strip() or "popular"
    playlist_queries = [
        f"{base_label} hits",
        f"top {base_label}",
        f"best of {base_label}",
        f"{base_label} mix",
    ]
    query = random.choice(playlist_queries)
    _log(debug_steps, log_step, f"Spotify API → search playlists: q='{query}', limit={playlist_limit}")
    try:
        playlists = sp.search(q=query, type="playlist", limit=playlist_limit)
        playlist_items = playlists.get("playlists", {}).get("items", [])
    except SpotifyException as exc:
        _log(debug_steps, log_step, f"Spotify playlist search failed: {exc}.")
        playlist_items = []

    collected: List[Dict] = []
    seen_ids: Set[str] = set()

    for playlist in playlist_items:
        if not isinstance(playlist, dict):
            continue
        playlist_id = playlist.get("id")
        if not playlist_id:
            continue
        owner = (playlist.get("owner") or {}).get("id")
        if owner and owner.lower() == "spotify":
            continue
        _log(
            debug_steps,
            log_step,
            f"Spotify API → playlist_items: playlist_id={playlist_id}, limit={track_limit}, market={market}",
        )
        try:
            response = sp.playlist_items(playlist_id, limit=track_limit, market=market)
        except SpotifyException:
            try:
                response = sp.playlist_items(playlist_id, limit=track_limit)
            except SpotifyException:
                continue
            except requests.exceptions.RequestException as exc:
                _log(
                    debug_steps,
                    log_step,
                    f"Network error fetching playlist items for '{playlist_id}': {exc}.",
                )
                continue
        except requests.exceptions.RequestException as exc:
            _log(
                debug_steps,
                log_step,
                f"Network error fetching playlist items for '{playlist_id}': {exc}.",
            )
            continue
        items = response.get("items", [])
        for entry in items:
            track = entry.get("track")
            if not track or not track.get("id") or track["id"] in seen_ids:
                continue
            seen_ids.add(track["id"])
            collected.append(track)

    _log(
        debug_steps,
        log_step,
        f"Collected {len(collected)} tracks from playlists for genre '{normalized_genre}'.",
    )

    return collected


def discover_top_tracks_for_genre(
    attributes: Dict[str, str],
    token: str,
    *,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    market: str = "US",
    seed_limit: int = 5,
    search_limit: int = 50,
) -> List[Dict[str, str]]:
    """Return a curated list of genre-specific tracks to bootstrap recommendations."""
    sp = spotipy.Spotify(auth=token)
    normalized_genre = _normalize_genre(attributes.get("genre", "pop") or "pop")
    query = f'genre:"{normalized_genre}"'

    playlist_tracks = _discover_playlist_seeds(
        sp,
        normalized_genre,
        market=market,
        debug_steps=debug_steps,
        log_step=log_step,
    )
    playlist_tracks = _filter_tracks_by_artist_genre(
        sp,
        playlist_tracks,
        normalized_genre,
        debug_steps=debug_steps,
        log_step=log_step,
    )
    if _should_filter_non_latin():
        playlist_tracks = _filter_non_latin_tracks(playlist_tracks)
    playlist_tracks.sort(key=lambda t: t.get("popularity", 0), reverse=True)

    def _collect(tracks: List[Dict]) -> List[Dict[str, str]]:
        """Select unique tracks up to the desired seed limit."""
        selected: List[Dict[str, str]] = []
        seen_ids: Set[str] = set()
        for track in tracks:
            if len(selected) >= seed_limit:
                break
            if track["id"] in seen_ids:
                continue
            seen_ids.add(track["id"])
            artist_ids = [artist.get("id") for artist in track.get("artists", []) if artist.get("id")]
            selected.append(
                {
                    "id": track["id"],
                    "name": track["name"],
                    "artists": ", ".join(artist.get("name", "") for artist in track.get("artists", [])),
                    "artist_ids": artist_ids,
                    "year": _extract_release_year(track),
                    "popularity": int(track.get("popularity") or 0),
                }
            )
        return selected

    selected = _collect(playlist_tracks)

    sample_names = [track.get("name") for track in playlist_tracks[:5] if track.get("name")]
    if sample_names:
        _log(debug_steps, log_step, f"Playlist seed sample: {sample_names}")

    if len(selected) < seed_limit:
        tracks: List[Dict] = []
        try:
            offset = random.randint(0, max(0, 100 - search_limit)) if search_limit < 100 else 0
            _log(
                debug_steps,
                log_step,
                f"Spotify API → search tracks (genre seed): q='{query}', limit={search_limit}, market={market}, offset={offset}",
            )
            tracks = sp.search(
                q=query,
                type="track",
                limit=search_limit,
                market=market,
                offset=offset,
            ).get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
        except SpotifyException as exc:
            _log(debug_steps, log_step, f"Spotify search for genre seeds failed: {exc}.")
        except requests.exceptions.RequestException as exc:
            _log(debug_steps, log_step, f"Network error during Spotify genre search: {exc}.")

        if not tracks:
            try:
                _log(
                    debug_steps,
                    log_step,
                    f"Spotify API → search tracks (no market): q='{query}', limit={search_limit}",
                )
                fallback_offset = random.randint(0, max(0, 100 - search_limit)) if search_limit < 100 else 0
                tracks = sp.search(
                    q=query,
                    type="track",
                    limit=search_limit,
                    offset=fallback_offset,
                ).get("tracks", {}).get("items", [])
            except SpotifyException as exc:
                _log(debug_steps, log_step, f"Spotify search without market failed: {exc}.")
                tracks = []
            except requests.exceptions.RequestException as exc:
                _log(debug_steps, log_step, f"Network error during Spotify fallback search: {exc}.")
                tracks = []

        if tracks:
            tracks = _filter_tracks_by_artist_genre(
                sp,
                tracks,
                normalized_genre,
                debug_steps=debug_steps,
                log_step=log_step,
            )
            if _should_filter_non_latin():
                tracks = _filter_non_latin_tracks(tracks)
            tracks.sort(key=lambda t: t.get("popularity", 0), reverse=True)
            sample_names = [track.get("name") for track in tracks[:5] if track.get("name")]
            if sample_names:
                _log(debug_steps, log_step, f"Search seed sample: {sample_names}")

            additional = _collect(tracks)
            for track in additional:
                if track not in selected and len(selected) < seed_limit:
                    selected.append(track)

    _log(
        debug_steps,
        log_step,
        f"Discovered {len(selected)} top tracks for genre '{normalized_genre}'.",
    )

    return selected


def resolve_seed_tracks(
    suggestions: List[Dict[str, str]],
    token: str,
    *,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    market: str = "US",
    limit: int = 5,
) -> List[Dict[str, str]]:
    """Resolve LLM-provided suggestions into concrete Spotify track metadata."""
    sp = spotipy.Spotify(auth=token)
    resolved: List[Dict[str, str]] = []

    for suggestion in suggestions:
        if len(resolved) >= limit:
            break

        title = suggestion.get("title", "").strip()
        artist = suggestion.get("artist", "").strip()
        if not title:
            continue

        primary_artist = _primary_artist_hint(artist)
        query_parts = [f'track:"{title}"']
        if artist:
            query_parts.append(f'artist:"{artist}"')
        query = " ".join(query_parts)

        tracks: List[Dict] = []
        try:
            _log(debug_steps, log_step, f'Spotify API → search track: q="{query}", limit=5, market={market}')
            tracks = sp.search(q=query, type="track", limit=5, market=market).get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
            if _should_filter_non_latin():
                tracks = _filter_non_latin_tracks(tracks)
        except SpotifyException as exc:
            _log(debug_steps, log_step, f"Spotify search failed for '{query}' with market {market}: {exc}.")
        except requests.exceptions.RequestException as exc:
            _log(debug_steps, log_step, f"Network error during Spotify search for '{query}': {exc}.")

        if not tracks and primary_artist and primary_artist != artist:
            fallback_query = f'track:"{title}" artist:"{primary_artist}"'
            try:
                _log(debug_steps, log_step, f'Spotify API → search track (primary artist): q="{fallback_query}", limit=5, market={market}')
                tracks = sp.search(q=fallback_query, type="track", limit=5, market=market).get("tracks", {}).get("items", [])
                tracks = _filter_by_market(tracks, market)
                if _should_filter_non_latin():
                    tracks = _filter_non_latin_tracks(tracks)
            except SpotifyException as exc:
                _log(debug_steps, log_step, f"Spotify search failed for '{fallback_query}' with market {market}: {exc}.")
            except requests.exceptions.RequestException as exc:
                _log(debug_steps, log_step, f"Network error during fallback Spotify search '{fallback_query}': {exc}.")

        if not tracks:
            try:
                _log(debug_steps, log_step, f'Spotify API → search track (no market): q="{query}", limit=5')
                tracks = sp.search(q=query, type="track", limit=5).get("tracks", {}).get("items", [])
                if _should_filter_non_latin():
                    tracks = _filter_non_latin_tracks(tracks)
            except SpotifyException as exc:
                _log(debug_steps, log_step, f"Spotify search retry without market failed for '{query}': {exc}.")
                continue
            except requests.exceptions.RequestException as exc:
                _log(debug_steps, log_step, f"Network error during Spotify search retry for '{query}': {exc}.")
                continue

        if not tracks:
            _log(debug_steps, log_step, f"No search results found for '{title}' ({artist}).")
            continue

        track = tracks[0]
        payload = _serialize_track_payload(track)
        suggestion_source = suggestion.get("seed_source") or suggestion.get("source")
        seed_label = suggestion_source or "resolved_seed"
        payload.setdefault("seed_source", seed_label)
        payload.setdefault("source", seed_label)
        resolved.append(payload)

    _log(debug_steps, log_step, f"Resolved {len(resolved)} seed tracks via Spotify search.")
    return resolved


def _score_track_basic(
    track: Dict,
    seed_artist_ids: Set[str],
    seed_year_avg: Optional[float],
    energy_label: Optional[str],
    prompt_keywords: Set[str],
    *,
    profile_cache: Optional[Dict[str, object]] = None,
    focus_artist_ids: Optional[Set[str]] = None,
    target_genre: Optional[str] = None,
) -> tuple[float, Dict[str, float]]:
    """Assign a heuristic score to rank candidates while blending affinity and novelty."""

    breakdown: Dict[str, float] = {}
    popularity = int(track.get("popularity", 40) or 0)
    popularity_score = (popularity / 100.0) * 0.45
    score = popularity_score
    breakdown["popularity"] = round(popularity_score, 4)

    artists = track.get("artists") or []
    artist_ids = {artist.get("id") for artist in artists if artist.get("id")}

    if seed_artist_ids and artist_ids.intersection(seed_artist_ids):
        seed_bonus = 0.2
        score += seed_bonus
        breakdown["seed_overlap"] = round(seed_bonus, 4)
    else:
        breakdown["seed_overlap"] = 0.0

    if focus_artist_ids and artist_ids.intersection(focus_artist_ids):
        focus_bonus = 0.3
        score += focus_bonus
        breakdown["focus_artist"] = round(focus_bonus, 4)
    else:
        breakdown["focus_artist"] = 0.0

    name_lower = (track.get("name") or "").lower()
    keyword_bonus = 0.0
    if prompt_keywords:
        keyword_hits = sum(1 for kw in prompt_keywords if kw in name_lower)
        keyword_bonus = min(keyword_hits, 2) * 0.05
        score += keyword_bonus
    breakdown["keyword_match"] = round(keyword_bonus, 4)

    year_bonus = 0.0
    energy_bonus = 0.0
    candidate_year = _extract_release_year(track)
    if seed_year_avg and candidate_year:
        year_diff = abs(candidate_year - seed_year_avg)
        year_bonus = max(0.0, (18 - year_diff) / 36.0) * 0.18
        score += year_bonus
        energy_lower = (energy_label or "").lower()
        if energy_lower == "high" and candidate_year >= seed_year_avg:
            energy_bonus = 0.05
        elif energy_lower == "low" and candidate_year <= seed_year_avg:
            energy_bonus = 0.05
        score += energy_bonus
    breakdown["year_alignment"] = round(year_bonus, 4)
    breakdown["energy_bias"] = round(energy_bonus, 4)

    cache_bonus = 0.0
    cache_genre_bonus = 0.0
    novelty_bonus = 0.0
    if profile_cache:
        track_lookup = profile_cache.get("tracks")
        if isinstance(track_lookup, dict):
            cached_record = track_lookup.get(track.get("id"))
            if isinstance(cached_record, dict):
                cache_bonus = 0.18
                score += cache_bonus

        if target_genre:
            genre_buckets = profile_cache.get("genre_buckets")
            if isinstance(genre_buckets, dict):
                genre_bucket = genre_buckets.get(target_genre)
                if isinstance(genre_bucket, dict):
                    track_ids = genre_bucket.get("track_ids") or []
                    if track.get("id") in track_ids:
                        cache_genre_bonus = 0.12
                        score += cache_genre_bonus

        artist_counts = profile_cache.get("artist_counts")
        if isinstance(artist_counts, dict) and artist_ids:
            for artist_id in artist_ids:
                play_count = int(artist_counts.get(artist_id, 0))
                if play_count == 0:
                    novelty_bonus += 0.05
                elif play_count <= 2:
                    novelty_bonus += 0.02
                elif play_count >= 6:
                    novelty_bonus -= 0.03
                else:
                    novelty_bonus -= 0.01
            score += novelty_bonus

    breakdown["cache_track_hit"] = round(cache_bonus, 4)
    breakdown["cache_genre_alignment"] = round(cache_genre_bonus, 4)
    breakdown["novelty"] = round(novelty_bonus, 4)

    total = max(score, 0.0)
    breakdown["total"] = round(total, 4)
    return total, breakdown


def get_similar_tracks(
    seed_track_ids: List[str],
    seed_artist_ids: Set[str],
    seed_year_avg: Optional[float],
    token: str,
    attributes: Dict[str, str],
    prompt_keywords: Set[str],
    *,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    market: str = "US",
    limit: int = 10,
    profile_cache: Optional[Dict[str, object]] = None,
    focus_artist_ids: Optional[Set[str]] = None,
) -> List[Dict[str, str]]:
    """Return a ranked list of tracks similar to the provided seed set."""
    if not seed_track_ids:
        _log(debug_steps, log_step, "No seed track IDs available; skipping local recommendations.")
        return []

    sp = spotipy.Spotify(auth=token)

    normalized_genre = _normalize_genre(attributes.get("genre", "pop") or "pop")
    energy_label = attributes.get("energy")

    candidate_tracks: List[Dict] = []

    playlist_candidates = _discover_playlist_seeds(
        sp,
        normalized_genre,
        market=market,
        debug_steps=debug_steps,
        log_step=log_step,
        playlist_limit=4,
        track_limit=40,
    )
    candidate_tracks.extend(playlist_candidates)

    search_queries = [f'genre:"{normalized_genre}" year:2015-2025']
    mood = attributes.get("mood")
    if mood:
        search_queries.append(f'"{mood}" {normalized_genre}')

    for search_query in search_queries:
        search_limit = min(limit * 4, 50)
        offset_cap = max(0, 100 - search_limit)
        offset = random.randint(0, offset_cap) if offset_cap else 0
        _log(
            debug_steps,
            log_step,
            f"Spotify API → search tracks: q='{search_query}', limit={search_limit}, market={market}, offset={offset}",
        )
        try:
            tracks = sp.search(
                q=search_query,
                type="track",
                limit=search_limit,
                market=market,
                offset=offset,
            ).get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
            tracks = _filter_tracks_by_artist_genre(
                sp,
                tracks,
                normalized_genre,
                debug_steps=debug_steps,
                log_step=log_step,
            )
            if _should_filter_non_latin():
                tracks = _filter_non_latin_tracks(tracks)
            candidate_tracks.extend(tracks)
            sample_names = [track.get("name") for track in tracks[:5] if track.get("name")]
            if sample_names:
                _log(debug_steps, log_step, f"Search returned {len(tracks)} candidates for query '{search_query}'. Sample: {sample_names}")
            else:
                _log(debug_steps, log_step, f"Search returned {len(tracks)} candidates for query '{search_query}'.")
        except SpotifyException as exc:
            _log(debug_steps, log_step, f"Spotify search error for '{search_query}': {exc}.")
        except requests.exceptions.RequestException as exc:
            _log(debug_steps, log_step, f"Network error during Spotify search for '{search_query}': {exc}.")

    unique_candidates: List[Dict] = []
    seen_ids: Set[str] = set(seed_track_ids)
    for track in candidate_tracks:
        track_id = track.get("id")
        if not track_id or track_id in seen_ids:
            continue
        seen_ids.add(track_id)
        unique_candidates.append(track)

    _log(debug_steps, log_step, f"Local recommender candidate pool size after filtering: {len(unique_candidates)}.")

    scored_tracks: List[tuple[float, Dict, Dict[str, float]]] = []
    artist_counts: Dict[str, int] = {}

    for track in unique_candidates:
        score, breakdown = _score_track_basic(
            track,
            seed_artist_ids,
            seed_year_avg,
            energy_label,
            prompt_keywords,
            profile_cache=profile_cache,
            focus_artist_ids=focus_artist_ids,
            target_genre=normalized_genre,
        )
        scored_tracks.append((score, track, breakdown))

    _log(debug_steps, log_step, f"Local recommender scored {len(scored_tracks)} candidates.")

    scored_tracks.sort(key=lambda item: item[0], reverse=True)

    recommendations: List[Dict[str, str]] = []
    for score, track, breakdown in scored_tracks:
        if len(recommendations) >= limit:
            break
        artists = track.get("artists", [])
        artist_names = [artist.get("name", "") for artist in artists]
        if any(artist_counts.get(name, 0) >= 2 for name in artist_names if name):
            continue
        artist_label = ", ".join(name for name in artist_names if name) or "Unknown"
        serialized = _serialize_track_payload(track)
        if artist_label and not serialized.get("artists"):
            serialized["artists"] = artist_label
        serialized["score"] = round(score, 4)
        serialized["score_breakdown"] = breakdown
        serialized["seed_artist_overlap"] = bool(seed_artist_ids and {artist.get("id") for artist in track.get("artists", []) or []}.intersection(seed_artist_ids))
        serialized["focus_artist_overlap"] = bool(
            focus_artist_ids and {artist.get("id") for artist in track.get("artists", []) or []}.intersection(focus_artist_ids)
        )
        recommendations.append(serialized)
        for name in artist_names:
            if not name:
                continue
            artist_counts[name] = artist_counts.get(name, 0) + 1

    _log(debug_steps, log_step, f"Local recommender selected {len(recommendations)} similarity-based tracks.")

    return recommendations


def create_playlist_with_tracks(
    token: str,
    track_ids: List[str],
    playlist_name: str,
    *,
    prefix: str = "",
    user_id: Optional[str] = None,
    public: bool = False,
) -> Dict[str, str]:
    """
    Create a Spotify playlist and populate it with the given track IDs.

    Returns the created playlist metadata and resolved user id.
    """
    if not track_ids:
        raise ValueError("At least one track id is required to create a playlist.")
    if not playlist_name:
        raise ValueError("A playlist name must be provided.")

    sp = spotipy.Spotify(auth=token)

    resolved_user_id = user_id
    if not resolved_user_id:
        profile = sp.current_user()
        resolved_user_id = profile.get("id")
        if not resolved_user_id:
            raise RuntimeError("Spotify user id could not be resolved.")

    normalized_prefix = prefix or ""
    cleaned_name = (playlist_name or "").strip()
    if not cleaned_name:
        raise ValueError("A playlist name must be provided.")
    playlist_title = f"{normalized_prefix}{cleaned_name}" if normalized_prefix else cleaned_name
    if len(playlist_title) > 100:
        raise ValueError("Playlist name must be 100 characters or fewer.")

    created = sp.user_playlist_create(
        user=resolved_user_id,
        name=playlist_title,
        public=public,
    )
    playlist_id = created.get("id")
    if not playlist_id:
        raise RuntimeError("Spotify did not return a playlist id.")

    # Spotify limits each request to 100 tracks max.
    chunk_size = 100
    for start in range(0, len(track_ids), chunk_size):
        batch = track_ids[start : start + chunk_size]
        # Add tracks in batches to respect Spotify API limits.
        try:
            sp.playlist_add_items(playlist_id, batch)
        except SpotifyException as exc:
            raise RuntimeError(
                f"Spotify rejected playlist items batch starting at index {start}: {exc}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(
                f"Network error while adding playlist items starting at index {start}: {exc}"
            ) from exc

    return {
        "playlist_id": playlist_id,
        "playlist_name": playlist_title,
        "user_id": resolved_user_id,
    }
