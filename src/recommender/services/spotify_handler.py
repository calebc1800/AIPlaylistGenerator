import unicodedata
from typing import Callable, Dict, Iterable, List, Optional, Set

from django.conf import settings
from django.core.cache import cache
import spotipy
from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyOAuth  # noqa: F401


def _log(
    debug_steps: Optional[List[str]],
    log_step: Optional[Callable[[str], None]],
    message: str,
) -> None:
    if log_step:
        log_step(message)
    elif debug_steps is not None:
        debug_steps.append(message)


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
    log_step: Optional[Callable[[str], None]] = None,
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
        _log(
            debug_steps,
            log_step,
            f"Failed to fetch artist genres: {exc}.",
        )
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

    _log(
        debug_steps,
        log_step,
        f"Filtered tracks by artist genre '{normalized_genre}': {len(filtered_tracks)} remaining.",
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


AUDIO_FEATURE_CACHE_TTL = 60 * 60  # seconds


def _chunked(sequence: List[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(sequence), size):
        yield sequence[start : start + size]


def _fetch_audio_features(
    sp: spotipy.Spotify,
    track_ids: List[str],
    *,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
) -> Dict[str, Dict]:
    features: Dict[str, Dict] = {}
    ids_to_fetch: List[str] = []

    for track_id in track_ids:
        cached = cache.get(f"spotify:audio_feature:{track_id}")
        if cached:
            features[track_id] = cached
        else:
            ids_to_fetch.append(track_id)

    for chunk in _chunked(ids_to_fetch, 100):
        if chunk:
            _log(
                debug_steps,
                log_step,
                f"Spotify API → audio_features: ids={','.join(chunk[:5])}{'...' if len(chunk) > 5 else ''}",
            )
        try:
            response = sp.audio_features(chunk)
        except SpotifyException as exc:
            _log(
                debug_steps,
                log_step,
                f"Spotify audio_features error: {exc}.",
            )
            continue

        for feature in response or []:
            if not feature or not feature.get("id"):
                continue
            features[feature["id"]] = feature
            cache.set(
                f"spotify:audio_feature:{feature['id']}",
                feature,
                AUDIO_FEATURE_CACHE_TTL,
            )
        _log(
            debug_steps,
            log_step,
            f"Cached audio features for chunk of {len(response or [])} tracks.",
        )

    return features


def _vectorize_audio_feature(feature: Dict) -> Optional[List[float]]:
    if not feature:
        return None
    tempo = feature.get("tempo") or 0.0
    loudness = feature.get("loudness") or -60.0
    return [
        feature.get("danceability", 0.0),
        feature.get("energy", 0.0),
        feature.get("valence", 0.0),
        min(max(tempo / 200.0, 0.0), 2.0),
        feature.get("acousticness", 0.0),
        feature.get("instrumentalness", 0.0),
        feature.get("speechiness", 0.0),
        min(max((loudness + 60.0) / 60.0, 0.0), 1.0),
    ]


def _compute_centroid(features: Iterable[Dict]) -> Optional[List[float]]:
    vectors: List[List[float]] = []
    for feature in features:
        vec = _vectorize_audio_feature(feature)
        if vec is not None:
            vectors.append(vec)
    if not vectors:
        return None
    length = len(vectors[0])
    centroid = [0.0] * length
    for vec in vectors:
        for idx, value in enumerate(vec):
            centroid[idx] += value
    count = float(len(vectors))
    return [value / count for value in centroid]


def _score_track(
    feature: Dict,
    centroid: List[float],
    *,
    target_energy: float,
    track: Dict,
) -> Optional[float]:
    vec = _vectorize_audio_feature(feature)
    if vec is None or centroid is None:
        return None
    if len(vec) != len(centroid):
        return None

    avg_diff = sum(abs(a - b) for a, b in zip(vec, centroid)) / len(vec)
    similarity = max(0.0, 1.0 - avg_diff)

    energy_penalty = abs((feature.get("energy") or 0.0) - target_energy)
    score = similarity - 0.3 * energy_penalty

    popularity = (track.get("popularity", 40) or 0) / 100.0
    score = (score * 0.7) + (popularity * 0.3)

    return score


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
    query = f"{normalized_genre.replace('-', ' ')} hits"
    _log(
        debug_steps,
        log_step,
        f"Spotify API → search playlists: q='{query}', limit={playlist_limit}",
    )
    try:
        playlists = sp.search(q=query, type="playlist", limit=playlist_limit)
        playlist_items = playlists.get("playlists", {}).get("items", [])
    except SpotifyException as exc:
        playlist_items = []
        _log(
            debug_steps,
            log_step,
            f"Spotify playlist search failed for genre '{normalized_genre}': {exc}.",
        )

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
    sample_names = [track["name"] for track in playlist_tracks[:5] if track.get("name")]
    if sample_names:
        _log(
            debug_steps,
            log_step,
            f"Playlist seed sample: {sample_names}",
        )

    if len(selected) < seed_limit:
        tracks: List[Dict] = []
        try:
            _log(
                debug_steps,
                log_step,
                f"Spotify API → search tracks (genre seed): q='{query}', limit={search_limit}, market={market}",
            )
            results = sp.search(q=query, type="track", limit=search_limit, market=market)
            tracks = results.get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
        except SpotifyException as exc:
            _log(
                debug_steps,
                log_step,
                f"Spotify search for genre seeds failed: {exc}.",
            )

        if not tracks:
            try:
                _log(
                    debug_steps,
                    log_step,
                    f"Spotify API → search tracks (no market): q='{query}', limit={search_limit}",
                )
                results = sp.search(q=query, type="track", limit=search_limit)
                tracks = results.get("tracks", {}).get("items", [])
            except SpotifyException as exc:
                _log(
                    debug_steps,
                    log_step,
                    f"Spotify search without market failed: {exc}.",
                )
                tracks = []

        if tracks:
            tracks = _filter_tracks_by_artist_genre(
                sp,
                tracks,
                normalized_genre,
                debug_steps=debug_steps,
                log_step=log_step,
            )
            tracks = _filter_non_latin_tracks(tracks)
            tracks.sort(key=lambda t: t.get("popularity", 0), reverse=True)
            sample_names = [track["name"] for track in tracks[:5] if track.get("name")]
            if sample_names:
                _log(
                    debug_steps,
                    log_step,
                    f"Search seed sample: {sample_names}",
                )

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

        tracks: List[Dict] = []
        try:
            _log(
                debug_steps,
                log_step,
                f'Spotify API → search track: q="{query}", limit=5, market={market}',
            )
            results = sp.search(q=query, type="track", limit=5, market=market)
            tracks = results.get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
            tracks = _filter_non_latin_tracks(tracks)
        except SpotifyException as exc:
            _log(
                debug_steps,
                log_step,
                f"Spotify search failed for '{query}' with market {market}: {exc}.",
            )

        if not tracks:
            try:
                _log(
                    debug_steps,
                    log_step,
                    f'Spotify API → search track (no market): q="{query}", limit=5',
                )
                results = sp.search(q=query, type="track", limit=5)
                tracks = _filter_non_latin_tracks(results.get("tracks", {}).get("items", []))
            except SpotifyException as exc:
                _log(
                    debug_steps,
                    log_step,
                    f"Spotify search retry without market failed for '{query}': {exc}.",
                )
                continue

        if not tracks:
            _log(
                debug_steps,
                log_step,
                f"No search results found for '{query}'.",
            )
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

    _log(
        debug_steps,
        log_step,
        f"Resolved {len(resolved)} seed tracks via Spotify search.",
    )

    return resolved


def get_similar_tracks(
    seed_track_ids: List[str],
    token: str,
    attributes: Dict[str, str],
    *,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    market: str = "US",
    limit: int = 10,
) -> List[str]:
    """Build local recommendations using Spotify audio features and similarity scoring."""
    if not seed_track_ids:
        _log(
            debug_steps,
            log_step,
            "No seed track IDs available; skipping local recommendations.",
        )
        return []

    sp = spotipy.Spotify(auth=token)

    energy_levels = {"low": 0.3, "medium": 0.55, "high": 0.8}
    target_energy = energy_levels.get(attributes.get("energy", "").lower(), 0.65)
    normalized_genre = _normalize_genre(attributes.get("genre", "pop") or "pop")

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

    search_queries = [
        f'genre:"{normalized_genre}" year:2015-2025',
    ]
    mood = attributes.get("mood")
    if mood:
        search_queries.append(f'"{mood}" {normalized_genre}')

    for search_query in search_queries:
        _log(
            debug_steps,
            log_step,
            f"Spotify API → search tracks: q='{search_query}', limit={min(limit * 4, 50)}, market={market}",
        )
        try:
            search_results = sp.search(
                q=search_query,
                type="track",
                limit=min(limit * 4, 50),
                market=market,
            )
            tracks = search_results.get("tracks", {}).get("items", [])
            tracks = _filter_by_market(tracks, market)
            tracks = _filter_tracks_by_artist_genre(
                sp,
                tracks,
                normalized_genre,
                debug_steps=debug_steps,
                log_step=log_step,
            )
            tracks = _filter_non_latin_tracks(tracks)
            candidate_tracks.extend(tracks)
            sample_names = [track.get("name") for track in tracks[:5] if track.get("name")]
            if sample_names:
                _log(
                    debug_steps,
                    log_step,
                    f"Search returned {len(tracks)} candidates for query '{search_query}'. Sample: {sample_names}",
                )
            else:
                _log(
                    debug_steps,
                    log_step,
                    f"Search returned {len(tracks)} candidates for query '{search_query}'.",
                )
        except SpotifyException as exc:
            _log(
                debug_steps,
                log_step,
                f"Spotify search error for '{search_query}': {exc}.",
            )

    unique_candidates: List[Dict] = []
    seen_ids: Set[str] = set(seed_track_ids)
    for track in candidate_tracks:
        track_id = track.get("id")
        if not track_id or track_id in seen_ids:
            continue
        seen_ids.add(track_id)
        unique_candidates.append(track)

    _log(
        debug_steps,
        log_step,
        f"Local recommender candidate pool size after filtering: {len(unique_candidates)}.",
    )

    seed_features = _fetch_audio_features(
        sp,
        list(dict.fromkeys(seed_track_ids)),
        debug_steps=debug_steps,
        log_step=log_step,
    )
    centroid = _compute_centroid(seed_features.values())
    if centroid is None:
        _log(
            debug_steps,
            log_step,
            "Seed audio features unavailable; ranking candidates by popularity.",
        )
        fallback_recommendations: List[str] = []
        artist_counts: Dict[str, int] = {}
        sorted_candidates = sorted(
            unique_candidates,
            key=lambda track: track.get("popularity", 0),
            reverse=True,
        )
        for track in sorted_candidates:
            if len(fallback_recommendations) >= limit:
                break
            artist_names = [artist.get("name", "") for artist in track.get("artists", [])]
            if any(artist_counts.get(name, 0) >= 2 for name in artist_names if name):
                continue
            artist_label = ", ".join(name for name in artist_names if name) or "Unknown"
            fallback_recommendations.append(
                f"{track.get('name', 'Unknown')} - {artist_label}"
            )
            for name in artist_names:
                if not name:
                    continue
                artist_counts[name] = artist_counts.get(name, 0) + 1

        _log(
            debug_steps,
            log_step,
            f"Local recommender popularity fallback selected {len(fallback_recommendations)} tracks.",
        )
        return fallback_recommendations

    candidate_ids = [track["id"] for track in unique_candidates]
    candidate_features = _fetch_audio_features(
        sp,
        candidate_ids,
        debug_steps=debug_steps,
        log_step=log_step,
    )

    scored_tracks: List[tuple[float, Dict]] = []
    artist_counts: Dict[str, int] = {}

    for track in unique_candidates:
        feature = candidate_features.get(track["id"])
        if not feature:
            continue
        score = _score_track(
            feature,
            centroid,
            target_energy=target_energy,
            track=track,
        )
        if score is None:
            continue
        scored_tracks.append((score, track))

    _log(
        debug_steps,
        log_step,
        f"Local recommender scored {len(scored_tracks)} candidates.",
    )

    scored_tracks.sort(key=lambda item: item[0], reverse=True)

    recommendations: List[str] = []
    for score, track in scored_tracks:
        if len(recommendations) >= limit:
            break
        artist_names = [artist.get("name", "") for artist in track.get("artists", [])]
        if any(artist_counts.get(name, 0) >= 2 for name in artist_names if name):
            continue
        artist_label = ", ".join(name for name in artist_names if name) or "Unknown"
        recommendations.append(
            f"{track.get('name', 'Unknown')} - {artist_label}"
        )
        for name in artist_names:
            if not name:
                continue
            artist_counts[name] = artist_counts.get(name, 0) + 1

    _log(
        debug_steps,
        log_step,
        f"Local recommender selected {len(recommendations)} similarity-based tracks.",
    )

    return recommendations
