"""Unit tests for the recommender app services and views."""

import json
import subprocess
from types import SimpleNamespace

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from recommender.models import SavedPlaylist
from recommender.services.spotify_handler import (
    _extract_release_year,
    _filter_by_market,
    _filter_non_latin_tracks,
    _filter_tracks_by_artist_genre,
    _genre_variants,
    _normalize_artist_key,
    _normalize_genre,
    _primary_image_url,
    _serialize_track_payload,
    _tracks_to_strings,
    discover_top_tracks_for_genre,
    get_similar_tracks,
    resolve_seed_tracks,
    create_playlist_with_tracks,
    _score_track_basic,
    _is_mostly_latin,
    compute_playlist_statistics,
)
from recommender.services.user_preferences import (
    describe_pending_options,
    get_default_preferences,
)
from recommender.services.llm_handler import (
    _json_candidates,
    _parse_json_response,
    _resolve_provider,
    dispatch_llm_query,
    extract_playlist_attributes,
    query_ollama,
    query_openai,
    refine_playlist,
    suggest_remix_tracks,
    suggest_seed_tracks,
)
from recommender.views import _cache_key, _build_context_from_payload, _make_logger


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
    @patch("recommender.views.compute_playlist_statistics")
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
        mock_stats,
        mock_extract,
    ):
        session = self.client.session
        session["spotify_access_token"] = "token"
        session["spotify_user_id"] = "user123"
        session.save()

        mock_extract.return_value = {"mood": "upbeat", "genre": "pop", "energy": "high"}
        mock_stats.return_value = {
            "total_tracks": 3,
            "total_duration": "00:09:15",
            "avg_popularity": 64.5,
            "novelty": 72.0,
            "genre_distribution": {"synth-pop": 40.0, "alt-pop": 25.0, "indie": 20.0},
            "genre_top": [
                {"genre": "synth-pop", "percentage": 40.0},
                {"genre": "alt-pop", "percentage": 25.0},
                {"genre": "indie", "percentage": 20.0},
            ],
            "genre_remaining": [
                {"genre": "dream-pop", "percentage": 15.0},
            ],
            "novelty_reference_ids": [],
            "source_mix": [
                {"key": "llm_seed", "label": "LLM Seeds", "count": 5, "percentage": 50.0},
                {"key": "similarity", "label": "Similarity Engine", "count": 3, "percentage": 30.0},
            ],
            "source_total": 10,
            "top_popular_tracks": [
                {"id": "1", "name": "Song A", "artists": "Artist A", "popularity": 80, "album_image_url": ""},
                {"id": "2", "name": "Song B", "artists": "Artist B", "popularity": 50, "album_image_url": ""},
            ],
            "least_popular_tracks": [
                {"id": "4", "name": "Song D", "artists": "Artist D", "popularity": 20, "album_image_url": ""},
            ],
        }
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
        mock_stats.assert_called_once()
        page = response.content.decode()
        self.assertIn('class="track-name">Song A', page)
        self.assertIn('class="track-name">Song C', page)
        self.assertIn('class="track-artist">Artist A', page)
        self.assertIn('id="playlist-stats-data"', page)
        self.assertIn('data-chart="genre"', page)
        self.assertIn('Freshness Gauge', page)
        self.assertIn('recommender_stats.js', page)
        self.assertIn('Popularity Highlights', page)
        self.assertIn('Most Popular', page)
        self.assertIn('Least Popular', page)
        self.assertIn('Show All Genres', page)
        self.assertIn('Source Blend', page)

    @patch("recommender.views.extract_playlist_attributes")
    @patch("recommender.views.compute_playlist_statistics")
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
        mock_stats,
        mock_extract,
    ):
        session = self.client.session
        session["spotify_access_token"] = "token"
        session.save()

        mock_extract.return_value = {"mood": "calm", "genre": "ambient", "energy": "low"}
        mock_stats.return_value = {
            "total_tracks": 2,
            "total_duration": "00:08:00",
            "avg_popularity": 55.0,
            "novelty": 80.0,
            "genre_distribution": {"ambient": 50.0, "chill": 30.0, "downtempo": 20.0},
            "genre_top": [
                {"genre": "ambient", "percentage": 50.0},
                {"genre": "chill", "percentage": 30.0},
                {"genre": "downtempo", "percentage": 20.0},
            ],
            "genre_remaining": [
                {"genre": "lofi", "percentage": 10.0},
            ],
            "novelty_reference_ids": [],
            "source_mix": [
                {"key": "genre_discovery", "label": "Spotify Discovery", "count": 3, "percentage": 60.0},
                {"key": "similarity", "label": "Similarity Engine", "count": 2, "percentage": 40.0},
            ],
            "source_total": 5,
            "top_popular_tracks": [
                {"id": "3", "name": "Fallback Song", "artists": "Fallback Artist", "popularity": 65, "album_image_url": ""},
            ],
            "least_popular_tracks": [
                {"id": "4", "name": "Similar Song", "artists": "Artist", "popularity": 45, "album_image_url": ""},
            ],
        }
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
        mock_stats.assert_called_once()
        page = response.content.decode()
        self.assertIn('class="track-name">Fallback Song', page)
        self.assertIn('class="track-name">Similar Song', page)
        self.assertIn('Popularity Highlights', page)
        self.assertIn('Most Popular', page)
        self.assertIn('Least Popular', page)
        self.assertIn('Show All Genres', page)

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
        self.assertNotIn("Debug Inspector", response.content.decode())

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
        self.assertIn("Debug Inspector", response.content.decode())


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

    @patch("recommender.views.compute_playlist_statistics")
    @patch("recommender.views.get_similar_tracks")
    @patch("recommender.views.resolve_seed_tracks")
    @patch("recommender.views.suggest_remix_tracks")
    def test_remix_updates_cached_playlist(
        self,
        mock_suggest,
        mock_resolve,
        mock_similar,
        mock_stats,
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
        mock_stats.return_value = {
            "total_tracks": 3,
            "total_duration": "00:10:00",
            "avg_popularity": 62.0,
            "novelty": 68.0,
            "genre_distribution": {"lo-fi": 40.0, "jazz": 30.0, "ambient": 20.0},
            "genre_top": [
                {"genre": "lo-fi", "percentage": 40.0},
                {"genre": "jazz", "percentage": 30.0},
                {"genre": "ambient", "percentage": 20.0},
            ],
            "genre_remaining": [
                {"genre": "chillhop", "percentage": 10.0},
            ],
            "novelty_reference_ids": [],
            "source_mix": [
                {"key": "remix_seed", "label": "Remix Seeds", "count": 5, "percentage": 50.0},
                {"key": "similarity", "label": "Similarity Engine", "count": 3, "percentage": 30.0},
            ],
            "source_total": 10,
            "top_popular_tracks": [
                {"id": "remix-1", "name": "Remix Track 1", "artists": "Remix Artist 1", "popularity": 70, "album_image_url": ""},
                {"id": "remix-2", "name": "Remix Track 2", "artists": "Remix Artist 2", "popularity": 60, "album_image_url": ""},
                {"id": "remix-3", "name": "Remix Track 3", "artists": "Remix Artist 3", "popularity": 55, "album_image_url": ""},
            ],
            "least_popular_tracks": [
                {"id": "old-1", "name": "Old Track 1", "artists": "Old Artist 1", "popularity": 40, "album_image_url": ""},
            ],
        }

        response = self.client.post(self.url, {"cache_key": cache_key})

        self.assertEqual(response.status_code, 200)
        mock_suggest.assert_called_once()
        mock_resolve.assert_called_once()
        mock_similar.assert_not_called()
        mock_stats.assert_called_once()

        cached = cache.get(cache_key)
        self.assertIsInstance(cached, dict)
        self.assertIn("playlist", cached)
        self.assertEqual(len(cached["playlist"]), 3)
        self.assertTrue(all(item.startswith("Remix Track") for item in cached["playlist"]))

        messages_list = list(get_messages(response.wsgi_request))
        self.assertTrue(any("remixed" in str(message).lower() for message in messages_list))
        content = response.content.decode()
        self.assertIn('Popularity Highlights', content)
        self.assertIn('Most Popular', content)
        self.assertIn('Least Popular', content)
        self.assertIn('Show All Genres', content)
        self.assertIn('Source Blend', content)

    def test_remix_requires_spotify_auth(self):
        cache_key = _cache_key("remix-user", "lofi coding mix")
        self._seed_cached_playlist(cache_key)

        response = self.client.post(self.url, {"cache_key": cache_key})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("spotify_auth:login"))


