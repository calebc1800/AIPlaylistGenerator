from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from unittest.mock import patch

from spotipy import SpotifyException

from .services.spotify_handler import (
    discover_top_tracks_for_genre,
    get_similar_tracks,
    resolve_seed_tracks,
)
from .views import _cache_key


class GeneratePlaylistViewTests(TestCase):
    """Tests for the recommender playlist generation view."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("recommender:generate_playlist")
        cache.clear()

    def test_requires_post(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_redirects_without_prompt(self):
        response = self.client.post(self.url, {"prompt": ""})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("spotify_auth:dashboard"))

    def test_redirects_without_access_token(self):
        response = self.client.post(self.url, {"prompt": "lofi coding"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("spotify_auth:login"))

    @patch("recommender.views.extract_playlist_attributes")
    @patch("recommender.views.suggest_seed_tracks")
    @patch("recommender.views.resolve_seed_tracks")
    @patch("recommender.views.get_similar_tracks")
    @patch("recommender.views.discover_top_tracks_for_genre")
    def test_generates_playlist_flow(
        self,
        mock_discover,
        mock_similar,
        mock_resolve,
        mock_suggest,
        mock_extract,
    ):
        session = self.client.session
        session["spotify_access_token"] = "token"
        session["spotify_user_id"] = "user123"
        session.save()

        mock_extract.return_value = {"mood": "upbeat", "genre": "pop", "energy": "high"}
        mock_suggest.return_value = [
            {"title": "Song A", "artist": "Artist A"},
            {"title": "Song B", "artist": "Artist B"},
        ]
        mock_resolve.return_value = [
            {"id": "1", "name": "Song A", "artists": "Artist A"},
            {"id": "2", "name": "Song B", "artists": "Artist B"},
        ]
        mock_similar.return_value = ["Song C - Artist C"]
        mock_discover.return_value = []

        response = self.client.post(self.url, {"prompt": "high energy pop"})

        self.assertEqual(response.status_code, 200)
        mock_extract.assert_called_once()
        mock_suggest.assert_called_once()
        mock_resolve.assert_called_once()
        mock_similar.assert_called_once()
        mock_discover.assert_not_called()
        page = response.content.decode()
        self.assertIn("Song A - Artist A", page)
        self.assertIn("Song C - Artist C", page)

    @patch("recommender.views.extract_playlist_attributes")
    @patch("recommender.views.suggest_seed_tracks")
    @patch("recommender.views.resolve_seed_tracks")
    @patch("recommender.views.get_similar_tracks")
    @patch("recommender.views.discover_top_tracks_for_genre")
    def test_llm_seed_fallback_discovers_tracks(
        self,
        mock_discover,
        mock_similar,
        mock_resolve,
        mock_suggest,
        mock_extract,
    ):
        session = self.client.session
        session["spotify_access_token"] = "token"
        session.save()

        mock_extract.return_value = {"mood": "calm", "genre": "ambient", "energy": "low"}
        mock_suggest.return_value = [{"title": "Ambient Song", "artist": "Someone"}]
        mock_resolve.return_value = []
        mock_discover.return_value = [
            {"id": "3", "name": "Fallback Song", "artists": "Fallback Artist"}
        ]
        mock_similar.return_value = ["Similar Song - Artist"]

        response = self.client.post(self.url, {"prompt": "calming ambient"})

        self.assertEqual(response.status_code, 200)
        mock_extract.assert_called_once()
        mock_suggest.assert_called_once()
        mock_resolve.assert_called_once()
        mock_discover.assert_called_once()
        mock_similar.assert_called_once()
        page = response.content.decode()
        self.assertIn("Fallback Song - Fallback Artist", page)
        self.assertIn("Similar Song - Artist", page)

    @patch("recommender.views.extract_playlist_attributes")
    @patch("recommender.views.suggest_seed_tracks")
    @patch("recommender.views.resolve_seed_tracks")
    @patch("recommender.views.get_similar_tracks")
    @patch("recommender.views.discover_top_tracks_for_genre")
    def test_uses_cached_playlist(
        self,
        mock_discover,
        mock_similar,
        mock_resolve,
        mock_suggest,
        mock_extract,
    ):
        session = self.client.session
        session["spotify_access_token"] = "token"
        session["spotify_user_id"] = "cache_user"
        session.save()

        cache_key = _cache_key("cache_user", "high energy pop")
        cache.set(
            cache_key,
            {
                "playlist": ["Cached Song - Artist"],
                "attributes": {"mood": "upbeat", "genre": "pop", "energy": "high"},
                "llm_suggestions": [
                    {"title": "Cached Song", "artist": "Cached Artist"}
                ],
                "resolved_seed_tracks": [
                    {"id": "1", "name": "Cached Song", "artists": "Cached Artist"}
                ],
                "seed_track_display": ["Cached Song - Cached Artist"],
                "similar_tracks": ["Similar Song - Similar Artist"],
            },
            timeout=60,
        )

        response = self.client.post(self.url, {"prompt": "high energy pop"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Cached Song - Artist", response.content.decode())
        mock_extract.assert_not_called()
        mock_suggest.assert_not_called()
        mock_resolve.assert_not_called()
        mock_similar.assert_not_called()
        mock_discover.assert_not_called()


class SpotifyHandlerTests(TestCase):
    """Unit tests for Spotify service helpers."""

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_resolve_seed_tracks_filters_market(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.search.return_value = {
            "tracks": {
                "items": [
                    {
                        "id": "1",
                        "name": "Song",
                        "artists": [{"name": "Artist"}],
                        "available_markets": ["US"],
                    }
                ]
            }
        }

        suggestions = [{"title": "Song", "artist": "Artist"}]
        results = resolve_seed_tracks(suggestions, token="token", debug_steps=[])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "1")
        mock_instance.search.assert_called_once()

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_resolve_seed_tracks_handles_no_results(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.search.return_value = {"tracks": {"items": []}}

        results = resolve_seed_tracks(
            [{"title": "Missing Song", "artist": "Artist"}],
            token="token",
            debug_steps=[],
        )

        self.assertEqual(results, [])

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_get_similar_tracks_handles_missing_seeds(self, mock_spotify):
        results = get_similar_tracks(
            [],
            token="token",
            attributes={"energy": "medium", "genre": "pop"},
            debug_steps=[],
        )

        self.assertEqual(results, [])
        mock_spotify.assert_not_called()

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_discover_top_tracks_for_genre(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.search.side_effect = [
            {
                "playlists": {
                    "items": [
                        {"id": "playlist1"},
                    ]
                }
            },
            {
                "tracks": {
                    "items": [
                        {
                            "id": "track1",
                            "name": "Track One",
                            "popularity": 80,
                            "artists": [{"name": "Artist One", "id": "artist1"}],
                            "available_markets": ["US"],
                        },
                        {
                            "id": "track2",
                            "name": "Track Two",
                            "popularity": 60,
                            "artists": [{"name": "Artist Two", "id": "artist2"}],
                            "available_markets": ["US"],
                        },
                    ]
                }
            },
        ]
        mock_instance.playlist_items.return_value = {
            "items": [
                {
                    "track": {
                        "id": "track1",
                        "name": "Track One",
                        "popularity": 80,
                        "artists": [{"name": "Artist One", "id": "artist1"}],
                        "available_markets": ["US"],
                    }
                }
            ]
        }
        mock_instance.artists.return_value = {
            "artists": [
                {"id": "artist1", "genres": ["k-pop", "dance pop"]},
                {"id": "artist2", "genres": ["j-pop"]},
            ]
        }

        results = discover_top_tracks_for_genre(
            {"genre": "k-pop"},
            token="token",
            debug_steps=[],
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "track1")

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_get_similar_tracks_uses_audio_features(self, mock_spotify):
        mock_instance = mock_spotify.return_value

        mock_instance.search.side_effect = [
            {
                "playlists": {
                    "items": [{"id": "playlist1"}],
                }
            },
            {
                "tracks": {
                    "items": [
                        {
                            "id": "cand1",
                            "name": "Candidate One",
                            "artists": [{"name": "Artist A", "id": "artistA"}],
                            "popularity": 70,
                            "available_markets": ["US"],
                            "album": {"release_date": "2023-01-01"},
                        },
                        {
                            "id": "cand2",
                            "name": "Candidate Two",
                            "artists": [{"name": "Artist B", "id": "artistB"}],
                            "popularity": 60,
                            "available_markets": ["US"],
                            "album": {"release_date": "2015-05-05"},
                        },
                    ]
                }
            },
            {
                "tracks": {"items": []},
            },
        ]

        mock_instance.playlist_items.return_value = {
            "items": [
                {
                    "track": {
                        "id": "cand3",
                        "name": "Playlist Candidate",
                        "artists": [{"name": "Artist C", "id": "artistC"}],
                        "popularity": 75,
                        "available_markets": ["US"],
                        "album": {"release_date": "2005-09-01"},
                    }
                }
            ]
        }

        mock_instance.artists.return_value = {
            "artists": [
                {"id": "artistA", "genres": ["pop"]},
                {"id": "artistB", "genres": ["pop"]},
                {"id": "artistC", "genres": ["pop"]},
                {"id": "seed_artist", "genres": ["pop"]},
            ]
        }

        feature_map = {
            "seed1": {
                "id": "seed1",
                "danceability": 0.8,
                "energy": 0.7,
                "valence": 0.6,
                "tempo": 120,
                "acousticness": 0.1,
                "instrumentalness": 0.0,
                "speechiness": 0.05,
                "loudness": -5,
            },
            "cand1": {
                "id": "cand1",
                "danceability": 0.82,
                "energy": 0.72,
                "valence": 0.58,
                "tempo": 118,
                "acousticness": 0.12,
                "instrumentalness": 0.0,
                "speechiness": 0.04,
                "loudness": -6,
            },
            "cand2": {
                "id": "cand2",
                "danceability": 0.5,
                "energy": 0.4,
                "valence": 0.3,
                "tempo": 90,
                "acousticness": 0.2,
                "instrumentalness": 0.0,
                "speechiness": 0.1,
                "loudness": -12,
            },
            "cand3": {
                "id": "cand3",
                "danceability": 0.78,
                "energy": 0.68,
                "valence": 0.62,
                "tempo": 122,
                "acousticness": 0.09,
                "instrumentalness": 0.0,
                "speechiness": 0.05,
                "loudness": -4,
            },
        }

        def audio_features_side_effect(ids):
            return [feature_map.get(tid) for tid in ids]

        mock_instance.audio_features.side_effect = audio_features_side_effect

        mock_instance.tracks.return_value = {
            "tracks": [
                {
                    "id": "seed1",
                    "album": {"release_date": "2020-01-01"},
                }
            ]
        }

        results = get_similar_tracks(
            ["seed1"],
            token="token",
            attributes={"energy": "medium", "genre": "pop", "mood": "happy"},
            debug_steps=[],
        )

        self.assertTrue(results)
        self.assertIn("Candidate One - Artist A", results[0])

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_get_similar_tracks_handles_missing_features(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.search.side_effect = [
            {"playlists": {"items": []}},
            {"tracks": {"items": []}},
        ]
        mock_instance.audio_features.return_value = [None]
        mock_instance.tracks.return_value = {
            "tracks": [
                {
                    "id": "seed1",
                    "album": {"release_date": "2020-01-01"},
                }
            ]
        }

        results = get_similar_tracks(
            ["seed1"],
            token="token",
            attributes={"energy": "medium", "genre": "pop"},
            debug_steps=[],
        )

        self.assertEqual(results, [])

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_get_similar_tracks_popularity_fallback(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.search.side_effect = [
            {"playlists": {"items": []}},
            {
                "tracks": {
                    "items": [
                        {
                            "id": "cand1",
                            "name": "Candidate One",
                            "artists": [{"name": "Artist A", "id": "artistA"}],
                            "popularity": 80,
                            "available_markets": ["US"],
                            "album": {"release_date": "2022-04-01"},
                        },
                        {
                            "id": "cand2",
                            "name": "Candidate Two",
                            "artists": [{"name": "Artist B", "id": "artistB"}],
                            "popularity": 60,
                            "available_markets": ["US"],
                            "album": {"release_date": "2010-02-02"},
                        },
                    ]
                }
            },
        ]
        mock_instance.artists.return_value = {
            "artists": [
                {"id": "artistA", "genres": ["pop"]},
                {"id": "artistB", "genres": ["pop"]},
                {"id": "seed_artist", "genres": ["pop"]},
            ]
        }
        mock_instance.audio_features.side_effect = SpotifyException(403, -1, "forbidden")
        mock_instance.tracks.return_value = {
            "tracks": [
                {
                    "id": "seed1",
                    "album": {"release_date": "2018-01-01"},
                }
            ]
        }

        results = get_similar_tracks(
            ["seed1"],
            token="token",
            attributes={"energy": "medium", "genre": "pop"},
            debug_steps=[],
        )

        self.assertTrue(results)
        self.assertEqual(results[0], "Candidate One - Artist A")
