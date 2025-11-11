"""Unit tests for the recommender app services and views."""

import json
from types import SimpleNamespace

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from recommender.models import PlaylistGenerationStat, SavedPlaylist
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
from recommender.services.stats_service import (
    get_genre_breakdown,
    summarize_generation_stats,
)
from recommender.services.user_preferences import (
    describe_pending_options,
    get_default_preferences,
)
from recommender.services.llm_handler import (
    _json_candidates,
    _parse_json_response,
    dispatch_llm_query,
    extract_playlist_attributes,
    query_openai,
    refine_playlist,
    suggest_remix_tracks,
    suggest_seed_tracks,
)
from recommender.views import _cache_key, _build_context_from_payload, _make_logger


def _payload_with_owner(session, cache_key, payload, owner_user_id=None):
    """Return a cache payload annotated with ownership metadata."""
    if not session.session_key:
        session.save()
    enriched = dict(payload)
    enriched.setdefault("cache_key", cache_key)
    enriched["owner_user_id"] = owner_user_id or session.get("spotify_user_id", "anonymous")
    enriched["owner_session_key"] = session.session_key
    return enriched


class ModelTests(TestCase):
    """Tests for recommender models."""

    def test_saved_playlist_str(self):
        """Test __str__ method of SavedPlaylist"""
        playlist = SavedPlaylist.objects.create(
            playlist_id="spotify123",
            like_count=10,
            creator_user_id="user456",
            creator_display_name="Test User"
        )
        expected = "spotify123 (user456)"
        self.assertEqual(str(playlist), expected)

    def test_playlist_generation_stat_str(self):
        """Test __str__ method of PlaylistGenerationStat"""
        import datetime
        stat = PlaylistGenerationStat.objects.create(
            user_identifier="testuser",
            prompt="test prompt",
            track_count=20,
            total_duration_ms=1000000
        )
        # Should include user_identifier and created_at timestamp
        str_repr = str(stat)
        self.assertIn("testuser", str_repr)
        # Check format includes date/time
        self.assertRegex(str_repr, r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}')


