import json

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth.models import User
from unittest.mock import patch, Mock
from explorer.models import Playlist, Song


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
        Playlist.objects.create(
            name='Playlist 1',
            creator=self.user,
            likes=10,
            spotify_id='p1'
        )
        Playlist.objects.create(
            name='Playlist 2',
            creator=self.user,
            likes=5,
            spotify_id='p2'
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
        Playlist.objects.all().delete()

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
        Playlist.objects.create(
            name='Test Playlist',
            creator=self.user,
            likes=10,
            spotify_id='test123',
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

        Playlist.objects.create(
            name='Sample Playlist',
            description='',
            creator=self.user,
            likes=0,
            spotify_id='toggle-sample',
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

        Playlist.objects.create(
            name='Visible Playlist',
            description='',
            creator=self.user,
            likes=0,
            spotify_id='toggle-visible',
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
        self.assertTrue(response.context['llm_toggle_visible'])
        self.assertIn('id="llm-toggle"', content)
        self.assertIn('LLM Provider', content)


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
        p1 = Playlist.objects.create(
            name='Top Hits',
            description='Popular songs',
            creator=self.user,
            likes=50,
            spotify_id='hits',
            cover_image='http://image1.url'
        )
        Song.objects.create(playlist=p1, name='Song 1', artist='Artist 1')
        Song.objects.create(playlist=p1, name='Song 2', artist='Artist 2')

        p2 = Playlist.objects.create(
            name='Chill Vibes',
            description='Relaxing music',
            creator=self.user,
            likes=30,
            spotify_id='chill'
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
        # Should be ordered by likes descending
        self.assertEqual(playlists[0].name, 'Top Hits')
        self.assertEqual(playlists[1].name, 'Chill Vibes')

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
