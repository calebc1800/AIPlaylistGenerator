from typing import Dict, List

import spotipy
from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyOAuth  # noqa: F401


def _normalize_genre(raw_genre: str) -> str:
    return raw_genre.strip().lower().replace(" ", "-")


def _tracks_to_strings(tracks: List[Dict]) -> List[str]:
    return [
        f"{track['name']} - {track['artists'][0]['name']}"
        for track in tracks
        if track.get("artists")
    ]


def get_spotify_recommendations(attributes: Dict[str, str], token: str) -> List[str]:
    """Fetch seed tracks from Spotify based on the parsed playlist attributes."""
    sp = spotipy.Spotify(auth=token)
    genre = attributes.get("genre", "pop") or "pop"
    energy = attributes.get("energy", "medium") or "medium"
    normalized_genre = _normalize_genre(genre)

    energy_levels = {"low": 0.3, "medium": 0.55, "high": 0.8}
    target_energy = energy_levels.get(energy.lower(), 0.65)

    tracks: List[Dict] = []

    try:
        recommendation_result = sp.recommendations(
            seed_genres=[normalized_genre],
            target_energy=target_energy,
            limit=10,
            market="US",
        )
        tracks = recommendation_result.get("tracks", [])
    except SpotifyException:
        tracks = []

    if not tracks and normalized_genre != "pop":
        try:
            recommendation_result = sp.recommendations(
                seed_genres=["pop"],
                target_energy=target_energy,
                limit=10,
                market="US",
            )
            tracks = recommendation_result.get("tracks", [])
        except SpotifyException:
            tracks = []

    if not tracks:
        search_query = f'genre:"{normalized_genre}"' if normalized_genre else "pop"
        try:
            search_results = sp.search(q=search_query, type="track", limit=10, market="US")
            tracks = search_results.get("tracks", {}).get("items", [])
        except SpotifyException:
            tracks = []

    if not tracks and normalized_genre != "pop":
        try:
            search_results = sp.search(q="pop", type="track", limit=10, market="US")
            tracks = search_results.get("tracks", {}).get("items", [])
        except SpotifyException:
            tracks = []

    return _tracks_to_strings(tracks)
