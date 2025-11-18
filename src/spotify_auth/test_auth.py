"""Tests for spotify_auth views and session helpers."""

# pylint: disable=duplicate-code

import json
import time
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse
from requests.exceptions import RequestException
from spotipy.exceptions import SpotifyException
from spotify_auth.views import SpotifyCallbackView


class SpotifyLoginViewTests(TestCase):
    """Tests for the Spotify login initiation view"""

    def setUp(self):
        self.client = Client()
        self.login_url = reverse('spotify_auth:login')

    def test_login_redirects_to_spotify(self):
        """Test that login view redirects to Spotify authorization page"""
        response = self.client.get(self.login_url)

        # Should redirect
        self.assertEqual(response.status_code, 302)

        # Should redirect to Spotify
        self.assertTrue(response.url.startswith('https://accounts.spotify.com/authorize'))

    def test_login_sets_state_in_session(self):
        """Test that a state token is set in the session for CSRF protection"""
        self.client.get(self.login_url)

        # State should be stored in session
        self.assertIn('spotify_auth_state', self.client.session)

        # State should not be empty
        state = self.client.session['spotify_auth_state']
        self.assertTrue(len(state) > 0)

    def test_login_includes_required_parameters(self):
        """Test that the authorization URL includes all required parameters"""
        response = self.client.get(self.login_url)

        # Check that URL contains required parameters
        self.assertIn('client_id=', response.url)
        self.assertIn('response_type=code', response.url)
        self.assertIn('redirect_uri=', response.url)
        self.assertIn('state=', response.url)
        self.assertIn('scope=', response.url)

    def test_login_uses_correct_client_id(self):
        """Test that the correct client ID from settings is used"""
        response = self.client.get(self.login_url)

        if settings.SPOTIFY_CLIENT_ID:
            self.assertIn(f'client_id={settings.SPOTIFY_CLIENT_ID}', response.url)

    def test_login_redirects_to_dashboard_when_session_valid(self):
        """Existing Spotify session should skip new authorization"""
        session = self.client.session
        session['spotify_access_token'] = 'cached_token'
        session['spotify_token_expires_at'] = int(time.time()) + 3600
        session.save()

        response = self.client.get(self.login_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:dashboard'))

    @patch('spotify_auth.session.requests.post')
    def test_login_refreshes_expired_token(self, mock_post):
        """Expired access token should refresh automatically before redirecting"""
        session = self.client.session
        session['spotify_access_token'] = 'expired_token'
        session['spotify_refresh_token'] = 'refresh_token'
        session['spotify_token_expires_at'] = int(time.time()) - 5
        session.save()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'new_access_token',
            'refresh_token': 'new_refresh_token',
            'expires_in': 3600,
        }
        mock_post.return_value = mock_response

        response = self.client.get(self.login_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:dashboard'))
        self.assertEqual(self.client.session['spotify_access_token'], 'new_access_token')
        self.assertEqual(self.client.session['spotify_refresh_token'], 'new_refresh_token')
        self.assertIn('spotify_token_expires_at', self.client.session)
        mock_post.assert_called_once()


class SpotifyCallbackViewTests(TestCase):
    """Tests for the Spotify OAuth callback view"""

    def setUp(self):
        self.client = Client()
        self.callback_url = reverse('spotify_auth:callback')

    def test_callback_without_code_returns_error(self):
        """Test that callback without authorization code returns an error"""
        response = self.client.get(self.callback_url)

        # Should return an error response
        self.assertEqual(response.status_code, 400)

    def test_callback_with_error_parameter(self):
        """Test that callback handles Spotify error responses"""
        response = self.client.get(self.callback_url, {'error': 'access_denied'})

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['error'], 'access_denied')

    def test_callback_state_mismatch_returns_error(self):
        """Test that mismatched state parameter is rejected (CSRF protection)"""
        # Set a state in session
        session = self.client.session
        session['spotify_auth_state'] = 'correct_state'
        session.save()

        # Try to use a different state
        response = self.client.get(self.callback_url, {
            'code': 'test_code',
            'state': 'wrong_state'
        })

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('State mismatch', data['error'])

    def test_callback_without_state_in_session(self):
        """Test that callback without state in session is rejected"""
        response = self.client.get(self.callback_url, {
            'code': 'test_code',
            'state': 'some_state'
        })

        self.assertEqual(response.status_code, 400)

    @patch('spotify_auth.views.requests.post')
    @patch('spotify_auth.views.SpotifyCallbackView.get_user_profile')
    def test_successful_callback_flow(self, mock_get_profile, mock_post):
        """Test successful OAuth callback with valid tokens"""
        # Set up session state
        session = self.client.session
        session['spotify_auth_state'] = 'test_state'
        session.save()

        # Mock the token exchange response
        mock_token_response = Mock()
        mock_token_response.status_code = 200
        mock_token_response.json.return_value = {
            'access_token': 'test_access_token',
            'refresh_token': 'test_refresh_token',
            'expires_in': 3600
        }
        mock_post.return_value = mock_token_response

        # Mock the user profile response
        mock_get_profile.return_value = {
            'id': 'test_user_id',
            'display_name': 'Test User'
        }

        # Make the callback request
        response = self.client.get(self.callback_url, {
            'code': 'test_auth_code',
            'state': 'test_state'
        })

        # Should redirect to dashboard
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:dashboard'))

        # Tokens should be stored in session
        self.assertEqual(self.client.session['spotify_access_token'], 'test_access_token')
        self.assertEqual(self.client.session['spotify_refresh_token'], 'test_refresh_token')
        self.assertEqual(self.client.session['spotify_expires_in'], 3600)
        self.assertIn('spotify_token_expires_at', self.client.session)

        # User info should be stored in session
        self.assertEqual(self.client.session['spotify_user_id'], 'test_user_id')
        self.assertEqual(self.client.session['spotify_display_name'], 'Test User')

        # State should be cleared from session
        self.assertNotIn('spotify_auth_state', self.client.session)

    @patch('spotify_auth.views.requests.post')
    def test_callback_with_failed_token_exchange(self, mock_post):
        """Test callback when Spotify token exchange fails"""
        # Set up session state
        session = self.client.session
        session['spotify_auth_state'] = 'test_state'
        session.save()

        # Mock a failed token response
        mock_token_response = Mock()
        mock_token_response.status_code = 400
        mock_post.return_value = mock_token_response

        # Make the callback request
        response = self.client.get(self.callback_url, {
            'code': 'test_auth_code',
            'state': 'test_state'
        })

        # Should return error
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('Failed to get access token', data['error'])

    @patch('spotify_auth.views.requests.post')
    def test_callback_with_network_error_during_token_exchange(self, mock_post):
        """Test callback when network error occurs during token exchange"""
        # Set up session state
        session = self.client.session
        session['spotify_auth_state'] = 'test_state'
        session.save()

        # Mock a network error
        mock_post.side_effect = RequestException("Connection timeout")

        # Make the callback request
        response = self.client.get(self.callback_url, {
            'code': 'test_auth_code',
            'state': 'test_state'
        })

        # Should return 502 Bad Gateway
        self.assertEqual(response.status_code, 502)
        data = json.loads(response.content)
        self.assertIn('Unable to reach Spotify', data['error'])

    @patch('spotify_auth.views.SpotifyCallbackView.get_user_profile')
    @patch('spotify_auth.views.requests.post')
    def test_callback_handles_user_profile_fetch_failure(self, mock_post, mock_get_profile):
        """Test callback gracefully handles user profile fetch failure"""
        # Set up session state
        session = self.client.session
        session['spotify_auth_state'] = 'test_state'
        session.save()

        # Mock successful token exchange
        mock_token_response = Mock()
        mock_token_response.status_code = 200
        mock_token_response.json.return_value = {
            'access_token': 'test_access_token',
            'refresh_token': 'test_refresh_token',
            'expires_in': 3600
        }
        mock_post.return_value = mock_token_response

        # Mock user profile fetch to return None (failure)
        mock_get_profile.return_value = None

        # Make the callback request
        response = self.client.get(self.callback_url, {
            'code': 'test_auth_code',
            'state': 'test_state'
        })

        # Should still redirect to dashboard despite profile fetch failure
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:dashboard'))

        # Tokens should still be stored
        self.assertEqual(self.client.session.get('spotify_access_token'), 'test_access_token')

        # User profile data should not be stored
        self.assertNotIn('spotify_user_id', self.client.session)
        self.assertNotIn('spotify_display_name', self.client.session)

    @patch('spotify_auth.views.requests.get')
    def test_get_user_profile_with_network_error(self, mock_get):
        """Test get_user_profile handles network errors gracefully"""
        # Mock a network error
        mock_get.side_effect = RequestException("Network error")

        view = SpotifyCallbackView()
        result = view.get_user_profile('test_token')

        # Should return None on network error
        self.assertIsNone(result)

    @patch('spotify_auth.views.requests.get')
    def test_get_user_profile_with_failed_response(self, mock_get):
        """Test get_user_profile handles non-200 responses"""
        # Mock a failed response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        view = SpotifyCallbackView()
        result = view.get_user_profile('test_token')

        # Should return None on failed response
        self.assertIsNone(result)

    @patch('spotify_auth.views.requests.post')
    def test_callback_token_exchange_parameters(self, mock_post):
        """Test that token exchange includes correct parameters"""
        # Set up session state
        session = self.client.session
        session['spotify_auth_state'] = 'test_state'
        session.save()

        # Mock the token response
        mock_token_response = Mock()
        mock_token_response.status_code = 200
        mock_token_response.json.return_value = {
            'access_token': 'test_token',
            'refresh_token': 'test_refresh',
            'expires_in': 3600
        }
        mock_post.return_value = mock_token_response

        # Make the callback request
        self.client.get(self.callback_url, {
            'code': 'test_auth_code',
            'state': 'test_state'
        })

        # Verify the token exchange was called with correct parameters
        mock_post.assert_called_once()
        call_args = mock_post.call_args

        # Check the URL
        self.assertEqual(call_args[0][0], 'https://accounts.spotify.com/api/token')

        # Check the data parameters
        data = call_args[1]['data']
        self.assertEqual(data['grant_type'], 'authorization_code')
        self.assertEqual(data['code'], 'test_auth_code')
        self.assertEqual(data['redirect_uri'], settings.SPOTIFY_REDIRECT_URI)
        self.assertEqual(data['client_id'], settings.SPOTIFY_CLIENT_ID)
        self.assertEqual(data['client_secret'], settings.SPOTIFY_CLIENT_SECRET)


