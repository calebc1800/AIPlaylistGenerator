import json

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth.models import User
from unittest.mock import patch, Mock
from recommender.models import SavedPlaylist


class DashboardViewTests(TestCase):
    """Tests for the Dashboard view"""

    def setUp(self):
        self.client = Client()
        self.dashboard_url = reverse('dashboard:dashboard')
        self.user = User.objects.create_user(username='testuser', password='testpass')

    def test_dashboard_redirects_without_token(self):
        """Test that dashboard redirects to login if not authenticated"""
        response = self.client.get(self.dashboard_url)

        # Should redirect to login
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('spotify_auth:login'))
        self.assertNotIn('spotify_access_token', self.client.session)

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_renders_with_valid_token(self, mock_spotify):
        """Test dashboard renders successfully with valid Spotify token"""
        # Set up session with access token
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        # Mock Spotify API responses
        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        # Mock user profile
        mock_sp_instance.current_user.return_value = {
            'id': 'test_user_id',
            'display_name': 'Test User',
            'email': 'test@example.com',
            'followers': {'total': 42},
            'external_urls': {'spotify': 'https://open.spotify.com/user/test_user_id'}
        }

        # Mock recently played
        mock_sp_instance.current_user_recently_played.return_value = {
            'items': []
        }

        # Make request
        response = self.client.get(self.dashboard_url)

        # Should render successfully
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/dashboard.html')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_displays_user_info(self, mock_spotify):
        """Test that dashboard displays user information correctly"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user_id',
            'display_name': 'Test User',
            'email': 'test@example.com',
            'followers': {'total': 100}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Check context data
        self.assertEqual(response.context['username'], 'Test User')
        self.assertEqual(response.context['user_id'], 'test_user_id')
        self.assertEqual(response.context['email'], 'test@example.com')
        self.assertEqual(response.context['followers'], 100)

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_uses_user_id_when_no_display_name(self, mock_spotify):
        """Test dashboard uses user ID when display name is not available"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        # Mock user profile without display_name
        mock_sp_instance.current_user.return_value = {
            'id': 'test_user_id',
            'email': 'test@example.com',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Should use user ID as username
        self.assertEqual(response.context['username'], 'test_user_id')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_displays_last_played_song(self, mock_spotify):
        """Test that dashboard displays the last played song"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }

        # Mock recently played with a track
        mock_sp_instance.current_user_recently_played.return_value = {
            'items': [
                {
                    'track': {
                        'name': 'Test Song',
                        'artists': [{'name': 'Test Artist'}],
                        'album': {
                            'name': 'Test Album',
                            'images': [{'url': 'https://example.com/image.jpg'}]
                        }
                    },
                    'played_at': '2024-01-01T12:00:00Z'
                }
            ]
        }

        response = self.client.get(self.dashboard_url)

        # Check last song data
        self.assertIsNotNone(response.context['last_song'])
        self.assertEqual(response.context['last_song']['name'], 'Test Song')
        self.assertEqual(response.context['last_song']['artist'], 'Test Artist')
        self.assertEqual(response.context['last_song']['album'], 'Test Album')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_handles_multiple_artists(self, mock_spotify):
        """Test dashboard properly formats songs with multiple artists"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }

        # Mock recently played with multiple artists
        mock_sp_instance.current_user_recently_played.return_value = {
            'items': [
                {
                    'track': {
                        'name': 'Collaboration Song',
                        'artists': [
                            {'name': 'Artist One'},
                            {'name': 'Artist Two'},
                            {'name': 'Artist Three'}
                        ],
                        'album': {
                            'name': 'Test Album',
                            'images': [{'url': 'https://example.com/image.jpg'}]
                        }
                    },
                    'played_at': '2024-01-01T12:00:00Z'
                }
            ]
        }

        response = self.client.get(self.dashboard_url)

        # Should format multiple artists correctly
        self.assertEqual(
            response.context['last_song']['artist'],
            'Artist One, Artist Two, Artist Three'
        )

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_handles_no_recent_tracks(self, mock_spotify):
        """Test dashboard handles no recent listening history gracefully"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }

        # Mock empty recently played
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Should handle empty history gracefully
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['last_song'])

    @patch('dashboard.views.get_genre_breakdown')
    @patch('dashboard.views.summarize_generation_stats')
    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_includes_generated_stats(self, mock_spotify, mock_summary, mock_breakdown):
        """Dashboard context should include generation stats for templating."""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_summary.return_value = {
            'total_playlists': 2,
            'total_tracks': 50,
            'total_hours': 3.5,
            'total_tokens': 2500,
            'avg_novelty': 78.2,
            'top_genre': 'Indie',
            'last_generated_at': '2024-01-01T12:00:00Z',
        }
        mock_breakdown.return_value = [{'genre': 'Indie', 'percentage': 60}]

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance
        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['generated_stats']['total_playlists'], 2)
        self.assertEqual(response.context['genre_breakdown'][0]['genre'], 'Indie')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_with_expired_token(self, mock_spotify):
        """Test dashboard redirects to login when token is expired"""
        session = self.client.session
        session['spotify_access_token'] = 'expired_token'
        session.save()

        # Mock Spotify API to raise 401 error
        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        # Create a SpotifyException with 401 status
        from spotipy.exceptions import SpotifyException
        mock_sp_instance.current_user.side_effect = SpotifyException(
            http_status=401,
            code=-1,
            msg='The access token expired'
        )

        response = self.client.get(self.dashboard_url)

        # Should redirect to login
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('spotify_auth:login'))

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_with_api_error(self, mock_spotify):
        """Test dashboard handles Spotify API errors gracefully"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        # Mock Spotify API to raise error
        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        from spotipy.exceptions import SpotifyException
        mock_sp_instance.current_user.side_effect = SpotifyException(
            http_status=500,
            code=-1,
            msg='Internal Server Error'
        )

        response = self.client.get(self.dashboard_url)

        # Should render with error message
        self.assertEqual(response.status_code, 200)
        self.assertIn('error', response.context)
        self.assertIn('Error fetching Spotify data', response.context['error'])

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_displays_playlists(self, mock_spotify):
        """Test that dashboard displays playlists in explore tab"""
        # Create some test playlists
        SavedPlaylist.objects.create(
            playlist_name='Playlist 1',
            playlist_id='p1',
            creator_user_id='user1',
            creator_display_name='testuser',
            like_count=10
        )
        SavedPlaylist.objects.create(
            playlist_name='Playlist 2',
            playlist_id='p2',
            creator_user_id='user1',
            creator_display_name='testuser',
            like_count=5
        )

        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Check that playlists are in context
        self.assertIn('playlists', response.context)
        playlists = response.context['playlists']
        self.assertEqual(playlists.count(), 2)

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_handles_empty_playlists_gracefully(self, mock_spotify):
        """Test that dashboard handles empty playlist database gracefully"""
        # Delete all existing playlists to ensure database is empty
        SavedPlaylist.objects.all().delete()

        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Should render successfully even without playlists
        self.assertEqual(response.status_code, 200)
        self.assertIn('playlists', response.context)

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_context_has_all_required_fields(self, mock_spotify):
        """Test that dashboard context has all required fields"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user_id',
            'display_name': 'Test User',
            'email': 'test@example.com',
            'followers': {'total': 42},
            'external_urls': {'spotify': 'https://open.spotify.com/user/test_user_id'}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Check all required context fields are present
        required_fields = [
            'username',
            'user_id',
            'email',
            'followers',
            'last_song',
            'profile_url',
            'playlists'
        ]

        for field in required_fields:
            self.assertIn(field, response.context, f"Missing {field} in context")

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_creates_spotify_client_with_token(self, mock_spotify):
        """Test that Spotify client is created with the correct token"""
        session = self.client.session
        session['spotify_access_token'] = 'my_test_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        self.client.get(self.dashboard_url)

        # Verify Spotify client was created with correct token
        mock_spotify.assert_called_once_with(auth='my_test_token')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_template_content(self, mock_spotify):
        """Test that dashboard template contains expected content"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'email': 'test@example.com',
            'followers': {'total': 42}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Check for expected content in the response
        self.assertContains(response, 'Dashboard')
        self.assertContains(response, 'Welcome, Test User!')
        self.assertContains(response, 'Explore')
        self.assertContains(response, 'Create')
        self.assertContains(response, 'Stats')
        self.assertContains(response, 'Account')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_has_tab_navigation(self, mock_spotify):
        """Test that dashboard includes tab navigation structure"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Check for tab navigation elements
        self.assertContains(response, 'class="nav-tabs"')
        self.assertContains(response, 'class="tab')
        self.assertContains(response, 'data-tab="explore"')
        self.assertContains(response, 'data-tab="create"')
        self.assertContains(response, 'data-tab="stats"')
        self.assertContains(response, 'data-tab="account"')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_displays_inline_playlist_cards(self, mock_spotify):
        """Test that dashboard displays playlists with inline card structure"""
        SavedPlaylist.objects.create(
            playlist_name='Test Playlist',
            playlist_id='test123',
            creator_user_id='user1',
            creator_display_name='testuser',
            like_count=10,
            spotify_uri='spotify:playlist:test123'
        )

        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Check for inline playlist card structure
        self.assertContains(response, 'playlist-card')
        self.assertContains(response, 'playlist-image')
        self.assertContains(response, 'playlist-title')
        self.assertContains(response, 'Test Playlist')

    @override_settings(RECOMMENDER_DEBUG_VIEW_ENABLED=False)
    @patch('dashboard.views.spotipy.Spotify')
    def test_llm_toggle_hidden_when_debug_disabled(self, mock_spotify):
        """Toggle switch should not render when debug mode is disabled."""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        SavedPlaylist.objects.create(
            playlist_name='Sample Playlist',
            playlist_id='toggle-sample',
            creator_user_id='user1',
            creator_display_name='testuser',
            like_count=0,
            description=''
        )

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance
        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0},
            'external_urls': {'spotify': 'https://example.com/profile'},
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['llm_toggle_visible'])
        self.assertNotIn('id="llm-toggle"', content)
        self.assertNotIn('LLM Provider', content)

    @override_settings(RECOMMENDER_DEBUG_VIEW_ENABLED=True)
    @patch('dashboard.views.spotipy.Spotify')
    def test_llm_toggle_visible_when_debug_enabled(self, mock_spotify):
        """Toggle switch should render when debug mode is enabled."""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        SavedPlaylist.objects.create(
            playlist_name='Visible Playlist',
            playlist_id='toggle-visible',
            creator_user_id='user1',
            creator_display_name='testuser',
            like_count=0,
            description=''
        )

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance
        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0},
            'external_urls': {'spotify': 'https://example.com/profile'},
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['llm_toggle_visible'])
        self.assertNotIn('id="llm-toggle"', content)
        self.assertNotIn('LLM Provider', content)


class DashboardStatsAPITests(TestCase):
    """API tests for live dashboard stats endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('dashboard:user-stats')

    @patch('dashboard.views.ensure_valid_spotify_session', return_value=False)
    def test_requires_valid_session(self, mock_session_check):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)
        self.assertIn('Authentication required', response.content.decode())

    @patch('dashboard.views._fetch_spotify_highlights')
    @patch('dashboard.views.get_genre_breakdown')
    @patch('dashboard.views.summarize_generation_stats')
    @patch('dashboard.views.spotipy.Spotify')
    @patch('dashboard.views.ensure_valid_spotify_session', return_value=True)
    def test_returns_combined_payload(
        self,
        mock_session_check,
        mock_spotify_client,
        mock_summary,
        mock_breakdown,
        mock_highlights,
    ):
        session = self.client.session
        session['spotify_access_token'] = 'test'
        session.save()

        mock_summary.return_value = {'total_playlists': 5, 'total_tracks': 120, 'total_tokens': 4200}
        mock_breakdown.return_value = [{'genre': 'Pop', 'percentage': 55.0}]
        mock_highlights.return_value = {'top_genres': [{'genre': 'Pop', 'count': 4}]}
        mock_spotify_client.return_value = Mock()

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        payload = json.loads(response.content.decode())
        self.assertEqual(payload['generated']['total_playlists'], 5)
        self.assertEqual(payload['generated']['total_tokens'], 4200)
        self.assertEqual(payload['genre_breakdown'][0]['genre'], 'Pop')
        self.assertEqual(payload['spotify']['top_genres'][0]['genre'], 'Pop')

    @patch('dashboard.views.ensure_valid_spotify_session', return_value=True)
    def test_requires_access_token(self, mock_session_check):
        """Test that API requires access token even when session is valid"""
        # Session is valid but no access token
        session = self.client.session
        session.save()

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)
        self.assertIn('Authentication required', response.content.decode())

    @patch('dashboard.views.get_genre_breakdown')
    @patch('dashboard.views.summarize_generation_stats')
    @patch('dashboard.views.spotipy.Spotify')
    @patch('dashboard.views.ensure_valid_spotify_session', return_value=True)
    def test_handles_spotify_exception_gracefully(
        self,
        mock_session_check,
        mock_spotify_client,
        mock_summary,
        mock_breakdown,
    ):
        """Test that API handles Spotify exceptions and returns empty highlights"""
        from spotipy.exceptions import SpotifyException

        session = self.client.session
        session['spotify_access_token'] = 'test'
        session.save()

        mock_summary.return_value = {'total_playlists': 3, 'total_tracks': 60}
        mock_breakdown.return_value = [{'genre': 'Jazz', 'percentage': 45.0}]

        # Mock Spotify client to raise exception
        mock_sp_instance = Mock()
        mock_spotify_client.return_value = mock_sp_instance
        mock_sp_instance.current_user_top_artists.side_effect = SpotifyException(
            http_status=503,
            code=-1,
            msg='Service Unavailable'
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        payload = json.loads(response.content.decode())
        # Should still return generated stats
        self.assertEqual(payload['generated']['total_playlists'], 3)
        # But Spotify highlights should be empty
        self.assertEqual(payload['spotify']['top_genres'], [])
        self.assertEqual(payload['spotify']['top_artists'], [])
        self.assertEqual(payload['spotify']['top_tracks'], [])


class DashboardIntegrationTests(TestCase):
    """Integration tests for dashboard functionality"""

    def setUp(self):
        self.client = Client()
        self.dashboard_url = reverse('dashboard:dashboard')
        self.user = User.objects.create_user(username='integration_test', password='pass')

    @patch('dashboard.views.spotipy.Spotify')
    def test_full_dashboard_flow_with_playlists(self, mock_spotify):
        """Test complete dashboard flow with user data and playlists"""
        # Create playlists
        p1 = SavedPlaylist.objects.create(
            playlist_name='Top Hits',
            playlist_id='hits',
            creator_user_id='user1',
            creator_display_name='integration_test',
            like_count=50,
            description='Popular songs',
            cover_image='http://image1.url'
        )

        p2 = SavedPlaylist.objects.create(
            playlist_name='Chill Vibes',
            playlist_id='chill',
            creator_user_id='user1',
            creator_display_name='integration_test',
            like_count=30,
            description='Relaxing music'
        )

        # Set up session
        session = self.client.session
        session['spotify_access_token'] = 'test_token'
        session.save()

        # Mock Spotify
        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'spotify_user',
            'display_name': 'Spotify User',
            'email': 'user@spotify.com',
            'followers': {'total': 100},
            'external_urls': {'spotify': 'https://open.spotify.com/user/spotify_user'}
        }

        mock_sp_instance.current_user_recently_played.return_value = {
            'items': [
                {
                    'track': {
                        'name': 'Last Played',
                        'artists': [{'name': 'Recent Artist'}],
                        'album': {
                            'name': 'Recent Album',
                            'images': [{'url': 'http://recent.jpg'}]
                        }
                    },
                    'played_at': '2024-01-01T12:00:00Z'
                }
            ]
        }

        response = self.client.get(self.dashboard_url)

        # Verify response
        self.assertEqual(response.status_code, 200)

        # Verify user data
        self.assertEqual(response.context['username'], 'Spotify User')
        self.assertEqual(response.context['email'], 'user@spotify.com')
        self.assertEqual(response.context['followers'], 100)

        # Verify last song
        self.assertIsNotNone(response.context['last_song'])
        self.assertEqual(response.context['last_song']['name'], 'Last Played')

        # Verify playlists
        playlists = response.context['playlists']
        self.assertEqual(playlists.count(), 2)
        # Should be ordered by like_count descending
        self.assertEqual(playlists[0].playlist_name, 'Top Hits')
        self.assertEqual(playlists[1].playlist_name, 'Chill Vibes')

        # Verify content in response
        self.assertContains(response, 'Spotify User')
        self.assertContains(response, 'Top Hits')
        self.assertContains(response, 'Chill Vibes')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_playlist_generation_form(self, mock_spotify):
        """Test that dashboard includes playlist generation form"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Check for playlist generation form elements
        self.assertContains(response, 'class="create-form"')
        self.assertContains(response, 'id="playlist_prompt"')
        self.assertContains(response, 'name="prompt"')
        self.assertContains(response, 'id="playlist_name"')
        self.assertContains(response, 'name="playlist_name"')
        self.assertContains(response, 'class="create-btn"')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_stats_section(self, mock_spotify):
        """Test that dashboard includes stats section with follower count"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'followers': {'total': 123}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Check for stats content
        self.assertContains(response, 'Your Music Stats')
        self.assertContains(response, '123')
        self.assertContains(response, 'Followers')
        self.assertContains(response, 'stat-card')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_stores_spotify_user_id_in_session(self, mock_spotify):
        """Test that dashboard stores Spotify user ID in session"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'spotify_user_123',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        response = self.client.get(self.dashboard_url)

        # Check that Spotify user ID is stored in session
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session.get('spotify_user_id'), 'spotify_user_123')

    @patch('dashboard.views.build_user_profile_seed_snapshot')
    @patch('dashboard.views.cache')
    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_builds_user_profile_snapshot(self, mock_spotify, mock_cache, mock_build_snapshot):
        """Test that dashboard builds user profile snapshot when not cached"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user_456',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        # Mock cache to return None (not cached)
        mock_cache.get.return_value = None
        mock_build_snapshot.return_value = {'snapshot': 'data'}

        response = self.client.get(self.dashboard_url)

        # Verify snapshot was built and cached
        self.assertEqual(response.status_code, 200)
        mock_build_snapshot.assert_called_once_with(mock_sp_instance)
        mock_cache.set.assert_called()

    @override_settings(RECOMMENDER_USER_PROFILE_CACHE_TTL=7200)
    @patch('dashboard.views.build_user_profile_seed_snapshot')
    @patch('dashboard.views.cache')
    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_uses_custom_cache_ttl(self, mock_spotify, mock_cache, mock_build_snapshot):
        """Test that dashboard uses custom cache TTL from settings"""
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user_789',
            'display_name': 'Test User',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        # Mock cache to return None (not cached)
        mock_cache.get.return_value = None
        mock_build_snapshot.return_value = {'snapshot': 'data'}

        response = self.client.get(self.dashboard_url)

        # Verify custom TTL was used
        self.assertEqual(response.status_code, 200)
        # Check that cache.set was called with TTL of 7200
        cache_set_calls = mock_cache.set.call_args_list
        self.assertTrue(any(call[0][2] == 7200 for call in cache_set_calls if len(call[0]) > 2))

    @patch('dashboard.views.ensure_valid_spotify_session')
    def test_dashboard_edge_case_valid_session_but_no_token(self, mock_ensure):
        """Test edge case where session is valid but access token is missing"""
        # Mock ensure_valid_spotify_session to return True
        mock_ensure.return_value = True

        # But don't set access token in session
        session = self.client.session
        session.save()

        response = self.client.get(self.dashboard_url)

        # Should redirect to login
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('spotify_auth:login'))


