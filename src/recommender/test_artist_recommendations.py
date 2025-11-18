"""Tests outlining the cached artist recommendation behavior."""

from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from recommender.services import artist_recommendation_service as service


class GenerateRecommendedArtistsTests(SimpleTestCase):
    """Unit-level expectations for the artist recommendation service."""

    def test_returns_empty_list_when_no_cache(self):
        with patch.object(service.cache, "get", return_value=None):
            self.assertEqual(service.generate_recommended_artists("user-123"), [])

    def test_ranks_artists_using_playcount_popularity_and_genre(self):
        profile_cache = {
            "artists": {
                "a1": {"id": "a1", "name": "Artist One", "play_count": 10, "genres": ["indie"], "popularity": 40},
                "a2": {"id": "a2", "name": "Artist Two", "play_count": 4, "genres": ["indie"], "popularity": 65},
                "a3": {"id": "a3", "name": "Artist Three", "play_count": 6, "genres": ["electro"], "popularity": 50},
            },
            "genre_buckets": {
                "indie": {"track_count": 12},
                "electro": {"track_count": 3},
            },
        }
        with patch.object(service.cache, "get", return_value=profile_cache):
            results = service.generate_recommended_artists("user-123", limit=2)

        self.assertEqual(len(results), 2)
        # Artist One should outrank Artist Two thanks to higher play count + genre weight.
        self.assertEqual(results[0]["id"], "a1")
        self.assertEqual(results[1]["id"], "a2")
        self.assertEqual(results[0]["reason"], "Heavily featured in your indie listening")

    def test_includes_metadata_from_snapshot(self):
        profile_cache = {
            "artists": {
                "a1": {
                    "id": "a1",
                    "name": "Artist One",
                    "play_count": 2,
                    "genres": [],
                    "popularity": 10,
                    "image": "https://img/1.jpg",
                    "followers": 5000,
                    "url": "https://open.spotify.com/artist/a1",
                },
            },
            "genre_buckets": {},
        }
        with patch.object(service.cache, "get", return_value=profile_cache):
            results = service.generate_recommended_artists("user-123", limit=5)

        self.assertEqual(results[0]["image"], "https://img/1.jpg")
        self.assertEqual(results[0]["followers"], 5000)
        self.assertEqual(results[0]["url"], "https://open.spotify.com/artist/a1")
        self.assertEqual(results[0]["reason"], "Frequently appears in your recent listening")
