import secrets
import requests
from urllib.parse import urlencode

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views import View


class SpotifyLoginView(View):
    """Initiates the Spotify OAuth flow"""
    
    def get(self, request):
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
    """Handles the OAuth callback from Spotify"""
    
    def get(self, request):
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
        
        response = requests.post(token_url, data=data)
        
        if response.status_code != 200:
            return JsonResponse({'error': 'Failed to get access token'}, status=400)
        
        token_data = response.json()
        
        # Store tokens in session (or save to database)
        request.session['spotify_access_token'] = token_data.get('access_token')
        request.session['spotify_refresh_token'] = token_data.get('refresh_token')
        request.session['spotify_expires_in'] = token_data.get('expires_in')
        
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
        response = requests.get('https://api.spotify.com/v1/me', headers=headers)
        
        if response.status_code == 200:
            return response.json()
        return None


class SpotifyRefreshTokenView(View):
    """Refreshes the Spotify access token"""
    
    def post(self, request):
        refresh_token = request.session.get('spotify_refresh_token')
        
        if not refresh_token:
            return JsonResponse({'error': 'No refresh token available'}, status=400)
        
        token_url = 'https://accounts.spotify.com/api/token'
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': settings.SPOTIFY_CLIENT_ID,
            'client_secret': settings.SPOTIFY_CLIENT_SECRET,
        }
        
        response = requests.post(token_url, data=data)
        
        if response.status_code != 200:
            return JsonResponse({'error': 'Failed to refresh token'}, status=400)
        
        token_data = response.json()
        
        # Update access token in session
        request.session['spotify_access_token'] = token_data.get('access_token')
        request.session['spotify_expires_in'] = token_data.get('expires_in')
        
        return JsonResponse({'message': 'Token refreshed successfully'})