class SpotifyRefreshTokenViewTests(TestCase):
    """Tests for the token refresh view"""

    def setUp(self):
        self.client = Client()
        self.refresh_url = reverse('spotify_auth:refresh')

    def test_refresh_without_token_returns_error(self):
        """Test that refresh without a refresh token returns an error"""
        response = self.client.post(self.refresh_url)

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('No refresh token available', data['error'])

    @patch('spotify_auth.views.requests.post')
    def test_successful_token_refresh(self, mock_post):
        """Test successful access token refresh"""
        # Set up session with refresh token
        session = self.client.session
        session['spotify_refresh_token'] = 'test_refresh_token'
        session.save()

        # Mock the refresh response
        mock_refresh_response = Mock()
        mock_refresh_response.status_code = 200
        mock_refresh_response.json.return_value = {
            'access_token': 'new_access_token',
            'expires_in': 3600
        }
        mock_post.return_value = mock_refresh_response

        # Make the refresh request
        response = self.client.post(self.refresh_url)

        # Should succeed
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['message'], 'Token refreshed successfully')

        # New access token should be in session
        self.assertEqual(self.client.session['spotify_access_token'], 'new_access_token')
        self.assertEqual(self.client.session['spotify_expires_in'], 3600)
        self.assertIn('spotify_token_expires_at', self.client.session)

    @patch('spotify_auth.views.requests.post')
    def test_failed_token_refresh(self, mock_post):
        """Test token refresh failure"""
        # Set up session with refresh token
        session = self.client.session
        session['spotify_refresh_token'] = 'invalid_refresh_token'
        session.save()

        # Mock a failed refresh response
        mock_refresh_response = Mock()
        mock_refresh_response.status_code = 400
        mock_post.return_value = mock_refresh_response

        # Make the refresh request
        response = self.client.post(self.refresh_url)

        # Should return error
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('Failed to refresh token', data['error'])

    @patch('spotify_auth.views.requests.post')
    def test_refresh_token_parameters(self, mock_post):
        """Test that refresh request includes correct parameters"""
        # Set up session with refresh token
        session = self.client.session
        session['spotify_refresh_token'] = 'test_refresh_token'
        session.save()

        # Mock the refresh response
        mock_refresh_response = Mock()
        mock_refresh_response.status_code = 200
        mock_refresh_response.json.return_value = {
            'access_token': 'new_token',
            'expires_in': 3600
        }
        mock_post.return_value = mock_refresh_response

        # Make the refresh request
        self.client.post(self.refresh_url)

        # Verify the refresh was called with correct parameters
        mock_post.assert_called_once()
        call_args = mock_post.call_args

        # Check the URL
        self.assertEqual(call_args[0][0], 'https://accounts.spotify.com/api/token')

        # Check the data parameters
        data = call_args[1]['data']
        self.assertEqual(data['grant_type'], 'refresh_token')
        self.assertEqual(data['refresh_token'], 'test_refresh_token')
        self.assertEqual(data['client_id'], settings.SPOTIFY_CLIENT_ID)
        self.assertEqual(data['client_secret'], settings.SPOTIFY_CLIENT_SECRET)

    @patch('spotify_auth.session.requests.post')
    def test_refresh_network_failure_returns_502(self, mock_post):
        """Network failures should bubble up as a 502 response"""
        session = self.client.session
        session['spotify_refresh_token'] = 'test_refresh_token'
        session.save()

        mock_post.side_effect = RequestException("timeout")

        response = self.client.post(self.refresh_url)

        self.assertEqual(response.status_code, 502)
        data = json.loads(response.content)
        self.assertIn('Unable to reach Spotify', data['error'])