class SpotifyHandlerTests(TestCase):
    """Unit tests for Spotify service helpers."""

    def test_compute_playlist_statistics_empty_playlist(self):
        stats = compute_playlist_statistics("token", [])

        self.assertEqual(stats["total_tracks"], 0)
        self.assertEqual(stats["total_duration"], "00:00:00")
        self.assertIsNone(stats["avg_popularity"])
        self.assertEqual(stats["novelty"], 100.0)
        self.assertEqual(stats["genre_distribution"], {})
        self.assertEqual(stats["genre_top"], [])
        self.assertEqual(stats["genre_remaining"], [])
        self.assertEqual(stats["novelty_reference_ids"], [])
        self.assertEqual(stats["source_mix"], [])
        self.assertEqual(stats["source_total"], 0)
        self.assertEqual(stats["top_popular_tracks"], [])
        self.assertEqual(stats["least_popular_tracks"], [])

    def test_compute_playlist_statistics_with_cached_overlap(self):
        tracks = [
            {
                "id": "track-1",
                "name": "First",
                "artists": "Artist One",
                "duration_ms": 60000,
                "popularity": 50,
                "artist_ids": ["artist-1"],
            },
            {
                "id": "track-2",
                "name": "Second",
                "artists": "Artist Two",
                "duration_ms": 120000,
                "popularity": 70,
                "artist_ids": ["artist-2"],
            },
        ]
        profile_cache = {
            "tracks": {"track-1": {"id": "track-1"}},
            "top_track_ids": ["track-3"],
        }

        stats = compute_playlist_statistics(
            "",
            tracks,
            profile_cache=profile_cache,
            cached_track_ids=["track-2"],
        )

        self.assertEqual(stats["total_tracks"], 2)
        self.assertEqual(stats["total_duration"], "00:03:00")
        self.assertEqual(stats["avg_popularity"], 60.0)
        self.assertEqual(stats["novelty"], 0.0)
        self.assertEqual(stats["genre_top"], [])
        self.assertEqual(stats["genre_remaining"], [])
        self.assertIn("track-1", stats["novelty_reference_ids"])
        self.assertIn("track-2", stats["novelty_reference_ids"])
        self.assertIn("track-3", stats["novelty_reference_ids"])
        self.assertEqual(len(stats["top_popular_tracks"]), 2)
        self.assertEqual(stats["top_popular_tracks"][0]["id"], "track-2")
        self.assertEqual(stats["least_popular_tracks"][0]["id"], "track-1")
        self.assertEqual(stats["source_total"], 2)
        self.assertEqual(len(stats["source_mix"]), 1)
        self.assertEqual(stats["source_mix"][0]["key"], "playlist")

    @patch("recommender.services.spotify_handler.spotipy.Spotify")
    def test_compute_playlist_statistics_populates_genre_distribution(self, mock_spotify):
        mock_instance = mock_spotify.return_value
        mock_instance.artists.return_value = {
            "artists": [
                {"id": "artist-1", "genres": ["Synth Pop", "Pop"]},
                {"id": "artist-2", "genres": ["Indie Rock"]},
            ]
        }

        tracks = [
            {
                "id": "track-1",
                "name": "First",
                "artists": "Artist One",
                "duration_ms": 90000,
                "popularity": 80,
                "artist_ids": ["artist-1"],
            },
            {
                "id": "track-2",
                "name": "Second",
                "artists": "Artist Two",
                "duration_ms": 90000,
                "popularity": 70,
                "artist_ids": ["artist-2"],
            },
        ]

        stats = compute_playlist_statistics(
            "token",
            tracks,
        )

        self.assertEqual(stats["total_tracks"], 2)
        self.assertAlmostEqual(stats["avg_popularity"], 75.0)
        self.assertAlmostEqual(stats["novelty"], 100.0)
        self.assertEqual(len(stats["genre_top"]), 3)
        self.assertTrue(any(item["genre"] == "indie-rock" for item in stats["genre_top"]))
        self.assertTrue(any(item["genre"] == "pop" for item in stats["genre_top"]))
        self.assertTrue(any(item["genre"] == "synth-pop" for item in stats["genre_top"]))
        self.assertEqual(stats["genre_remaining"], [])
        mock_instance.artists.assert_called_once()
        self.assertEqual(len(stats["top_popular_tracks"]), 2)
        self.assertEqual(stats["top_popular_tracks"][0]["id"], "track-1")
        self.assertEqual(stats["least_popular_tracks"][0]["id"], "track-2")
        self.assertEqual(stats["source_total"], 2)
        self.assertEqual(len(stats["source_mix"]), 1)

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


