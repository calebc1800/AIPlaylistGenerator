# spotify_auth/tests.py

from django.test import TestCase, Client
from django.urls import reverse
from django.conf import settings
from unittest.mock import patch, Mock
import json


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
        response = self.client.get(self.login_url)
        
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
        
        # Should redirect to home
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('home'))
        
        # Tokens should be stored in session
        self.assertEqual(self.client.session['spotify_access_token'], 'test_access_token')
        self.assertEqual(self.client.session['spotify_refresh_token'], 'test_refresh_token')
        self.assertEqual(self.client.session['spotify_expires_in'], 3600)
        
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
        
        # Mock user profile
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
        
        # Should redirect to home
        self.assertEqual(callback_response.status_code, 302)
        
        # Session should contain tokens
        self.assertIn('spotify_access_token', self.client.session)
        self.assertIn('spotify_refresh_token', self.client.session)
        self.assertIn('spotify_user_id', self.client.session)


# Run tests with: python3 manage.py test spotify_auth.tests