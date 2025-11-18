"""Authentication views that integrate Spotify OAuth."""

import logging
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views import View

from .session import ensure_valid_spotify_session, refresh_access_token, store_token

logger = logging.getLogger(__name__)
SPOTIFY_HTTP_TIMEOUT = int(getattr(settings, "SPOTIFY_HTTP_TIMEOUT", 15))


class SpotifyLoginView(View):
    """Initiate the Spotify OAuth flow."""

    def get(self, request):
        """Redirect the user to Spotify's authorization page or reuse tokens."""
        if ensure_valid_spotify_session(request):
            return redirect('dashboard:dashboard')

        # Generate a random state for CSRF protection
        state = secrets.token_urlsafe(16)
        request.session['spotify_auth_state'] = state

        # Spotify authorization parameters
        scope = " ".join(getattr(settings, "SPOTIFY_SCOPES", []))

        params = {
            'client_id': settings.SPOTIFY_CLIENT_ID,
            'response_type': 'code',
            'redirect_uri': settings.SPOTIFY_REDIRECT_URI,
            'state': state,
            'scope': scope,
        }

        auth_url = f"https://accounts.spotify.com/authorize?{urlencode(params)}"
        return redirect(auth_url)


class SpotifyCallbackView(View):
    """Handle the OAuth callback from Spotify."""

    def get(self, request):
        """Verify the callback state and persist the resulting tokens."""
        # Get the authorization code and state from query params
        code = request.GET.get('code')
        state = request.GET.get('state')
        error = request.GET.get('error')

        # Check for errors
        if error:
            return JsonResponse({'error': error}, status=400)

        # Verify state to prevent CSRF attacks
        stored_state = request.session.get('spotify_auth_state')
        if not state or state != stored_state:
            return JsonResponse({'error': 'State mismatch. Possible CSRF attack.'}, status=400)

        # Clear the state from session
        del request.session['spotify_auth_state']

        # Exchange authorization code for access token
        token_url = 'https://accounts.spotify.com/api/token'
        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': settings.SPOTIFY_REDIRECT_URI,
            'client_id': settings.SPOTIFY_CLIENT_ID,
            'client_secret': settings.SPOTIFY_CLIENT_SECRET,
        }

        try:
            response = requests.post(token_url, data=data, timeout=SPOTIFY_HTTP_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            logger.exception("Spotify token exchange failed: %s", exc)
            return JsonResponse({'error': 'Unable to reach Spotify at the moment.'}, status=502)

        if response.status_code != 200:
            return JsonResponse({'error': 'Failed to get access token'}, status=400)

        token_data = response.json()

        # Store tokens in session (or save to database)
        store_token(request.session, token_data)

        # Optional: Get user profile data
        user_profile = self.get_user_profile(token_data.get('access_token'))

        # Store user info in session
        if user_profile:
            request.session['spotify_user_id'] = user_profile.get('id')
            request.session['spotify_display_name'] = user_profile.get('display_name')

        return redirect('dashboard:dashboard')

    def get_user_profile(self, access_token):
        """Fetch the user's Spotify profile"""
        headers = {'Authorization': f'Bearer {access_token}'}
        try:
            response = requests.get(
                'https://api.spotify.com/v1/me',
                headers=headers,
                timeout=SPOTIFY_HTTP_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            logger.exception("Spotify profile fetch failed: %s", exc)
            return None

        if response.status_code == 200:
            return response.json()
        return None


class SpotifyRefreshTokenView(View):
    """Refreshes the Spotify access token"""

    def post(self, request):
        """Refresh the requester's Spotify access token."""
        refresh_token = request.session.get('spotify_refresh_token')

        if not refresh_token:
            return JsonResponse({'error': 'No refresh token available'}, status=400)

        refreshed, reason = refresh_access_token(request.session)
        if not refreshed:
            if reason == "network":
                return JsonResponse({'error': 'Unable to reach Spotify at the moment.'}, status=502)
            return JsonResponse({'error': 'Failed to refresh token'}, status=400)

        return JsonResponse({'message': 'Token refreshed successfully'})