class SpotifyUtilityFunctionTests(TestCase):
    """Coverage for low-level Spotify helper functions."""

    def test_normalize_helpers_handle_unicode(self):
        self.assertEqual(_normalize_genre("Lo-Fi ✨ Beats"), "lo-fi--beats")
        self.assertEqual(_normalize_artist_key("HΔppen!ng Artist"), "hppenngartist")

    def test_genre_variants_expands_aliases(self):
        variants = _genre_variants("r-b")
        self.assertIn("r&b", variants)
        self.assertIn("rb", variants)

    def test_tracks_to_strings_and_market_filter(self):
        tracks = [
            {"name": "Song One", "artists": [{"name": "Artist A"}], "available_markets": ["US", "GB"]},
            {"name": "Song Two", "artists": [{"name": "Artist B"}], "available_markets": []},
            {"name": "Song Three", "artists": [{"name": "Artist C"}], "available_markets": ["CA"]},
        ]
        filtered = _filter_by_market(tracks, "US")
        self.assertEqual(len(filtered), 2)
        strings = _tracks_to_strings(filtered)
        self.assertEqual(strings, ["Song One - Artist A", "Song Two - Artist B"])

    def test_non_latin_detection_filters_tracks(self):
        self.assertTrue(_is_mostly_latin("Café Society"))
        self.assertFalse(_is_mostly_latin("東京の夜"))
        tracks = [
            {"name": "Café Society"},
            {"name": "東京の夜"},
        ]
        kept = _filter_non_latin_tracks(tracks)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["name"], "Café Society")

    def test_filter_tracks_by_artist_genre_matches_aliases(self):
        class DummySpotify:
            def artists(self, ids):
                return {
                    "artists": [
                        {"id": "artist1", "genres": ["Synth Pop", "Alt Pop"]},
                        {"id": "artist2", "genres": ["Classical"]},
                    ]
                }

        tracks = [
            {"id": "track1", "artists": [{"id": "artist1"}], "popularity": 65},
            {"id": "track2", "artists": [{"id": "artist2"}], "popularity": 70},
        ]
        filtered = _filter_tracks_by_artist_genre(DummySpotify(), tracks, "synth-pop", popularity_threshold=60)
        self.assertEqual([track["id"] for track in filtered], ["track1"])

    def test_release_year_and_primary_image_extraction(self):
        track = {"album": {"release_date": "2019-10-31"}}
        self.assertEqual(_extract_release_year(track), 2019)
        self.assertIsNone(_extract_release_year({"album": {"release_date": "unknown"}}))
        images = [{"url": ""}, {"url": "http://example.com/img.jpg"}]
        self.assertEqual(_primary_image_url(images), "http://example.com/img.jpg")
        self.assertEqual(_primary_image_url([]), "")

    def test_serialize_track_payload_compiles_metadata(self):
        track = {
            "id": "track-123",
            "name": "Moments",
            "artists": [{"name": "Artist A", "id": "artistA"}, {"name": "Artist B", "id": "artistB"}],
            "album": {
                "name": "Moments LP",
                "images": [{"url": "http://example.com/img.jpg"}],
                "release_date": "2020-05-01",
            },
            "duration_ms": 210000,
            "popularity": 70,
        }
        payload = _serialize_track_payload(track)
        self.assertEqual(payload["artists"], "Artist A, Artist B")
        self.assertEqual(payload["album_name"], "Moments LP")
        self.assertEqual(payload["album_image_url"], "http://example.com/img.jpg")
        self.assertEqual(payload["year"], 2020)
        self.assertEqual(payload["duration_ms"], 210000)
        self.assertEqual(payload["artist_ids"], ["artistA", "artistB"])


