from django.test import TestCase, Client
from django.urls import reverse
from django.core.cache import cache
from unittest.mock import patch

from spotipy.exceptions import SpotifyException

from .services.spotify_handler import get_spotify_recommendations
from .views import _cache_key


class GeneratePlaylistViewTests(TestCase):
    """Tests for the recommender playlist generation view."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("recommender:generate_playlist")
        cache.clear()

    def test_requires_post(self):
        """Ensure non-POST requests are rejected."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_redirects_without_prompt(self):
        """Empty prompts should send users back to the dashboard."""
        response = self.client.post(self.url, {"prompt": ""})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("spotify_auth:dashboard"))

    def test_redirects_without_access_token(self):
        """If the session lacks a Spotify token, redirect to login."""
        response = self.client.post(self.url, {"prompt": "lofi coding"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("spotify_auth:login"))

    @patch("recommender.views.refine_playlist")
    @patch("recommender.views.get_spotify_recommendations")
    @patch("recommender.views.extract_playlist_attributes")
    def test_generates_playlist_flow(
        self, mock_extract, mock_get_recs, mock_refine
    ):
        """Happy path: LLM + Spotify calls produce a playlist."""
        session = self.client.session
        session["spotify_access_token"] = "token"
        session["spotify_user_id"] = "user123"
        session.save()

        mock_extract.return_value = {"mood": "upbeat", "genre": "pop", "energy": "high"}
        mock_get_recs.return_value = ["Song A - Artist A"]
        mock_refine.return_value = [
            "Song A - Artist A",
            "Song B - Artist B",
        ]

        response = self.client.post(self.url, {"prompt": "high energy pop"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Song B - Artist B", response.content.decode())
        mock_extract.assert_called_once_with("high energy pop")
        mock_get_recs.assert_called_once()
        mock_refine.assert_called_once()

    @patch("recommender.views.refine_playlist")
    @patch("recommender.views.get_spotify_recommendations")
    @patch("recommender.views.extract_playlist_attributes")
    def test_empty_seed_tracks_skips_refine(
        self, mock_extract, mock_get_recs, mock_refine
    ):
        """No seed tracks should render an empty list without refining."""
        session = self.client.session
        session["spotify_access_token"] = "token"
        session.save()

        mock_extract.return_value = {"mood": "calm", "genre": "ambient", "energy": "low"}
        mock_get_recs.return_value = []

        response = self.client.post(self.url, {"prompt": "calming ambient"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("No playlist could be generated", response.content.decode())
        mock_extract.assert_called_once()
        mock_get_recs.assert_called_once()
        mock_refine.assert_not_called()

    @patch("recommender.views.refine_playlist")
    @patch("recommender.views.get_spotify_recommendations")
    @patch("recommender.views.extract_playlist_attributes")
    def test_uses_cached_playlist(
        self, mock_extract, mock_get_recs, mock_refine
    ):
        """Cached results should be served without re-running services."""
        session = self.client.session
        session["spotify_access_token"] = "token"
        session["spotify_user_id"] = "cache_user"
        session.save()

        cache_key = _cache_key("cache_user", "high energy pop")
        cache.set(cache_key, ["Cached Song - Artist"], timeout=60)

        response = self.client.post(self.url, {"prompt": "high energy pop"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Cached Song - Artist", response.content.decode())
        mock_extract.assert_not_called()
        mock_get_recs.assert_not_called()
        mock_refine.assert_not_called()


class SpotifyHandlerTests(TestCase):
    """Unit tests for Spotify service helpers."""

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_fallback_to_pop_on_recommendation_error(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.recommendation_genre_seeds.return_value = {"genres": ["pop"]}
        mock_instance.recommendations.side_effect = [
            SpotifyException(404, -1, "not found"),
            {"tracks": [{"name": "Song", "artists": [{"name": "Artist"}]}]},
        ]

        results = get_spotify_recommendations(
            {"genre": "nonexistent genre", "energy": "medium"},
            token="token",
        )

        self.assertEqual(results, ["Song - Artist"])
        self.assertEqual(mock_instance.recommendations.call_count, 2)

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_seed_fetch_failure_defaults_to_pop(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.recommendation_genre_seeds.side_effect = SpotifyException(
            404, -1, "not found"
        )
        mock_instance.recommendations.side_effect = [
            SpotifyException(404, -1, "not found"),
            {"tracks": [{"name": "Song", "artists": [{"name": "Artist"}]}]},
        ]

        results = get_spotify_recommendations(
            {"genre": "k-pop", "energy": "high"},
            token="token",
        )

        self.assertEqual(results, ["Song - Artist"])
        self.assertEqual(mock_instance.recommendations.call_count, 2)
