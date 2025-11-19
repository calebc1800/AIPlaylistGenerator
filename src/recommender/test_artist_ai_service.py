"""Tests for the artist AI suggestion service."""

from __future__ import annotations

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from recommender.services import artist_ai_service as service


class ArtistAISuggestionsTests(SimpleTestCase):
    """Verify AI suggestions are enriched with Spotify metadata."""

    def setUp(self):
        self.seed_artists = [
            {"id": "top1", "name": "Top Artist", "genres": ["indie"], "popularity": 60},
        ]

    @patch("recommender.services.artist_ai_service.dispatch_llm_query")
    @patch("recommender.services.artist_ai_service.fetch_seed_artists")
    def test_uses_profile_cache_metadata(self, mock_fetch_seeds, mock_dispatch):
        """Cards should reuse artist metadata from the cache."""
        mock_fetch_seeds.return_value = self.seed_artists
        mock_dispatch.return_value = '[{"name": "Top Artist", "reason": "AI pick"}]'
        profile_cache = {
            "artists": {
                "top1": {
                    "id": "top1",
                    "name": "Top Artist",
                    "image": "https://img/top.jpg",
                    "genres": ["indie"],
                    "popularity": 65,
                    "followers": 12345,
                    "url": "https://open.spotify.com/artist/top1",
                }
            }
        }

        cards = service.generate_ai_artist_cards(
            "user-123",
            sp=None,
            profile_cache=profile_cache,
            limit=1,
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["name"], "Top Artist")
        self.assertEqual(cards[0]["reason"], "AI pick")
        self.assertEqual(cards[0]["followers"], 12345)

    @patch("recommender.services.artist_ai_service.dispatch_llm_query")
    @patch("recommender.services.artist_ai_service.fetch_seed_artists")
    def test_searches_spotify_when_not_in_cache(self, mock_fetch_seeds, mock_dispatch):
        """Fallback to Spotify search when artist is not cached."""
        mock_fetch_seeds.return_value = self.seed_artists
        mock_dispatch.return_value = '[{"name": "Newcomer", "reason": "Fresh sound"}]'
        mock_sp = Mock()
        mock_sp.search.return_value = {
            "artists": {
                "items": [
                    {
                        "id": "new-1",
                        "name": "Newcomer",
                        "genres": ["electronic"],
                        "images": [{"url": "https://img/new.jpg"}],
                        "popularity": 70,
                        "followers": {"total": 4000},
                        "external_urls": {"spotify": "https://open.spotify.com/artist/new-1"},
                    }
                ]
            }
        }
        mock_sp.artist_top_tracks.return_value = {"tracks": [{"id": "track-1"}]}

        cards = service.generate_ai_artist_cards(
            "user-123",
            sp=mock_sp,
            profile_cache={},
            limit=1,
        )

        self.assertEqual(cards[0]["id"], "new-1")
        self.assertEqual(cards[0]["reason"], "Fresh sound")
        mock_sp.search.assert_called_once()

    @patch("recommender.services.artist_ai_service.dispatch_llm_query")
    @patch("recommender.services.artist_ai_service.fetch_seed_artists")
    @patch("recommender.services.artist_ai_service._search_artist")
    def test_skips_artists_without_followers_or_tracks(
        self,
        mock_search,
        mock_fetch_seeds,
        mock_dispatch,
    ):
        """Invalid AI picks should be dropped in favor of seeds."""
        mock_fetch_seeds.return_value = [
            {
                "id": "seed-1",
                "name": "Seed One",
                "genres": ["pop"],
                "popularity": 55,
                "followers": 5000,
                "url": "https://open.spotify.com/artist/seed1",
            }
        ]
        mock_dispatch.return_value = '[{"name": "Fake Artist"}, {"name": "Real Artist"}]'

        def fake_search(_sp_instance, name):
            if name == "Fake Artist":
                return {
                    "id": "fake",
                    "name": "Fake Artist",
                    "followers": 0,
                    "popularity": 0,
                    "url": "",
                    "image": "",
                }
            return {
                "id": "real",
                "name": "Real Artist",
                "followers": 4000,
                "popularity": 70,
                "url": "https://open.spotify.com/artist/real",
                "image": "",
            }

        mock_search.side_effect = fake_search
        mock_sp = Mock()
        mock_sp.artist_top_tracks.side_effect = [
            {"tracks": []},
            {"tracks": [{"id": "track-1"}]},
        ]

        cards = service.generate_ai_artist_cards(
            "user-123",
            sp=mock_sp,
            profile_cache={},
            limit=1,
        )

        self.assertEqual(len(cards), 1)
        # The invalid AI suggestion should be skipped and the seed fallback used.
        self.assertEqual(cards[0]["id"], "seed-1")
        self.assertEqual(cards[0]["reason"], "From your listening history")
        self.assertGreaterEqual(mock_sp.artist_top_tracks.call_count, 2)
