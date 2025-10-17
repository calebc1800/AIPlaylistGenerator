import secrets
import requests
import spotipy
from urllib.parse import urlencode
from django.shortcuts import redirect, render
from django.http import JsonResponse
from django.conf import settings
from django.views import View
from spotipy.oauth2 import SpotifyOAuth


class SpotifyLoginView(View):
    """Initiates the Spotify OAuth flow"""
    
    def get(self, request):
        # Generate a random state for CSRF protection
        state = secrets.token_urlsafe(16)
        request.session['spotify_auth_state'] = state
        
        # Spotify authorization parameters
        params = {
            'client_id': settings.SPOTIFY_CLIENT_ID,
            'response_type': 'code',
            'redirect_uri': settings.SPOTIFY_REDIRECT_URI,
            'state': state,
            'scope': 'user-read-email user-read-private user-read-recently-played',  # Add scopes as needed
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
        
        return redirect('spotify_auth:dashboard')
    
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

        # Add this new view class at the end of the file
class SpotifyDashboardView(View):
    """Display user's Spotify data"""
    
    def get(self, request):
        access_token = request.session.get('spotify_access_token')
        if not access_token:
            return redirect('spotify_auth:login')
        
        sp = spotipy.Spotify(auth=access_token)
        try:
            user_profile = sp.current_user()
            recently_played = sp.current_user_recently_played(limit=1)
            
            username = user_profile.get('display_name') or user_profile.get('id')
            user_id = user_profile.get('id')
            email = user_profile.get('email')
            followers = user_profile.get('followers', {}).get('total', 0)
            
            last_song = None
            if recently_played and recently_played.get('items'):
                track = recently_played['items'][0]['track']
                last_song = {
                    'name': track['name'],
                    'artist': ', '.join([artist['name'] for artist in track['artists']]),
                    'album': track['album']['name'],
                    'image': track['album']['images'][0]['url'] if track['album']['images'] else None,
                    'played_at': recently_played['items'][0]['played_at']
                }
            
            context = {
                'username': username,
                'user_id': user_id,
                'email': email,
                'followers': followers,
                'last_song': last_song,
                'profile_url': user_profile.get('external_urls', {}).get('spotify'),
            }
            return render(request, 'spotify_auth/dashboard.html', context)
        
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 401:
                return redirect('spotify_auth:login')
            return render(request, 'spotify_auth/dashboard.html', {'error': f'Error fetching Spotify data: {str(e)}'})