from typing import Dict, List

import spotipy
from spotipy.oauth2 import SpotifyOAuth  # noqa: F401


def _normalize_genre(raw_genre: str) -> str:
    return raw_genre.strip().lower().replace(" ", "-")


def get_spotify_recommendations(attributes: Dict[str, str], token: str) -> List[str]:
    """Fetch seed tracks from Spotify based on the parsed playlist attributes."""
    sp = spotipy.Spotify(auth=token)
    genre = attributes.get("genre", "pop") or "pop"
    energy = attributes.get("energy", "medium") or "medium"
    normalized_genre = _normalize_genre(genre)

    try:
        seed_response = sp.recommendation_genre_seeds()
        valid_genres = set(seed_response.get("genres", []))
    except spotipy.SpotifyException:
        valid_genres = set()

    if not normalized_genre or (valid_genres and normalized_genre not in valid_genres):
        normalized_genre = "pop"

    energy_levels = {"low": 0.3, "medium": 0.55, "high": 0.8}
    target_energy = energy_levels.get(energy.lower(), 0.65)

    try:
        results = sp.recommendations(
            seed_genres=[normalized_genre],
            target_energy=target_energy,
            limit=10,
        )
    except spotipy.SpotifyException:
        return []

    tracks = results.get("tracks", [])
    return [
        f"{track['name']} - {track['artists'][0]['name']}"
        for track in tracks
        if track.get("artists")
    ]