class StatsServiceTests(TestCase):
    """Verify aggregation helpers for generation history."""

    def setUp(self):
        PlaylistGenerationStat.objects.all().delete()

    def test_summary_empty_identifier(self):
        summary = summarize_generation_stats(None)
        self.assertEqual(summary["total_playlists"], 0)
        self.assertEqual(summary["total_tracks"], 0)
        self.assertEqual(summary["top_genre"], "")
        self.assertEqual(summary["total_tokens"], 0)

    def test_summary_empty_string_identifier(self):
        """Test summarize_generation_stats with empty string returns empty summary"""
        summary = summarize_generation_stats("")
        self.assertEqual(summary["total_playlists"], 0)
        self.assertEqual(summary["total_tracks"], 0)

    def test_summary_with_records(self):
        PlaylistGenerationStat.objects.create(
            user_identifier="user123",
            prompt="alt pop vibes",
            track_count=20,
            total_duration_ms=3_600_000,
            top_genre="Alt Pop",
            avg_novelty=78.5,
            stats={"genre_top": [{"genre": "Alt Pop", "percentage": 40}]},
            total_tokens=1200,
        )
        PlaylistGenerationStat.objects.create(
            user_identifier="user123",
            prompt="chill study",
            track_count=25,
            total_duration_ms=4_200_000,
            top_genre="Chill",
            avg_novelty=81.0,
            stats={"genre_top": [{"genre": "Chill", "percentage": 50}]},
            total_tokens=800,
        )

        summary = summarize_generation_stats("user123")
        self.assertEqual(summary["total_playlists"], 2)
        self.assertEqual(summary["total_tracks"], 45)
        self.assertAlmostEqual(summary["total_hours"], 2.17, places=2)
        self.assertEqual(summary["top_genre"], "Alt Pop")
        self.assertEqual(summary["avg_novelty"], 79.8)
        self.assertEqual(summary["total_tokens"], 2000)

    def test_genre_breakdown(self):
        PlaylistGenerationStat.objects.create(
            user_identifier="user456",
            prompt="road trip",
            track_count=10,
            total_duration_ms=2_000_000,
            top_genre="Indie",
            stats={
                "genre_top": [
                    {"genre": "Indie", "percentage": 60},
                    {"genre": "Rock", "percentage": 30},
                ]
            },
        )
        PlaylistGenerationStat.objects.create(
            user_identifier="user456",
            prompt="focus",
            track_count=12,
            total_duration_ms=2_500_000,
            top_genre="Lo-Fi",
            stats={
                "genre_top": [
                    {"genre": "Lo-Fi", "percentage": 70},
                    {"genre": "Indie", "percentage": 20},
                ]
            },
        )

        breakdown = get_genre_breakdown("user456")
        self.assertTrue(any(entry["genre"] == "Indie" for entry in breakdown))
        self.assertTrue(any(entry["genre"] == "Lo-Fi" for entry in breakdown))

    def test_genre_breakdown_with_empty_identifier(self):
        """Test get_genre_breakdown returns empty list for None identifier"""
        breakdown = get_genre_breakdown(None)
        self.assertEqual(breakdown, [])

    def test_genre_breakdown_with_empty_string_identifier(self):
        """Test get_genre_breakdown returns empty list for empty string"""
        breakdown = get_genre_breakdown("")
        self.assertEqual(breakdown, [])

    def test_genre_breakdown_with_top_genre_not_in_genre_top(self):
        """Test get_genre_breakdown includes top_genre even if not in stats"""
        PlaylistGenerationStat.objects.create(
            user_identifier="user789",
            prompt="jazz classics",
            track_count=15,
            total_duration_ms=3_000_000,
            top_genre="Jazz",  # This genre is not in stats['genre_top']
            stats={
                "genre_top": [
                    {"genre": "Blues", "percentage": 40},
                ]
            },
        )

        breakdown = get_genre_breakdown("user789")
        # Jazz should be included even though it's only in top_genre
        self.assertTrue(any(entry["genre"] == "Jazz" for entry in breakdown))

    def test_genre_breakdown_with_invalid_genre_entries(self):
        """Test _normalize_genre_entries handles invalid data gracefully"""
        from recommender.services.stats_service import _normalize_genre_entries

        # Test with non-dict entries
        result = _normalize_genre_entries([
            {"genre": "Valid", "percentage": 50},
            "not a dict",  # Should be skipped
            None,  # Should be skipped
            {"genre": "", "percentage": 30},  # Empty genre should be skipped
            {"percentage": 20},  # Missing genre should be skipped
        ])

        # Only the first valid entry should remain
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["genre"], "Valid")
        self.assertEqual(result[0]["weight"], 50)

    def test_genre_breakdown_with_non_dict_stats(self):
        """Test get_genre_breakdown handles non-dict stats gracefully"""
        PlaylistGenerationStat.objects.create(
            user_identifier="user_nondict",
            prompt="test",
            track_count=10,
            total_duration_ms=1_000_000,
            top_genre="Pop",
            stats="not a dict",  # Invalid stats format
        )

        breakdown = get_genre_breakdown("user_nondict")
        # Should still include top_genre
        self.assertTrue(any(entry["genre"] == "Pop" for entry in breakdown))


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
            _payload_with_owner(
                session,
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
            ),
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
            _payload_with_owner(
                session,
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
            ),
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
            _payload_with_owner(
                session,
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
            ),
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
        session = self.client.session
        if not session.session_key:
            session.save()
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
            _payload_with_owner(
                session,
                cache_key,
                {
                    "playlist": playlist,
                    "track_details": tracks,
                    "track_ids": [entry["id"] for entry in tracks],
                    "prompt": "lofi coding mix",
                    "attributes": {"mood": "chill", "genre": "lo-fi", "energy": "low"},
                    "suggested_playlist_name": "Lofi Coding Mix",
                },
            ),
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
        mock_instance.current_user.return_value = {"id": "user123", "display_name": "User Name"}
        mock_instance.user_playlist_create.return_value = {"id": "playlist123", "display_name": "User Name"}

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

        mock_instance.current_user.assert_called()
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

    @patch("recommender.services.llm_handler.query_openai", return_value="openai-response")
    def test_dispatch_llm_query_always_routes_to_openai(self, mock_openai):
        result = dispatch_llm_query("prompt", provider="legacy", model="tiny", timeout=15)
        self.assertEqual(result, "openai-response")
        mock_openai.assert_called_once_with(
            "prompt",
            model="tiny",
            temperature=None,
            max_output_tokens=None,
        )

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
        session = self.client.session
        session["spotify_access_token"] = "token"
        session["spotify_user_id"] = "cache-owner"
        session.save()
        cache.set(
            self.cache_key,
            _payload_with_owner(
                session,
                self.cache_key,
                {
                    "playlist": ["Song A - Artist A"],
                    "track_ids": ["track1", "track2"],
                    "prompt": "test prompt",
                    "debug_steps": [],
                    "errors": [],
                },
            ),
            timeout=60,
        )
        session["recommender_last_cache_key"] = self.cache_key
        session.save()

    @patch("recommender.views.create_playlist_with_tracks")
    def test_save_playlist_success(self, mock_create_playlist):
        mock_create_playlist.return_value = {
            "playlist_id": "playlist123",
            "playlist_name": "TEST Summer Vibes",
            "user_id": "user123",
            "user_display_name": "User Name",
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
            creator_display_name='initial name',
            like_count=5,
        )
        mock_create_playlist.return_value = {
            "playlist_id": "playlist123",
            "playlist_name": "TEST Summer Vibes",
            "user_id": "user456",
            "user_display_name": 'user456 name',
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
        session = self.client.session
        session["spotify_access_token"] = "token"
        session["spotify_user_id"] = "editor"
        session.save()
        self.session = session

    def test_remove_track_updates_cache(self):
        cache_key = "recommender:test"
        cache.set(
            cache_key,
            _payload_with_owner(
                self.session,
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
            ),
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
            _payload_with_owner(
                self.session,
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
            ),
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

    def test_is_customized_always_returns_false(self):
        """Test that is_customized property always returns False for placeholder"""
        prefs = get_default_preferences()
        # Until user settings UI is implemented, this should always be False
        self.assertFalse(prefs.is_customized)

    def test_get_preferences_for_request(self):
        """Test get_preferences_for_request returns default preferences"""
        from django.test import RequestFactory
        from recommender.services.user_preferences import get_preferences_for_request

        factory = RequestFactory()
        request = factory.get('/test/')

        prefs = get_preferences_for_request(request)
        # Should return same as get_default_preferences()
        default_prefs = get_default_preferences()
        self.assertEqual(prefs.track_count, default_prefs.track_count)
        self.assertEqual(prefs.enforce_unique_tracks, default_prefs.enforce_unique_tracks)
        self.assertEqual(prefs.allow_seed_only_playlists, default_prefs.allow_seed_only_playlists)


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

    @override_settings(RECOMMENDER_DEBUG_VIEW_ENABLED=False)
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
            "llm_provider": "LegacyLLM",
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
        self.assertEqual(context["llm_provider"], "openai")
        self.assertEqual(context["llm_provider_default"], "openai")
        self.assertEqual(context["debug_steps"], [])
        self.assertIsNone(context["profile_snapshot"])

    @override_settings(RECOMMENDER_DEBUG_VIEW_ENABLED=True)
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
        self.assertEqual(context["llm_provider"], "openai")
        self.assertEqual(context["llm_provider_default"], "openai")
        self.assertEqual(context["seed_track_details"][0]["name"], "Seed")
        self.assertEqual(context["similar_track_details"][0]["name"], "Similar")