class HelperFunctionTests(TestCase):
    """Tests for helper functions in dashboard views"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='testpass')

    def test_resolve_generation_identifier_with_authenticated_user(self):
        """Test _resolve_generation_identifier with authenticated user"""
        from dashboard.views import _resolve_generation_identifier

        # Log in the user
        self.client.login(username='testuser', password='testpass')
        request = self.client.get(reverse('dashboard:dashboard')).wsgi_request

        identifier = _resolve_generation_identifier(request)

        # Should return user's primary key as string
        self.assertEqual(identifier, str(self.user.pk))

    def test_resolve_generation_identifier_with_spotify_user_id_param(self):
        """Test _resolve_generation_identifier with spotify_user_id parameter"""
        from dashboard.views import _resolve_generation_identifier

        request = self.client.get(reverse('dashboard:dashboard')).wsgi_request

        identifier = _resolve_generation_identifier(request, spotify_user_id='spotify_123')

        # Should return the provided spotify_user_id
        self.assertEqual(identifier, 'spotify_123')

    def test_resolve_generation_identifier_with_session_spotify_user_id(self):
        """Test _resolve_generation_identifier with spotify_user_id in session"""
        from dashboard.views import _resolve_generation_identifier

        session = self.client.session
        session['spotify_user_id'] = 'session_spotify_456'
        session.save()

        request = self.client.get(reverse('dashboard:dashboard')).wsgi_request

        identifier = _resolve_generation_identifier(request)

        # Should return spotify_user_id from session
        self.assertEqual(identifier, 'session_spotify_456')

    def test_resolve_generation_identifier_anonymous(self):
        """Test _resolve_generation_identifier with anonymous user and no spotify_user_id"""
        from dashboard.views import _resolve_generation_identifier

        request = self.client.get(reverse('dashboard:dashboard')).wsgi_request

        identifier = _resolve_generation_identifier(request)

        # Should return 'anonymous'
        self.assertEqual(identifier, 'anonymous')

    def test_ensure_session_key_with_existing_key(self):
        """Test _ensure_session_key with existing session key"""
        from dashboard.views import _ensure_session_key

        session = self.client.session
        session['test'] = 'data'
        session.save()

        request = self.client.get(reverse('dashboard:dashboard')).wsgi_request

        session_key = _ensure_session_key(request)

        # Should return the existing session key
        self.assertIsNotNone(session_key)
        self.assertEqual(session_key, request.session.session_key)

    def test_ensure_session_key_creates_new_key(self):
        """Test _ensure_session_key creates new session key if missing"""
        from dashboard.views import _ensure_session_key
        from django.test import RequestFactory
        from django.contrib.sessions.middleware import SessionMiddleware

        # Create a fresh request without a session key
        factory = RequestFactory()
        request = factory.get('/dashboard/')

        # Add session middleware
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)

        # Ensure session key is None initially
        self.assertIsNone(request.session.session_key)

        session_key = _ensure_session_key(request)

        # Should create and return a session key
        self.assertIsNotNone(session_key)
        self.assertEqual(session_key, request.session.session_key)

    @patch('dashboard.views.cache')
    def test_fetch_spotify_highlights_from_cache(self, mock_cache):
        """Test _fetch_spotify_highlights returns cached data"""
        from dashboard.views import _fetch_spotify_highlights

        session = self.client.session
        session.save()

        request = self.client.get(reverse('dashboard:dashboard')).wsgi_request

        cached_data = {
            'top_genres': [{'genre': 'Rock', 'count': 5}],
            'top_artists': [{'name': 'Artist 1'}],
            'top_tracks': [{'name': 'Track 1'}]
        }
        mock_cache.get.return_value = cached_data

        mock_sp = Mock()

        result = _fetch_spotify_highlights(request, mock_sp)

        # Should return cached data without calling Spotify API
        self.assertEqual(result, cached_data)
        mock_sp.current_user_top_artists.assert_not_called()
        mock_sp.current_user_top_tracks.assert_not_called()

    @patch('dashboard.views.cache')
    def test_fetch_spotify_highlights_with_spotify_exception(self, mock_cache):
        """Test _fetch_spotify_highlights handles Spotify exception"""
        from dashboard.views import _fetch_spotify_highlights
        from spotipy.exceptions import SpotifyException

        session = self.client.session
        session.save()

        request = self.client.get(reverse('dashboard:dashboard')).wsgi_request

        mock_cache.get.return_value = None

        mock_sp = Mock()
        mock_sp.current_user_top_artists.side_effect = SpotifyException(
            http_status=500,
            code=-1,
            msg='API Error'
        )

        result = _fetch_spotify_highlights(request, mock_sp)

        # Should return empty highlights
        self.assertEqual(result['top_genres'], [])
        self.assertEqual(result['top_artists'], [])
        self.assertEqual(result['top_tracks'], [])

    @patch('dashboard.views.cache')
    def test_fetch_spotify_highlights_builds_data(self, mock_cache):
        """Test _fetch_spotify_highlights builds highlights from Spotify API"""
        from dashboard.views import _fetch_spotify_highlights

        session = self.client.session
        session.save()

        request = self.client.get(reverse('dashboard:dashboard')).wsgi_request

        mock_cache.get.return_value = None

        mock_sp = Mock()
        mock_sp.current_user_top_artists.return_value = {
            'items': [
                {
                    'name': 'Artist 1',
                    'genres': ['rock', 'alternative', 'indie'],
                    'images': [{'url': 'https://image1.jpg'}]
                },
                {
                    'name': 'Artist 2',
                    'genres': ['pop', 'rock'],
                    'images': []
                }
            ]
        }

        mock_sp.current_user_top_tracks.return_value = {
            'items': [
                {
                    'name': 'Track 1',
                    'artists': [{'name': 'Artist A'}, {'name': 'Artist B'}],
                    'album': {
                        'name': 'Album 1',
                        'images': [{'url': 'https://album1.jpg'}]
                    }
                },
                {
                    'name': 'Track 2',
                    'artists': [{'name': 'Artist C'}],
                    'album': {
                        'name': 'Album 2',
                        'images': []
                    }
                }
            ]
        }

        result = _fetch_spotify_highlights(request, mock_sp)

        # Verify top genres (should count occurrences)
        self.assertEqual(len(result['top_genres']), 4)  # Rock appears twice
        self.assertEqual(result['top_genres'][0]['genre'], 'Rock')  # Most common
        self.assertEqual(result['top_genres'][0]['count'], 2)

        # Verify top artists
        self.assertEqual(len(result['top_artists']), 2)
        self.assertEqual(result['top_artists'][0]['name'], 'Artist 1')
        self.assertEqual(result['top_artists'][0]['genres'], ['Rock', 'Alternative', 'Indie'])
        self.assertEqual(result['top_artists'][0]['image'], 'https://image1.jpg')
        self.assertEqual(result['top_artists'][1]['image'], '')  # No image

        # Verify top tracks
        self.assertEqual(len(result['top_tracks']), 2)
        self.assertEqual(result['top_tracks'][0]['name'], 'Track 1')
        self.assertEqual(result['top_tracks'][0]['artists'], 'Artist A, Artist B')
        self.assertEqual(result['top_tracks'][0]['album'], 'Album 1')
        self.assertEqual(result['top_tracks'][0]['image'], 'https://album1.jpg')
        self.assertEqual(result['top_tracks'][1]['image'], '')  # No image

        # Verify caching
        mock_cache.set.assert_called_once()

    @patch('dashboard.views.cache')
    def test_fetch_spotify_highlights_handles_empty_response(self, mock_cache):
        """Test _fetch_spotify_highlights handles empty API responses"""
        from dashboard.views import _fetch_spotify_highlights

        session = self.client.session
        session.save()

        request = self.client.get(reverse('dashboard:dashboard')).wsgi_request

        mock_cache.get.return_value = None

        mock_sp = Mock()
        mock_sp.current_user_top_artists.return_value = {'items': []}
        mock_sp.current_user_top_tracks.return_value = {'items': []}

        result = _fetch_spotify_highlights(request, mock_sp)

        # Should return empty lists
        self.assertEqual(result['top_genres'], [])
        self.assertEqual(result['top_artists'], [])
        self.assertEqual(result['top_tracks'], [])
