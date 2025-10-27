import unicodedata
from typing import Dict, Iterable, List, Optional, Set

from django.conf import settings
import spotipy
from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyOAuth  # noqa: F401


def _normalize_genre(raw_genre: str) -> str:
    return raw_genre.strip().lower().replace(" ", "-")


def _genre_variants(normalized_genre: str) -> Set[str]:
    if not normalized_genre:
        return set()
    base = normalized_genre.replace("-", " ")
    compact = base.replace(" ", "")
    variants = {normalized_genre, base, compact}
    if normalized_genre.endswith("-music"):
        variants.add(normalized_genre[:-6])
    if normalized_genre == "r-b":
        variants.update({"r&b", "rb"})
    if normalized_genre == "hip-hop":
        variants.add("hiphop")
    return {v for v in variants if v}


def _tracks_to_strings(tracks: List[Dict]) -> List[str]:
    return [
        f"{track['name']} - {track['artists'][0]['name']}"
        for track in tracks
        if track.get("artists")
    ]


def _filter_by_market(tracks: List[Dict], market: str) -> List[Dict]:
    filtered = []
    for track in tracks:
        markets = track.get("available_markets")
        if not markets or market in markets:
            filtered.append(track)
    return filtered


def _filter_tracks_by_artist_genre(
    sp: spotipy.Spotify,
    tracks: List[Dict],
    normalized_genre: str,
    *,
    debug_steps: Optional[List[str]] = None,
    popularity_threshold: int = 45,
) -> List[Dict]:
    if not tracks:
        return []

    genre_aliases = _genre_variants(normalized_genre)

    artist_id_map = {}
    artist_ids: List[str] = []
    for track in tracks:
        ids = [
            artist.get("id")
            for artist in track.get("artists", [])
            if artist.get("id")
        ]
        if ids:
            artist_id_map[track["id"]] = ids
            for artist_id in ids:
                if artist_id and artist_id not in artist_ids:
                    artist_ids.append(artist_id)

    if not artist_ids:
        return tracks

    artist_details: Dict[str, List[str]] = {}
    try:
        for chunk_start in range(0, len(artist_ids), 50):
            chunk = artist_ids[chunk_start : chunk_start + 50]
            response = sp.artists(chunk)
            for artist in response.get("artists", []):
                artist_details[artist["id"]] = artist.get("genres", [])
    except SpotifyException as exc:
        if debug_steps is not None:
            debug_steps.append(f"Failed to fetch artist genres: {exc}.")
        return tracks

    target = normalized_genre.replace("-", "")
    filtered_tracks: List[Dict] = []
    for track in tracks:
        if track.get("popularity", 0) < popularity_threshold:
            continue
        matched = False
        for artist_id in artist_id_map.get(track["id"], []):
            genres = artist_details.get(artist_id, [])
            for genre in genres:
                normalized = genre.lower()
                canonical = normalized.replace(" ", "").replace("-", "")
                if (
                    (target and target in canonical)
                    or any(
                        alias == normalized
                        or alias == canonical
                        or alias in canonical
                        for alias in genre_aliases
                    )
                ):
                    matched = True
                    break
            if matched:
                break
        if matched:
            filtered_tracks.append(track)

    if debug_steps is not None:
        debug_steps.append(
            f"Filtered tracks by artist genre '{normalized_genre}': {len(filtered_tracks)} remaining."
        )

    return filtered_tracks or tracks


def _is_mostly_latin(text: str) -> bool:
    if not text:
        return True
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return True
    latin = sum(
        1
        for c in alpha_chars
        if "LATIN" in unicodedata.name(c, "")
    )
    return latin / len(alpha_chars) >= 0.4


def _filter_non_latin_tracks(tracks: Iterable[Dict]) -> List[Dict]:
    filtered: List[Dict] = []
    for track in tracks:
        if _is_mostly_latin(track.get("name", "")):
            filtered.append(track)
    return filtered


def _discover_playlist_seeds(
    sp: spotipy.Spotify,
    normalized_genre: str,
    *,
    market: str,
    debug_steps: Optional[List[str]] = None,
    playlist_limit: int = 3,
    track_limit: int = 40,
) -> List[Dict]:
    query = f"{normalized_genre.replace('-', ' ')} hits"
    if debug_steps is not None:
        debug_steps.append(
            f"Spotify API → search playlists: q='{query}', limit={playlist_limit}"
        )
    try:
        playlists = sp.search(q=query, type="playlist", limit=playlist_limit)
        playlist_items = playlists.get("playlists", {}).get("items", [])
    except SpotifyException as exc:
        playlist_items = []
        if debug_steps is not None:
            debug_steps.append(
                f"Spotify playlist search failed for genre '{normalized_genre}': {exc}."
            )

    collected: List[Dict] = []
    seen_ids: Set[str] = set()

    for playlist in playlist_items:
        if not isinstance(playlist, dict):
            continue
        playlist_id = playlist.get("id")
        if not playlist_id:
            continue
        if debug_steps is not None:
            debug_steps.append(
                f"Spotify API → playlist_items: playlist_id={playlist_id}, limit={track_limit}, market={market}"
            )
        try:
            response = sp.playlist_items(playlist_id, limit=track_limit, market=market)
            items = response.get("items", [])
        except SpotifyException:
            try:
                response = sp.playlist_items(playlist_id, limit=track_limit)
                items = response.get("items", [])
            except SpotifyException:
                continue

        for entry in items:
            track = entry.get("track")
            if not track or not track.get("id"):
                continue
            if track["id"] in seen_ids:
                continue
            seen_ids.add(track["id"])
            collected.append(track)

    if debug_steps is not None:
        debug_steps.append(
            f"Collected {len(collected)} tracks from playlists for genre '{normalized_genre}'."
        )

    return collected


