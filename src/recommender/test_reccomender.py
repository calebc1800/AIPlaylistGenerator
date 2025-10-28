"""Unit tests for the recommender app services and views."""

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from unittest.mock import patch

from .services.spotify_handler import (
    discover_top_tracks_for_genre,
    get_similar_tracks,
    resolve_seed_tracks,
    create_playlist_with_tracks,
)
from .services.user_preferences import (
    describe_pending_options,
    get_default_preferences,
)
from .services.llm_handler import refine_playlist
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
        self.assertEqual(response.url, reverse("dashboard:dashboard"))

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
        mock_similar.return_value = [
            {"id": "3", "name": "Song C", "artists": "Artist C"},
        ]
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
        mock_similar.return_value = [
            {"id": "4", "name": "Similar Song", "artists": "Artist"},
        ]

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
                "similar_tracks_display": ["Similar Song - Similar Artist"],
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
    def test_resolve_seed_tracks_includes_metadata(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.search.return_value = {
            "tracks": {
                "items": [
                    {
                        "id": "1",
                        "name": "Song",
                        "artists": [{"name": "Artist", "id": "artist1"}],
                        "available_markets": ["US"],
                        "album": {"release_date": "2021-01-01"},
                    }
                ]
            }
        }

        results = resolve_seed_tracks(
            [{"title": "Song", "artist": "Artist"}],
            token="token",
            debug_steps=[],
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["artist_ids"], ["artist1"])
        self.assertEqual(results[0]["year"], 2021)

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_discover_top_tracks_for_genre(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.search.side_effect = [
            {"playlists": {"items": [{"id": "playlist1"}]}},
            {
                "tracks": {
                    "items": [
                        {
                            "id": "track1",
                            "name": "Track One",
                            "popularity": 80,
                            "artists": [{"name": "Artist One", "id": "artist1"}],
                            "available_markets": ["US"],
                            "album": {"release_date": "2020-05-01"},
                        }
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
                        "album": {"release_date": "2020-05-01"},
                    }
                }
            ]
        }
        mock_instance.artists.return_value = {
            "artists": [
                {"id": "artist1", "genres": ["pop"]},
            ]
        }

        results = discover_top_tracks_for_genre(
            {"genre": "pop"},
            token="token",
            debug_steps=[],
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["artist_ids"], ["artist1"])

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_get_similar_tracks_scores_candidates(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.search.side_effect = [
            {"playlists": {"items": []}},
            {
                "tracks": {
                    "items": [
                        {
                            "id": "cand1",
                            "name": "Happy Energy",
                            "artists": [{"name": "Artist A", "id": "artistA"}],
                            "popularity": 70,
                            "available_markets": ["US"],
                            "album": {"release_date": "2023-01-01"},
                        },
                        {
                            "id": "cand2",
                            "name": "Mellow Tune",
                            "artists": [{"name": "Artist B", "id": "artistB"}],
                            "popularity": 65,
                            "available_markets": ["US"],
                            "album": {"release_date": "2010-05-05"},
                        },
                    ]
                }
            },
            {
                "tracks": {
                    "items": [
                        {
                            "id": "cand1",
                            "name": "Happy Energy",
                            "artists": [{"name": "Artist A", "id": "artistA"}],
                            "popularity": 70,
                            "available_markets": ["US"],
                            "album": {"release_date": "2023-01-01"},
                        },
                        {
                            "id": "cand2",
                            "name": "Mellow Tune",
                            "artists": [{"name": "Artist B", "id": "artistB"}],
                            "popularity": 65,
                            "available_markets": ["US"],
                            "album": {"release_date": "2010-05-05"},
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

        results = get_similar_tracks(
            ["seed1"],
            {"artistA"},
            2020.0,
            token="token",
            attributes={"energy": "high", "genre": "pop", "mood": "happy"},
            prompt_keywords={"happy", "energy"},
            debug_steps=[],
        )

        self.assertTrue(results)
        self.assertEqual(results[0]["name"], "Happy Energy")
        self.assertEqual(results[0]["artists"], "Artist A")

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_get_similar_tracks_handles_missing_seeds(self, mock_spotify):
        results = get_similar_tracks(
            [],
            set(),
            None,
            token="token",
            attributes={"energy": "medium", "genre": "pop"},
            prompt_keywords=set(),
            debug_steps=[],
        )

        self.assertEqual(results, [])
        mock_spotify.assert_not_called()

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_create_playlist_with_tracks_chunking(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.current_user.return_value = {"id": "user123"}
        mock_instance.user_playlist_create.return_value = {"id": "playlist123"}

        track_ids = [f"track{i}" for i in range(205)]
        result = create_playlist_with_tracks(
            token="token",
            track_ids=track_ids,
            playlist_name="Focus Mix",
            prefix="TEST ",
        )

        self.assertEqual(result["playlist_id"], "playlist123")
        self.assertEqual(result["playlist_name"], "TEST Focus Mix")
        self.assertEqual(mock_instance.playlist_add_items.call_count, 3)
        batched_lengths = [
            len(call.args[1]) for call in mock_instance.playlist_add_items.call_args_list
        ]
        self.assertEqual(batched_lengths, [100, 100, 5])

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_create_playlist_with_tracks_uses_existing_user(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.user_playlist_create.return_value = {"id": "playlist456"}

        result = create_playlist_with_tracks(
            token="token",
            track_ids=["track1"],
            playlist_name="Daily Mix",
            user_id="preset-user",
        )

        mock_instance.current_user.assert_not_called()
        mock_instance.playlist_add_items.assert_called_once()
        self.assertEqual(mock_instance.playlist_add_items.call_args.args[0], "playlist456")
        self.assertEqual(mock_instance.playlist_add_items.call_args.args[1], ["track1"])
        self.assertEqual(result["user_id"], "preset-user")

    def test_create_playlist_with_tracks_requires_tracks(self):
        with self.assertRaises(ValueError):
            create_playlist_with_tracks(token="token", track_ids=[], playlist_name="Empty")


class LLMHandlerTests(TestCase):
    """Unit tests for LLM helper behaviour."""

    @patch("recommender.services.llm_handler.query_ollama", return_value="")
    def test_refine_playlist_empty_response_returns_seeds(self, mock_query):
        seeds = ["Song A - Artist A", "Song B - Artist B"]
        result = refine_playlist(seeds, {"mood": "chill"}, query_fn=mock_query)
        self.assertEqual(result, seeds)
        mock_query.assert_called_once()

    @patch(
        "recommender.services.llm_handler.query_ollama",
        return_value="New Track - Artist\nSong A - Artist A\n",
    )
    def test_refine_playlist_appends_unique_suggestions(self, mock_query):
        seeds = ["Song A - Artist A"]
        result = refine_playlist(seeds, {"mood": "focus"}, query_fn=mock_query)
        self.assertEqual(result, ["Song A - Artist A", "New Track - Artist"])
        mock_query.assert_called_once()


class SavePlaylistViewTests(TestCase):
    """Tests for saving playlists to Spotify."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("recommender:save_playlist")
        self.cache_key = "save-cache-key"
        cache.clear()
        cache.set(
            self.cache_key,
            {
                "playlist": ["Song A - Artist A"],
                "track_ids": ["track1", "track2"],
                "prompt": "test prompt",
                "debug_steps": [],
                "errors": [],
            },
            timeout=60,
        )
        session = self.client.session
        session["spotify_access_token"] = "token"
        session.save()

    @patch("recommender.views.create_playlist_with_tracks")
    def test_save_playlist_success(self, mock_create_playlist):
        mock_create_playlist.return_value = {
            "playlist_name": "TEST Summer Vibes",
            "user_id": "user123",
        }

        response = self.client.post(
            self.url,
            {
                "cache_key": self.cache_key,
                "playlist_name": "Summer Vibes",
            },
        )

        self.assertEqual(response.status_code, 200)
        mock_create_playlist.assert_called_once()
        messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("saved to Spotify" in message for message in messages))
        session = self.client.session
        self.assertEqual(session.get("spotify_user_id"), "user123")

    @patch("recommender.views.create_playlist_with_tracks")
    def test_save_playlist_missing_name(self, mock_create_playlist):
        response = self.client.post(
            self.url,
            {
                "cache_key": self.cache_key,
                "playlist_name": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        mock_create_playlist.assert_not_called()
        messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertIn("Please provide a playlist name.", messages)


class UserPreferencePlaceholderTests(TestCase):
    """Smoke tests for future user preference helpers."""

    def test_get_default_preferences_within_bounds(self):
        prefs = get_default_preferences()
        self.assertGreaterEqual(prefs.track_count, settings.RECOMMENDER_MIN_PLAYLIST_LENGTH)
        self.assertLessEqual(prefs.track_count, settings.RECOMMENDER_MAX_PLAYLIST_LENGTH)

    def test_describe_pending_options_lists_expected_keys(self):
        description = describe_pending_options()
        keys = {item["key"] for item in description}
        self.assertIn("track_count", keys)
        self.assertIn("enforce_unique_tracks", keys)
        self.assertIn("allow_seed_only_playlists", keys)
