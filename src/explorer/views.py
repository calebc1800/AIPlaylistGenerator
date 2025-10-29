import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q, F
from django.conf import settings
from django.views import View
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from .models import Playlist, Song


class SpotifyAPIHelper:
    """Helper class for Spotify API operations"""

    @staticmethod
    def get_access_token():
        """Get Spotify API access token using Client Credentials flow"""
        auth_url = settings.SPOTIFY_AUTH_URL

        data = {
            'grant_type': 'client_credentials',
            'client_id': settings.SPOTIFY_CLIENT_ID,
            'client_secret': settings.SPOTIFY_CLIENT_SECRET,
        }

        response = requests.post(auth_url, data=data)

        if response.status_code == 200:
            return response.json()['access_token']
        else:
            raise Exception("Failed to get Spotify access token")

    @staticmethod
    def fetch_playlists(query='', limit=10):
        """Fetch playlists from Spotify API"""
        try:
            token = SpotifyAPIHelper.get_access_token()

            headers = {
                'Authorization': f'Bearer {token}'
            }

            search_url = "https://api.spotify.com/v1/search"
            params = {
                'q': query if query else 'playlist',
                'type': 'playlist',
                'limit': limit
            }

            response = requests.get(search_url, headers=headers, params=params)

            if response.status_code == 200:
                return response.json()['playlists']['items']
            else:
                return []
        except Exception as e:
            print(f"Error fetching Spotify playlists: {e}")
            return []

    @staticmethod
    def import_playlist(playlist_data):
        """Import a Spotify playlist into the database"""
        try:
            # Get or create the default user
            user, _ = User.objects.get_or_create(
                username='spotify_user',
                defaults={'first_name': 'Spotify', 'last_name': 'Importer'}
            )

            # Extract cover image safely
            cover_image = ''
            if playlist_data.get('images') and len(playlist_data['images']) > 0:
                cover_image = playlist_data['images'][0].get('url', '')

            # Create or update the playlist
            playlist, created = Playlist.objects.get_or_create(
                spotify_id=playlist_data['id'],
                defaults={
                    'name': playlist_data.get('name', 'Untitled Playlist'),
                    'description': playlist_data.get('description', ''),
                    'creator': user,
                    'likes': playlist_data.get('followers', {}).get('total', 0) if playlist_data.get(
                        'followers') else 0,
                    'cover_image': cover_image,
                    'spotify_uri': playlist_data.get('uri', ''),
                }
            )

            # Fetch and add songs from the playlist
            if created:
                SpotifyAPIHelper.fetch_and_add_songs(playlist, playlist_data['tracks']['href'])

            return playlist
        except Exception as e:
            print(f"Error importing playlist: {e}")
            return None

    @staticmethod
    def fetch_and_add_songs(playlist, tracks_url, limit=5):
        """Fetch tracks from a Spotify playlist and add them as sample songs"""
        try:
            token = SpotifyAPIHelper.get_access_token()

            headers = {
                'Authorization': f'Bearer {token}'
            }

            params = {'limit': limit}
            response = requests.get(tracks_url, headers=headers, params=params)

            if response.status_code == 200:
                tracks = response.json()['items']

                for track_item in tracks:
                    track = track_item['track']
                    if track:
                        Song.objects.get_or_create(
                            playlist=playlist,
                            spotify_id=track['id'],
                            defaults={
                                'name': track['name'],
                                'artist': ', '.join([artist['name'] for artist in track['artists']])
                            }
                        )
        except Exception as e:
            print(f"Error fetching songs: {e}")


class ExplorerView(View):
    """Display playlists from database, or fetch from Spotify if empty"""

    def get(self, request):
        playlists = Playlist.objects.all().order_by('-likes')

        # If no playlists exist, fetch from Spotify
        if not playlists.exists():
            spotify_playlists = SpotifyAPIHelper.fetch_playlists('popular', limit=10)
            for spotify_playlist in spotify_playlists:
                SpotifyAPIHelper.import_playlist(spotify_playlist)

            playlists = Playlist.objects.all().order_by('-likes')

        context = {
            'playlists': playlists,
        }

        return render(request, 'explorer/playlist_grid.html', context)


class SearchView(View):
    """Search view for finding playlists"""

    def get(self, request):
        query = request.GET.get('q', '')
        playlists = []

        if query:
            # First, search in local database
            playlists = Playlist.objects.filter(
                Q(name__icontains=query) |
                Q(description__icontains=query) |
                Q(creator__username__icontains=query) |
                Q(sample_songs__name__icontains=query)
            ).distinct().order_by('-likes')

            # If no results found locally, fetch from Spotify
            if not playlists.exists():
                spotify_playlists = SpotifyAPIHelper.fetch_playlists(query, limit=10)
                for spotify_playlist in spotify_playlists:
                    playlist = SpotifyAPIHelper.import_playlist(spotify_playlist)
                    if playlist:
                        playlists = list(playlists) + [playlist]
        else:
            playlists = Playlist.objects.all().order_by('-likes')

        context = {
            'playlists': playlists,
            'query': query,
            'results_count': len(playlists) if isinstance(playlists, list) else playlists.count(),
        }

        return render(request, 'explorer/search.html', context)


class ProfileView(View):
    """Display a user's profile and their playlists"""

    def get(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return render(request, 'explorer/profile.html', {
                'error': 'User not found'
            }, status=404)

        playlists = Playlist.objects.filter(creator=user).order_by('-likes')

        context = {
            'profile_user': user,
            'playlists': playlists,
        }

        return render(request, 'explorer/profile.html', context)


class LogoutView(View):
    """Handle user logout"""

    def get(self, request):
        # Clear session data
        request.session.flush()
        return redirect('home')


# Keep these for backwards compatibility if needed
def playlist_explorer(request):
    """Function-based view wrapper for HomeView"""
    return ExplorerView.as_view()(request)


def search(request):
    """Function-based view wrapper for SearchView"""
    return SearchView.as_view()(request)


def profile(request, user_id):
    """Function-based view wrapper for ProfileView"""
    return ProfileView.as_view()(request, user_id=user_id)


def logout(request):
    """Function-based view wrapper for LogoutView"""
    return LogoutView.as_view()(request)


@require_POST
@csrf_exempt
def like_playlist(request, spotify_id):
    """Handle playlist like action"""
    playlist = get_object_or_404(Playlist, spotify_id=spotify_id)

    # Increment the likes field using F expression for atomic operation
    playlist.likes = F('likes') + 1
    playlist.save()

    # Refresh to get the actual value
    playlist.refresh_from_db()

    # Return JSON response for AJAX requests
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'likes': playlist.likes
        })

    # For non-AJAX requests, redirect back
    return redirect(request.META.get('HTTP_REFERER', 'home'))