"""Unit tests for the recommender app services and views."""

import json

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from recommender.models import SavedPlaylist
from recommender.services.spotify_handler import (
    discover_top_tracks_for_genre,
    get_similar_tracks,
    resolve_seed_tracks,
    create_playlist_with_tracks,
)
from recommender.services.user_preferences import (
    describe_pending_options,
    get_default_preferences,
)
from recommender.services.llm_handler import (
    extract_playlist_attributes,
    refine_playlist,
    suggest_seed_tracks,
)
from recommender.views import _cache_key


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
        self.assertIn('class="track-name">Song A', page)
        self.assertIn('class="track-name">Song C', page)
        self.assertIn('class="track-artist">Artist A', page)

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
        self.assertIn('class="track-name">Fallback Song', page)
        self.assertIn('class="track-name">Similar Song', page)

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
        page = response.content.decode()
        self.assertIn('class="track-name">Cached Song', page)
        self.assertIn('class="track-artist">Artist', page)
        mock_extract.assert_not_called()
        mock_suggest.assert_not_called()
        mock_resolve.assert_not_called()
        mock_similar.assert_not_called()
        mock_discover.assert_not_called()

    @override_settings(RECOMMENDER_DEBUG_VIEW_ENABLED=False)
    def test_debug_panel_hidden_by_default(self):
        session = self.client.session
        session["spotify_access_token"] = "token"
        session["spotify_user_id"] = "debugger"
        session.save()

        cache_key = _cache_key("debugger", "prompt")
        cache.set(
            cache_key,
            {
                "playlist": ["Debug Song - Debug Artist"],
                "track_ids": ["debug-track"],
                "track_details": [
                    {
                        "id": "debug-track",
                        "name": "Debug Song",
                        "artists": "Debug Artist",
                        "album_name": "",
                        "album_image_url": "",
                        "duration_ms": 0,
                    }
                ],
                "debug_steps": ["[0.00s] Step executed."],
            },
            timeout=60,
        )

        response = self.client.post(self.url, {"prompt": "prompt"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Generation Debug Steps", response.content.decode())

    @override_settings(RECOMMENDER_DEBUG_VIEW_ENABLED=True)
    def test_debug_panel_visible_when_flag_enabled(self):
        session = self.client.session
        session["spotify_access_token"] = "token"
        session["spotify_user_id"] = "debugger"
        session.save()

        cache_key = _cache_key("debugger", "prompt")
        cache.set(
            cache_key,
            {
                "playlist": ["Debug Song - Debug Artist"],
                "track_ids": ["debug-track"],
                "track_details": [
                    {
                        "id": "debug-track",
                        "name": "Debug Song",
                        "artists": "Debug Artist",
                        "album_name": "",
                        "album_image_url": "",
                        "duration_ms": 0,
                    }
                ],
                "debug_steps": ["[0.00s] Step executed."],
            },
            timeout=60,
        )

        response = self.client.post(self.url, {"prompt": "prompt"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Generation Debug Steps", response.content.decode())


class RemixPlaylistViewTests(TestCase):
    """Tests for the playlist remix endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("recommender:remix_playlist")
        cache.clear()

    def _seed_cached_playlist(self, cache_key: str, track_count: int = 3):
        tracks = []
        playlist = []
        for index in range(track_count):
            track_id = f"old-{index}"
            track_name = f"Old Track {index + 1}"
            artist_name = f"Old Artist {index + 1}"
            tracks.append(
                {
                    "id": track_id,
                    "name": track_name,
                    "artists": artist_name,
                    "album_name": "",
                    "album_image_url": "",
                    "duration_ms": 180000,
                }
            )
            playlist.append(f"{track_name} - {artist_name}")

        cache.set(
            cache_key,
            {
                "playlist": playlist,
                "track_details": tracks,
                "track_ids": [entry["id"] for entry in tracks],
                "prompt": "lofi coding mix",
                "attributes": {"mood": "chill", "genre": "lo-fi", "energy": "low"},
                "suggested_playlist_name": "Lofi Coding Mix",
            },
            timeout=60,
        )

    @patch("recommender.views.get_similar_tracks")
    @patch("recommender.views.resolve_seed_tracks")
    @patch("recommender.views.suggest_remix_tracks")
    def test_remix_updates_cached_playlist(
        self,
        mock_suggest,
        mock_resolve,
        mock_similar,
    ):
        session = self.client.session
        session["spotify_access_token"] = "token"
        session["spotify_user_id"] = "remix-user"
        session.save()

        cache_key = _cache_key("remix-user", "lofi coding mix")
        self._seed_cached_playlist(cache_key)

        mock_suggest.return_value = [
            {"title": "Remix Track 1", "artist": "Remix Artist 1"},
            {"title": "Remix Track 2", "artist": "Remix Artist 2"},
            {"title": "Remix Track 3", "artist": "Remix Artist 3"},
        ]
        mock_resolve.return_value = [
            {
                "id": f"remix-{index}",
                "name": f"Remix Track {index}",
                "artists": f"Remix Artist {index}",
                "album_name": "",
                "album_image_url": "",
                "duration_ms": 200000,
                "artist_ids": [f"artist-{index}"],
                "year": 2020,
            }
            for index in range(1, 4)
        ]
        mock_similar.return_value = []

        response = self.client.post(self.url, {"cache_key": cache_key})

        self.assertEqual(response.status_code, 200)
        mock_suggest.assert_called_once()
        mock_resolve.assert_called_once()
        mock_similar.assert_not_called()

        cached = cache.get(cache_key)
        self.assertIsInstance(cached, dict)
        self.assertIn("playlist", cached)
        self.assertEqual(len(cached["playlist"]), 3)
        self.assertTrue(all(item.startswith("Remix Track") for item in cached["playlist"]))

        messages_list = list(get_messages(response.wsgi_request))
        self.assertTrue(any("remixed" in str(message).lower() for message in messages_list))

    def test_remix_requires_spotify_auth(self):
        cache_key = _cache_key("remix-user", "lofi coding mix")
        self._seed_cached_playlist(cache_key)

        response = self.client.post(self.url, {"cache_key": cache_key})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("spotify_auth:login"))


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
                        "duration_ms": 210000,
                        "available_markets": ["US"],
                        "album": {
                            "release_date": "2021-01-01",
                            "name": "Album",
                            "images": [{"url": "http://example.com/art.jpg"}],
                        },
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
        self.assertEqual(results[0]["album_name"], "Album")
        self.assertEqual(results[0]["album_image_url"], "http://example.com/art.jpg")
        self.assertEqual(results[0]["duration_ms"], 210000)

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

    @patch(
        "recommender.services.llm_handler.dispatch_llm_query",
        return_value='Here you go!\n```json\n{ "mood": "nostalgic", "genre": "country", "energy_level": "medium" }\n```',
    )
    def test_extract_playlist_attributes_handles_code_fence(self, mock_query):
        attributes = extract_playlist_attributes("country classics", debug_steps=[], provider="openai")
        self.assertEqual(attributes["mood"], "nostalgic")
        self.assertEqual(attributes["genre"], "country")
        self.assertEqual(attributes["energy"], "medium")
        mock_query.assert_called_once()

    @patch(
        "recommender.services.llm_handler.dispatch_llm_query",
        return_value=(
            "Here’s a JSON array of country classics that fit the requested attributes:\n"
            "```json\n"
            '[{"title": "Take Me Home, Country Roads", "artist": "John Denver"},'
            '{"title": "Jolene", "artist": "Dolly Parton"}]\n'
            "```"
        ),
    )
    def test_suggest_seed_tracks_parses_json_code_block(self, mock_query):
        suggestions = suggest_seed_tracks(
            "country classics",
            {"genre": "country", "mood": "nostalgic", "energy": "medium"},
            debug_steps=[],
            provider="openai",
        )
        self.assertEqual(
            suggestions[:2],
            [
                {"title": "Take Me Home, Country Roads", "artist": "John Denver"},
                {"title": "Jolene", "artist": "Dolly Parton"},
            ],
        )
        mock_query.assert_called_once()

    @patch("recommender.services.llm_handler.dispatch_llm_query", return_value="")
    def test_refine_playlist_empty_response_returns_seeds(self, mock_query):
        seeds = ["Song A - Artist A", "Song B - Artist B"]
        result = refine_playlist(seeds, {"mood": "chill"}, query_fn=mock_query)
        self.assertEqual(result, seeds)
        mock_query.assert_called_once()

    @patch(
        "recommender.services.llm_handler.dispatch_llm_query",
        return_value="New Track - Artist\nSong A - Artist A\n",
    )
    def test_refine_playlist_appends_unique_suggestions(self, mock_query):
        seeds = ["Song A - Artist A"]
        result = refine_playlist(seeds, {"mood": "focus"}, query_fn=mock_query)
        self.assertEqual(result, ["Song A - Artist A", "New Track - Artist"])
        mock_query.assert_called_once()

    @patch("recommender.services.llm_handler.dispatch_llm_query", return_value="")
    def test_suggest_seed_tracks_uses_fallback_when_llm_unavailable(self, mock_query):
        suggestions = suggest_seed_tracks(
            "Feel-good pop anthems",
            {"genre": "pop", "mood": "happy", "energy": "high"},
            debug_steps=[],
        )
        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0]["title"], "Blinding Lights")


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
            "playlist_id": "playlist123",
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
        saved = SavedPlaylist.objects.get(playlist_id="playlist123")
        self.assertEqual(saved.creator_user_id, "user123")
        self.assertEqual(saved.like_count, 0)

    @patch("recommender.views.create_playlist_with_tracks")
    def test_save_playlist_preserves_existing_like_count(self, mock_create_playlist):
        SavedPlaylist.objects.create(
            playlist_id="playlist123",
            creator_user_id="initial-user",
            like_count=5,
        )
        mock_create_playlist.return_value = {
            "playlist_id": "playlist123",
            "playlist_name": "TEST Summer Vibes",
            "user_id": "user456",
        }

        response = self.client.post(
            self.url,
            {
                "cache_key": self.cache_key,
                "playlist_name": "Summer Vibes",
            },
        )

        self.assertEqual(response.status_code, 200)
        saved = SavedPlaylist.objects.get(playlist_id="playlist123")
        self.assertEqual(saved.creator_user_id, "user456")
        self.assertEqual(saved.like_count, 5)

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


class PlaylistEditingTests(TestCase):
    """Tests for modifying cached playlists via the editing endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("recommender:update_cached_playlist")
        cache.clear()

    def test_remove_track_updates_cache(self):
        cache_key = "recommender:test"
        cache.set(
            cache_key,
            {
                "track_details": [
                    {
                        "id": "track1",
                        "name": "First",
                        "artists": "Artist",
                        "album_name": "",
                        "album_image_url": "",
                        "duration_ms": 0,
                    },
                    {
                        "id": "track2",
                        "name": "Second",
                        "artists": "Artist",
                        "album_name": "",
                        "album_image_url": "",
                        "duration_ms": 0,
                    },
                ],
                "track_ids": ["track1", "track2"],
                "playlist": ["First - Artist", "Second - Artist"],
            },
            timeout=60,
        )

        response = self.client.post(
            self.url,
            data=json.dumps({"action": "remove", "cache_key": cache_key, "track_id": "track1"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["track_count"], 1)
        self.assertEqual(payload["track_ids"], ["track2"])
        cached = cache.get(cache_key)
        self.assertEqual(len(cached["track_details"]), 1)
        self.assertEqual(cached["track_details"][0]["id"], "track2")

    def test_remove_by_position_when_id_missing(self):
        cache_key = "recommender:position"
        cache.set(
            cache_key,
            {
                "track_details": [
                    {
                        "id": "",
                        "name": "Untitled",
                        "artists": "Unknown",
                        "album_name": "",
                        "album_image_url": "",
                        "duration_ms": 0,
                    }
                ],
                "track_ids": [],
                "playlist": ["Untitled - Unknown"],
            },
            timeout=60,
        )

        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "action": "remove",
                    "cache_key": cache_key,
                    "track_id": "",
                    "position": 0,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["track_count"], 0)
        cached = cache.get(cache_key)
        self.assertEqual(cached["track_details"], [])
        self.assertEqual(cached["playlist"], [])

    def test_update_requires_json_payload(self):
        response = self.client.post(self.url, {"action": "remove"})
        self.assertEqual(response.status_code, 400)
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