class SpotifyScoringUnitTests(TestCase):
    """Unit coverage for local similarity scoring helpers."""

    def test_score_track_basic_includes_multiple_bonuses(self):
        track = {
            "id": "track-1",
            "name": "Happy Energy",
            "popularity": 80,
            "artists": [{"id": "artistA", "name": "Artist A"}],
            "album": {"release_date": "2022-05-01"},
        }
        profile_cache = {
            "tracks": {"track-1": {"name": "Happy Energy"}},
            "genre_buckets": {"pop": {"track_ids": ["track-1"]}},
            "artist_counts": {"artistA": 0},
        }

        score, breakdown = _score_track_basic(
            track,
            {"artistA"},
            2020.0,
            "high",
            {"happy", "energy"},
            profile_cache=profile_cache,
            focus_artist_ids={"artistA"},
            target_genre="pop",
        )

        self.assertGreater(score, 0.45)
        self.assertGreater(breakdown["seed_overlap"], 0)
        self.assertGreater(breakdown["focus_artist"], 0)
        self.assertGreater(breakdown["keyword_match"], 0)
        self.assertGreater(breakdown["year_alignment"], 0)
        self.assertGreater(breakdown["energy_bias"], 0)
        self.assertGreater(breakdown["cache_track_hit"], 0)
        self.assertGreater(breakdown["cache_genre_alignment"], 0)
        self.assertGreater(breakdown["novelty"], 0)

    def test_score_track_basic_handles_sparse_metadata(self):
        track = {
            "id": "track-2",
            "name": "Unknown Track",
            "popularity": 5,
            "artists": [{"id": "artistB", "name": "Artist B"}],
            "album": {"release_date": "1980"},
        }
        profile_cache = {"artist_counts": {"artistB": 10}}

        score, breakdown = _score_track_basic(
            track,
            set(),
            None,
            None,
            set(),
            profile_cache=profile_cache,
            focus_artist_ids=set(),
            target_genre=None,
        )

        self.assertGreaterEqual(score, 0.0)
        self.assertEqual(breakdown["year_alignment"], 0.0)
        self.assertEqual(breakdown["energy_bias"], 0.0)
        self.assertLessEqual(breakdown["novelty"], 0.0)


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

    def test_json_candidates_extracts_code_fences(self):
        raw = "Intro\n```json\n{\"title\": \"Song\"}\n```\nTrailing text"
        candidates = _json_candidates(raw)
        self.assertIn('{"title": "Song"}', candidates)
        self.assertIn("Intro", candidates[-1])

    def test_parse_json_response_handles_embedded_objects(self):
        payload = "Some info {\"tracks\": [1, 2]} extra text"
        parsed = _parse_json_response(payload)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed["tracks"], [1, 2])

    @override_settings(RECOMMENDER_LLM_DEFAULT_PROVIDER="ollama")
    def test_resolve_provider_defaults_for_invalid_option(self):
        self.assertEqual(_resolve_provider("anthropic"), "ollama")
        with override_settings(RECOMMENDER_LLM_DEFAULT_PROVIDER="invalid"):
            self.assertEqual(_resolve_provider(None), "openai")

    @patch("recommender.services.llm_handler.query_openai", return_value="openai-response")
    @patch("recommender.services.llm_handler.query_ollama", return_value="ollama-response")
    def test_dispatch_llm_query_routes_to_ollama(self, mock_ollama, mock_openai):
        result = dispatch_llm_query("prompt", provider="ollama", model="tiny", timeout=15)
        self.assertEqual(result, "ollama-response")
        mock_ollama.assert_called_once()
        mock_openai.assert_not_called()

    @override_settings(DEBUG=False, RECOMMENDER_OLLAMA_TIMEOUT_SECONDS=45)
    @patch("recommender.services.llm_handler.subprocess.run")
    def test_query_ollama_returns_trimmed_output(self, mock_run):
        mock_run.return_value = SimpleNamespace(stdout=" result \n", stderr="", returncode=0)
        output = query_ollama("hi there", model="llama3")
        self.assertEqual(output, "result")
        mock_run.assert_called_once()

    @override_settings(DEBUG=False)
    @patch(
        "recommender.services.llm_handler.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ollama", timeout=60),
    )
    def test_query_ollama_handles_timeout(self, mock_run):
        self.assertEqual(query_ollama("slow prompt"), "")
        mock_run.assert_called_once()

    @patch("recommender.services.llm_handler._get_openai_client")
    def test_query_openai_returns_output_text(self, mock_get_client):
        class DummyResponses:
            def create(self, **kwargs):
                return SimpleNamespace(output_text="  final answer  ")

        mock_get_client.return_value = SimpleNamespace(responses=DummyResponses())
        output = query_openai("prompt", model="gpt-4", temperature=0.2, max_output_tokens=123)
        self.assertEqual(output, "final answer")
        mock_get_client.assert_called_once()

    @patch("recommender.services.llm_handler._get_openai_client")
    def test_query_openai_collects_segmented_output(self, mock_get_client):
        class DummyContent:
            def __init__(self, value):
                self.text = SimpleNamespace(value=value)

        class DummyResponse:
            output_text = ""
            output = [SimpleNamespace(content=[DummyContent("Line1"), DummyContent("Line2")])]

        class DummyResponses:
            def create(self, **kwargs):
                return DummyResponse()

        mock_get_client.return_value = SimpleNamespace(responses=DummyResponses())
        output = query_openai("prompt")
        self.assertEqual(output, "Line1Line2")

    @patch("recommender.services.llm_handler.query_openai", return_value="openai-response")
    def test_dispatch_llm_query_defaults_to_openai(self, mock_openai):
        result = dispatch_llm_query("prompt", provider="unknown", temperature=0.5, max_output_tokens=256)
        self.assertEqual(result, "openai-response")
        mock_openai.assert_called_once()

    @patch(
        "recommender.services.llm_handler.dispatch_llm_query",
        return_value=json.dumps(
            [
                {"title": "Fresh Track", "artist": "Artist X"},
                "Existing Song - Artist Y",
            ]
        ),
    )
    def test_suggest_remix_tracks_parses_mixed_responses(self, mock_dispatch):
        existing = ["Existing Song - Artist Y", "Another Song - Artist Z"]
        suggestions = suggest_remix_tracks(
            existing,
            {"genre": "pop"},
            prompt="refresh",
            target_count=3,
            debug_steps=[],
        )
        self.assertEqual(len(suggestions), 3)
        self.assertEqual(suggestions[0]["title"], "Fresh Track")
        self.assertEqual(suggestions[1]["title"], "Existing Song")
        mock_dispatch.assert_called_once()

    @patch("recommender.services.llm_handler.dispatch_llm_query", return_value="")
    def test_suggest_remix_tracks_falls_back_to_existing(self, mock_dispatch):
        existing = ["Song One - Artist A", "Song Two - Artist B"]
        suggestions = suggest_remix_tracks(
            existing,
            {"genre": "pop"},
            prompt="refresh",
            target_count=2,
        )
        self.assertEqual([item["title"] for item in suggestions], ["Song One", "Song Two"])
        mock_dispatch.assert_called_once()


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


