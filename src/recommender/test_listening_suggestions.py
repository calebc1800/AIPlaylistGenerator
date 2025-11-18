"""Tests for the listening suggestions service."""

from unittest.mock import patch

from django.test import TestCase

from recommender.services.listening_suggestions import generate_listening_suggestions


class ListeningSuggestionServiceTests(TestCase):
    """Unit tests for the listening suggestion generator."""

    @patch("recommender.services.listening_suggestions.get_genre_breakdown")
    @patch("recommender.services.listening_suggestions.summarize_generation_stats")
    def test_generate_suggestions_with_profile_data(self, mock_summary, mock_breakdown):
        """Suggestions should leverage profile cache data."""
        mock_breakdown.return_value = [
            {"genre": "indie-rock"},
            {"genre": "dream-pop"},
        ]
        mock_summary.return_value = {
            "top_genre": "alt-pop",
            "avg_novelty": 82,
            "total_playlists": 3,
        }
        profile_cache = {
            "source": "top_tracks",
            "genre_buckets": {
                "indie-folk": {"track_count": 8},
                "synth-pop": {"track_count": 4},
            },
            "artists": {
                "1": {"name": "Phoebe Bridgers", "play_count": 12},
                "2": {"name": "The 1975", "play_count": 9},
            },
        }

        prompts = generate_listening_suggestions(
            "user123",
            profile_cache=profile_cache,
            max_prompts=9,
        )

        self.assertGreaterEqual(len(prompts), 6)
        self.assertTrue(any("Indie Rock" in prompt or "Alt Pop" in prompt for prompt in prompts))
        self.assertTrue(any("Phoebe Bridgers" in prompt for prompt in prompts))

    @patch("recommender.services.listening_suggestions.get_genre_breakdown", return_value=[])
    @patch("recommender.services.listening_suggestions.summarize_generation_stats", return_value={})
    def test_generate_suggestions_without_identifier(self, _summary, _breakdown):
        """No identifier must short-circuit suggestions."""
        prompts = generate_listening_suggestions(None, profile_cache=None)
        self.assertEqual(prompts, [])
