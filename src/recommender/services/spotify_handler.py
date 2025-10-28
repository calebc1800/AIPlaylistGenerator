"""Spotify helper utilities for the recommender app."""

import re
import unicodedata
from typing import Callable, Dict, Iterable, List, Optional, Set

from django.conf import settings
import spotipy
from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyOAuth  # noqa: F401


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
    """Normalize a genre string into a lowercase hyphenated token."""
    return raw_genre.strip().lower().replace(" ", "-")


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

    try:
        response = sp.artists(list(dict.fromkeys(artist_ids))[:50])
    except SpotifyException as exc:
        _log(debug_steps, log_step, f"Failed to fetch artist genres: {exc}.")
        return tracks

    artist_details = {
        artist.get("id"): artist.get("genres", [])
        for artist in response.get("artists", [])
    }

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
    date = album.get("release_date")
    if not date:
        return None
    year_str = date.split("-")[0]
    return int(year_str) if year_str.isdigit() else None


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
    query = f"{normalized_genre.replace('-', ' ')} hits"
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
            _log(
                debug_steps,
                log_step,
                f"Spotify API → search tracks (genre seed): q='{query}', limit={search_limit}, market={market}",
            )
            tracks = sp.search(q=query, type="track", limit=search_limit, market=market).get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
        except SpotifyException as exc:
            _log(debug_steps, log_step, f"Spotify search for genre seeds failed: {exc}.")

        if not tracks:
            try:
                _log(
                    debug_steps,
                    log_step,
                    f"Spotify API → search tracks (no market): q='{query}', limit={search_limit}",
                )
                tracks = sp.search(q=query, type="track", limit=search_limit).get("tracks", {}).get("items", [])
            except SpotifyException as exc:
                _log(debug_steps, log_step, f"Spotify search without market failed: {exc}.")
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

        if not tracks:
            try:
                _log(debug_steps, log_step, f'Spotify API → search track (no market): q="{query}", limit=5')
                tracks = sp.search(q=query, type="track", limit=5).get("tracks", {}).get("items", [])
                if _should_filter_non_latin():
                    tracks = _filter_non_latin_tracks(tracks)
            except SpotifyException as exc:
                _log(debug_steps, log_step, f"Spotify search retry without market failed for '{query}': {exc}.")
                continue

        if not tracks:
            _log(debug_steps, log_step, f"No search results found for '{title}' ({artist}).")
            continue

        track = tracks[0]
        artist_ids = [artist.get("id") for artist in track.get("artists", []) if artist.get("id")]
        resolved.append(
            {
                "id": track["id"],
                "name": track["name"],
                "artists": ", ".join(artist.get("name", "") for artist in track.get("artists", [])),
                "artist_ids": artist_ids,
                "year": _extract_release_year(track),
            }
        )

    _log(debug_steps, log_step, f"Resolved {len(resolved)} seed tracks via Spotify search.")
    return resolved


def _score_track_basic(
    track: Dict,
    seed_artist_ids: Set[str],
    seed_year_avg: Optional[float],
    energy_label: Optional[str],
    prompt_keywords: Set[str],
) -> float:
    """Assign a heuristic score to a candidate track to rank recommendations."""
    score = (track.get("popularity", 40) or 0) / 100.0 * 0.5

    artists = track.get("artists") or []
    if seed_artist_ids and any(artist.get("id") in seed_artist_ids for artist in artists):
        score += 0.25

    name_lower = (track.get("name") or "").lower()
    if prompt_keywords:
        keyword_hits = sum(1 for kw in prompt_keywords if kw in name_lower)
        score += min(keyword_hits, 2) * 0.05

    candidate_year = _extract_release_year(track)
    if seed_year_avg and candidate_year:
        year_diff = abs(candidate_year - seed_year_avg)
        score += max(0.0, (20 - year_diff) / 40.0) * 0.2
        energy_lower = (energy_label or "").lower()
        if energy_lower == "high" and candidate_year >= seed_year_avg:
            score += 0.05
        elif energy_lower == "low" and candidate_year <= seed_year_avg:
            score += 0.05

    return score


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
        _log(debug_steps, log_step, f"Spotify API → search tracks: q='{search_query}', limit={min(limit * 4, 50)}, market={market}")
        try:
            tracks = sp.search(
                q=search_query,
                type="track",
                limit=min(limit * 4, 50),
                market=market,
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

    unique_candidates: List[Dict] = []
    seen_ids: Set[str] = set(seed_track_ids)
    for track in candidate_tracks:
        track_id = track.get("id")
        if not track_id or track_id in seen_ids:
            continue
        seen_ids.add(track_id)
        unique_candidates.append(track)

    _log(debug_steps, log_step, f"Local recommender candidate pool size after filtering: {len(unique_candidates)}.")

    scored_tracks: List[tuple[float, Dict]] = []
    artist_counts: Dict[str, int] = {}

    for track in unique_candidates:
        score = _score_track_basic(track, seed_artist_ids, seed_year_avg, energy_label, prompt_keywords)
        scored_tracks.append((score, track))

    _log(debug_steps, log_step, f"Local recommender scored {len(scored_tracks)} candidates.")

    scored_tracks.sort(key=lambda item: item[0], reverse=True)

    recommendations: List[Dict[str, str]] = []
    for score, track in scored_tracks:
        if len(recommendations) >= limit:
            break
        artists = track.get("artists", [])
        artist_names = [artist.get("name", "") for artist in artists]
        if any(artist_counts.get(name, 0) >= 2 for name in artist_names if name):
            continue
        artist_label = ", ".join(name for name in artist_names if name) or "Unknown"
        recommendations.append(
            {
                "id": track.get("id"),
                "name": track.get("name", "Unknown"),
                "artists": artist_label,
            }
        )
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
    playlist_title = f"{normalized_prefix}{playlist_name}" if normalized_prefix else playlist_name

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
        sp.playlist_add_items(playlist_id, batch)

    return {
        "playlist_id": playlist_id,
        "playlist_name": playlist_title,
        "user_id": resolved_user_id,
    }