def discover_top_tracks_for_genre(
    attributes: Dict[str, str],
    token: str,
    *,
    debug_steps: Optional[List[str]] = None,
    market: str = "US",
    seed_limit: int = 5,
    search_limit: int = 50,
) -> List[Dict[str, str]]:
    sp = spotipy.Spotify(auth=token)
    normalized_genre = _normalize_genre(attributes.get("genre", "pop") or "pop")
    query = f'genre:"{normalized_genre}"'

    playlist_tracks = _discover_playlist_seeds(
        sp,
        normalized_genre,
        market=market,
        debug_steps=debug_steps,
    )
    playlist_tracks = _filter_tracks_by_artist_genre(
        sp,
        playlist_tracks,
        normalized_genre,
        debug_steps=debug_steps,
    )
    playlist_tracks = _filter_non_latin_tracks(playlist_tracks)

    playlist_tracks.sort(key=lambda t: t.get("popularity", 0), reverse=True)

    def _collect(tracks: List[Dict]) -> List[Dict[str, str]]:
        selected: List[Dict[str, str]] = []
        seen_ids: Set[str] = set()
        for track in tracks:
            if len(selected) >= seed_limit:
                break
            if track["id"] in seen_ids:
                continue
            seen_ids.add(track["id"])
            selected.append(
                {
                    "id": track["id"],
                    "name": track["name"],
                    "artists": ", ".join(
                        artist["name"] for artist in track.get("artists", [])
                    ),
                }
            )
        return selected

    selected = _collect(playlist_tracks)

    if len(selected) < seed_limit:
        tracks: List[Dict] = []
        try:
            if debug_steps is not None:
                debug_steps.append(
                    f"Spotify API → search tracks (genre seed): q='{query}', limit={search_limit}, market={market}"
                )
            results = sp.search(q=query, type="track", limit=search_limit, market=market)
            tracks = results.get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
        except SpotifyException as exc:
            if debug_steps is not None:
                debug_steps.append(f"Spotify search for genre seeds failed: {exc}.")

        if not tracks:
            try:
                if debug_steps is not None:
                    debug_steps.append(
                        f"Spotify API → search tracks (no market): q='{query}', limit={search_limit}"
                    )
                results = sp.search(q=query, type="track", limit=search_limit)
                tracks = results.get("tracks", {}).get("items", [])
            except SpotifyException as exc:
                if debug_steps is not None:
                    debug_steps.append(f"Spotify search without market failed: {exc}.")
                tracks = []

        if tracks:
            tracks = _filter_tracks_by_artist_genre(
                sp,
                tracks,
                normalized_genre,
                debug_steps=debug_steps,
            )
            tracks = _filter_non_latin_tracks(tracks)
            tracks.sort(key=lambda t: t.get("popularity", 0), reverse=True)

            additional = _collect(tracks)
            for track in additional:
                if track not in selected and len(selected) < seed_limit:
                    selected.append(track)

    if debug_steps is not None:
        debug_steps.append(
            f"Discovered {len(selected)} top tracks for genre '{normalized_genre}'."
        )

    return selected


def resolve_seed_tracks(
    suggestions: List[Dict[str, str]],
    token: str,
    *,
    debug_steps: Optional[List[str]] = None,
    market: str = "US",
    limit: int = 5,
) -> List[Dict[str, str]]:
    """Resolve LLM-suggested tracks to concrete Spotify IDs."""
    sp = spotipy.Spotify(auth=token)
    resolved: List[Dict[str, str]] = []

    for suggestion in suggestions:
        if len(resolved) >= limit:
            break

        title = suggestion.get("title", "").strip()
        artist = suggestion.get("artist", "").strip()
        if not title:
            continue

        query_parts = [f'track:"{title}"']
        if artist:
            query_parts.append(f'artist:"{artist}"')
        query = " ".join(query_parts)

        tracks = []

        try:
            if debug_steps is not None:
                debug_steps.append(
                    f'Spotify API → search track: q="{query}", limit=5, market={market}'
                )
            results = sp.search(q=query, type="track", limit=5, market=market)
            tracks = results.get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
            tracks = _filter_non_latin_tracks(tracks)
        except SpotifyException as exc:
            if debug_steps is not None:
                debug_steps.append(f"Spotify search failed for '{query}' with market {market}: {exc}.")

        if not tracks:
            try:
                if debug_steps is not None:
                    debug_steps.append(
                        f'Spotify API → search track (no market): q="{query}", limit=5'
                    )
                results = sp.search(q=query, type="track", limit=5)
                tracks = _filter_non_latin_tracks(results.get("tracks", {}).get("items", []))
            except SpotifyException as exc:
                if debug_steps is not None:
                    debug_steps.append(f"Spotify search retry without market failed for '{query}': {exc}.")
                continue

        if not tracks and debug_steps is not None:
            debug_steps.append(f"No search results found for '{query}'.")
        if not tracks:
            continue

        track = tracks[0]
        resolved.append(
            {
                "id": track["id"],
                "name": track["name"],
                "artists": ", ".join(artist["name"] for artist in track.get("artists", [])),
            }
        )

    if debug_steps is not None:
        debug_steps.append(f"Resolved {len(resolved)} seed tracks via Spotify search.")

    return resolved