class SpotifyIntegrationTests(TestCase):
    """Integration tests for the full OAuth flow"""

    def setUp(self):
        self.client = Client()

    @patch('spotify_auth.views.requests.post')
    @patch('spotify_auth.views.requests.get')
    def test_complete_oauth_flow(self, mock_get, mock_post):
        """Test the complete OAuth flow from login to callback"""
        # Step 1: Initiate login
        login_response = self.client.get(reverse('spotify_auth:login'))
        self.assertEqual(login_response.status_code, 302)

        # Extract state from session
        state = self.client.session['spotify_auth_state']
        self.assertIsNotNone(state)

        # Step 2: Mock token exchange
        mock_token_response = Mock()
        mock_token_response.status_code = 200
        mock_token_response.json.return_value = {
            'access_token': 'test_access_token',
            'refresh_token': 'test_refresh_token',
            'expires_in': 3600
        }
        mock_post.return_value = mock_token_response

        # Mock user profile via requests.get (used by get_user_profile method)
        mock_profile_response = Mock()
        mock_profile_response.status_code = 200
        mock_profile_response.json.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'email': 'test@example.com'
        }
        mock_get.return_value = mock_profile_response

        # Step 3: Complete callback
        callback_response = self.client.get(reverse('spotify_auth:callback'), {
            'code': 'auth_code',
            'state': state
        })

        # Should redirect to dashboard
        self.assertEqual(callback_response.status_code, 302)

        # Session should contain tokens
        self.assertIn('spotify_access_token', self.client.session)
        self.assertIn('spotify_refresh_token', self.client.session)
        self.assertIn('spotify_user_id', self.client.session)
        self.assertIn('spotify_token_expires_at', self.client.session)