class ViewHelperTests(TestCase):
    """Focused tests for view helper utilities."""

    def test_make_logger_tracks_errors_and_debug_steps(self):
        debug_steps = []
        errors = []
        log = _make_logger(debug_steps, errors)

        log("Seed pipeline started.")
        log("Error: missing artist metadata.")

        self.assertEqual(len(errors), 1)
        self.assertIn("missing artist metadata", errors[0].lower())
        self.assertEqual(len(debug_steps), 2)
        self.assertTrue(debug_steps[0].endswith("Seed pipeline started."))
        self.assertTrue(debug_steps[1].endswith("Error: missing artist metadata."))

    @override_settings(RECOMMENDER_DEBUG_VIEW_ENABLED=False, RECOMMENDER_LLM_DEFAULT_PROVIDER="openai")
    def test_build_context_from_payload_converts_legacy_fields(self):
        payload = {
            "playlist": ["Track 1 - Artist 1", "Track 2 - Artist 2"],
            "track_ids": ["track-1", "track-2"],
            "track_details": "legacy",
            "seed_track_details": [
                {"id": "seed-1", "name": "Seed 1", "artists": "Seed Artist"},
                "invalid-seed",
            ],
            "similar_tracks_debug": [
                {"id": "sim-1", "name": "Sim 1", "artists": "Sim Artist"},
                "invalid-similar",
            ],
            "seed_track_display": ["Seed 1 - Seed Artist"],
            "similar_tracks_display": ["Sim 1 - Sim Artist"],
            "preference_descriptions": {"track_count": "Keep between 10 and 30."},
            "user_preferences": {"track_count": 15},
            "prompt": "test prompt",
            "cache_key": "cache-key",
            "attributes": {"mood": "happy"},
            "debug_steps": ["[0.00s] Legacy entry."],
            "profile_snapshot": "not-a-dict",
            "llm_provider": "Ollama",
        }

        context = _build_context_from_payload(payload)

        self.assertEqual(len(context["playlist_tracks"]), 2)
        self.assertEqual(context["playlist_tracks"][0]["id"], "track-1")
        self.assertEqual(context["playlist_tracks"][0]["name"], "Track 1")
        self.assertEqual(context["playlist_tracks"][0]["artists"], "Artist 1")
        self.assertEqual(len(context["seed_track_details"]), 1)
        self.assertEqual(context["seed_track_details"][0]["id"], "seed-1")
        self.assertIn("seed_source", context["seed_track_details"][0])
        self.assertIn("source", context["seed_track_details"][0])
        self.assertEqual(
            context["seed_track_details"][0]["seed_source"],
            context["seed_track_details"][0]["source"],
        )
        self.assertEqual(len(context["similar_track_details"]), 1)
        self.assertEqual(context["similar_track_details"][0]["id"], "sim-1")
        self.assertEqual(context["preference_descriptions"], [
            {
                "key": "track_count",
                "label": "Track Count",
                "description": "Keep between 10 and 30.",
            }
        ])
        self.assertEqual(context["seed_tracks"], ["Seed 1 - Seed Artist"])
        self.assertEqual(context["similar_tracks"], ["Sim 1 - Sim Artist"])
        self.assertEqual(context["llm_provider"], "Ollama")
        self.assertEqual(context["llm_provider_default"], "openai")
        self.assertEqual(context["debug_steps"], [])
        self.assertIsNone(context["profile_snapshot"])

    @override_settings(RECOMMENDER_DEBUG_VIEW_ENABLED=True, RECOMMENDER_LLM_DEFAULT_PROVIDER="ollama")
    def test_build_context_from_payload_preserves_debug_when_enabled(self):
        payload = {
            "playlist": ["Existing Track - Artist"],
            "track_details": [
                {
                    "id": "existing",
                    "name": "Existing Track",
                    "artists": "Artist",
                    "album_name": "Album",
                    "album_image_url": "http://example.com/img.jpg",
                    "duration_ms": 180000,
                }
            ],
            "seed_track_details": [{"id": "seed", "name": "Seed", "artists": "Seed Artist"}],
            "similar_tracks_debug": [{"id": "similar", "name": "Similar", "artists": "Similar Artist"}],
            "debug_steps": ["[0.01s] Already logged."],
            "attributes": {"genre": "pop"},
            "user_preferences": {},
            "preference_descriptions": [
                {"key": "track_count", "label": "Track Count", "description": "Number of tracks."}
            ],
        }

        context = _build_context_from_payload(payload)

        self.assertEqual(context["debug_steps"], ["[0.01s] Already logged."])
        self.assertEqual(context["playlist_tracks"][0]["album_name"], "Album")
        self.assertEqual(context["llm_provider"], "ollama")
        self.assertEqual(context["llm_provider_default"], "ollama")
        self.assertEqual(context["seed_track_details"][0]["name"], "Seed")
        self.assertEqual(context["similar_track_details"][0]["name"], "Similar")