def get_similar_tracks(
    seed_track_ids: List[str],
    token: str,
    attributes: Dict[str, str],
    *,
    debug_steps: Optional[List[str]] = None,
    market: str = "US",
    limit: int = 10,
) -> List[str]:
    """Fetch Spotify recommendations similar to the provided seed track IDs."""
    if not seed_track_ids:
        if debug_steps is not None:
            debug_steps.append("No seed track IDs available; skipping Spotify recommendations.")
        return []

    sp = spotipy.Spotify(auth=token)

    energy_levels = {"low": 0.3, "medium": 0.55, "high": 0.8}
    target_energy = energy_levels.get(attributes.get("energy", "").lower(), 0.65)
    normalized_genre = _normalize_genre(attributes.get("genre", "pop") or "pop")

    tracks: List[Dict] = []
    use_recommendations = getattr(settings, "SPOTIFY_USE_RECOMMENDATIONS", False)

    seed_ids = seed_track_ids[:5]
    if use_recommendations:
        if debug_steps is not None:
            debug_steps.append(
                f"Spotify recommendations request: seeds={seed_ids}, energy={target_energy}, market={market}"
            )
        try:
            recommendation_result = sp.recommendations(
                seed_tracks=seed_ids,
                target_energy=target_energy,
                limit=limit,
            )
            tracks = recommendation_result.get("tracks", [])
            tracks = _filter_by_market(tracks, market)
            tracks = _filter_tracks_by_artist_genre(
                sp, tracks, normalized_genre, debug_steps=debug_steps
            )
            if debug_steps is not None:
                debug_steps.append(
                    f"Received {len(tracks)} tracks from Spotify recommendations."
                )
        except SpotifyException as exc:
            tracks = []
            if debug_steps is not None:
                debug_steps.append(f"Spotify recommendations error: {exc}.")

        if not tracks:
            try:
                recommendation_result = sp.recommendations(
                    seed_genres=[normalized_genre],
                    target_energy=target_energy,
                    limit=limit,
                )
                tracks = recommendation_result.get("tracks", [])
                tracks = _filter_by_market(tracks, market)
                tracks = _filter_tracks_by_artist_genre(
                    sp, tracks, normalized_genre, debug_steps=debug_steps
                )
                if debug_steps is not None:
                    debug_steps.append(
                        f"Genre-based recommendations returned {len(tracks)} tracks."
                    )
            except SpotifyException as exc:
                if debug_steps is not None:
                    debug_steps.append(f"Spotify genre recommendation error: {exc}.")
    else:
        if debug_steps is not None:
            debug_steps.append("Skipping recommendations API per settings; falling back to search.")

    if not tracks:
        search_terms = [
            f'genre:"{normalized_genre}"',
            "year:2015-2025",
        ]
        search_query = " ".join(search_terms)
        if debug_steps is not None:
            debug_steps.append(f"Falling back to Spotify search with query '{search_query}'.")
        try:
            search_results = sp.search(q=search_query, type="track", limit=limit, market=market)
            tracks = search_results.get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
            tracks = _filter_tracks_by_artist_genre(
                sp, tracks, normalized_genre, debug_steps=debug_steps
            )
            if debug_steps is not None:
                debug_steps.append(
                    f"Search returned {len(tracks)} market-filtered tracks for query '{search_query}'."
                )
        except SpotifyException as exc:
            tracks = []
            if debug_steps is not None:
                debug_steps.append(f"Spotify search fallback failed: {exc}.")

    if not tracks:
        try:
            search_results = sp.search(
                q=f'"{attributes.get("mood", "")}" {normalized_genre}',
                type="track",
                limit=limit,
            )
            tracks = search_results.get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
            tracks = _filter_tracks_by_artist_genre(
                sp, tracks, normalized_genre, debug_steps=debug_steps
            )
            if debug_steps is not None:
                debug_steps.append(
                    f"Mood-based search returned {len(tracks)} tracks."
                )
        except SpotifyException as exc:
            tracks = []
            if debug_steps is not None:
                debug_steps.append(f"Mood-based search failed: {exc}.")

    return _tracks_to_strings(tracks)