class SpotifyDashboardViewTests(TestCase):
    """Tests for the Spotify dashboard view"""

    def setUp(self):
        self.client = Client()
        self.dashboard_url = reverse('dashboard:dashboard')

    def test_dashboard_redirects_without_token(self):
        """Test that dashboard redirects to login if not authenticated"""
        response = self.client.get(self.dashboard_url)

        # Should redirect to login
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('spotify_auth:login'))

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_with_valid_token(self, mock_spotify):
        """Test dashboard displays user data with valid token"""
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

        # Make request
        response = self.client.get(self.dashboard_url)

        # Should render successfully
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/dashboard.html')

        # Check context data
        self.assertEqual(response.context['username'], 'Test User')
        self.assertEqual(response.context['user_id'], 'test_user_id')
        self.assertEqual(response.context['email'], 'test@example.com')
        self.assertEqual(response.context['followers'], 42)
        self.assertIsNotNone(response.context['last_song'])
        self.assertEqual(response.context['last_song']['name'], 'Test Song')
        self.assertEqual(response.context['last_song']['artist'], 'Test Artist')
        self.assertIn('playlists', response.context)

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_without_display_name(self, mock_spotify):
        """Test dashboard uses user ID when display name is not available"""
        # Set up session with access token
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        # Mock Spotify API responses
        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        # Mock user profile without display_name
        mock_sp_instance.current_user.return_value = {
            'id': 'test_user_id',
            'email': 'test@example.com',
            'followers': {'total': 0}
        }

        # Mock empty recently played
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        # Make request
        response = self.client.get(self.dashboard_url)

        # Should use user ID as username
        self.assertEqual(response.context['username'], 'test_user_id')

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_with_no_recent_tracks(self, mock_spotify):
        """Test dashboard handles no recent listening history"""
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
            'followers': {'total': 0}
        }

        # Mock empty recently played
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        # Make request
        response = self.client.get(self.dashboard_url)

        # Should handle empty history gracefully
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['last_song'])

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_with_expired_token(self, mock_spotify):
        """Test dashboard redirects to login when token is expired"""
        # Set up session with access token
        session = self.client.session
        session['spotify_access_token'] = 'expired_token'
        session.save()

        # Mock Spotify API to raise 401 error
        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        # Create a SpotifyException with 401 status
        mock_sp_instance.current_user.side_effect = SpotifyException(
            http_status=401,
            code=-1,
            msg='The access token expired'
        )

        # Make request
        response = self.client.get(self.dashboard_url)

        # Should redirect to login
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('spotify_auth:login'))

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_with_api_error(self, mock_spotify):
        """Test dashboard handles Spotify API errors gracefully"""
        # Set up session with access token
        session = self.client.session
        session['spotify_access_token'] = 'test_access_token'
        session.save()

        # Mock Spotify API to raise error
        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.side_effect = SpotifyException(
            http_status=500,
            code=-1,
            msg='Internal Server Error'
        )

        # Make request
        response = self.client.get(self.dashboard_url)

        # Should render with error message
        self.assertEqual(response.status_code, 200)
        self.assertIn('error', response.context)
        self.assertIn('Error fetching Spotify data', response.context['error'])

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_with_multiple_artists(self, mock_spotify):
        """Test dashboard properly formats songs with multiple artists"""
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

        # Make request
        response = self.client.get(self.dashboard_url)

        # Should format multiple artists correctly
        self.assertEqual(
            response.context['last_song']['artist'],
            'Artist One, Artist Two, Artist Three',
        )

    @patch('dashboard.views.spotipy.Spotify')
    def test_dashboard_creates_spotify_client_with_token(self, mock_spotify):
        """Test that Spotify client is created with the correct token"""
        # Set up session with access token
        session = self.client.session
        session['spotify_access_token'] = 'my_test_token'
        session.save()

        # Mock Spotify API responses
        mock_sp_instance = Mock()
        mock_spotify.return_value = mock_sp_instance

        mock_sp_instance.current_user.return_value = {
            'id': 'test_user',
            'followers': {'total': 0}
        }
        mock_sp_instance.current_user_recently_played.return_value = {'items': []}

        # Make request
        self.client.get(self.dashboard_url)

        # Verify Spotify client was created with correct token
        mock_spotify.assert_called_once_with(auth='my_test_token')
