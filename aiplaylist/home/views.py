# views.py
import requests
from django.shortcuts import render, redirect
from django.db.models import Q
from django.conf import settings
from .models import Playlist, Song
from django.contrib.auth.models import User


def get_spotify_access_token():
    """
    Get Spotify API access token using Client Credentials flow
    """
    auth_url = "https://accounts.spotify.com/api/token"

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


def fetch_spotify_playlists(query='', limit=10):
    """
    Fetch playlists from Spotify API
    """
    try:
        token = get_spotify_access_token()

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


def import_spotify_playlist(playlist_data):
    """
    Import a Spotify playlist into the database
    """
    try:
        # Get or create the default user (or the playlist owner)
        user, _ = User.objects.get_or_create(
            username='spotify_user',
            defaults={'first_name': 'Spotify', 'last_name': 'Importer'}
        )

        # Create or update the playlist
        playlist, created = Playlist.objects.get_or_create(
            spotify_id=playlist_data['id'],
            defaults={
                'name': playlist_data['name'],
                'description': playlist_data.get('description', ''),
                'creator': user,
                'likes': playlist_data.get('followers', {}).get('total', 0),
                'cover_image': playlist_data['images'][0]['url'] if playlist_data.get('images') else '',
                'spotify_uri': playlist_data['uri'],
            }
        )

        # Fetch and add songs from the playlist
        if created:
            fetch_and_add_spotify_songs(playlist, playlist_data['tracks']['href'])

        return playlist
    except Exception as e:
        print(f"Error importing playlist: {e}")
        return None


def fetch_and_add_spotify_songs(playlist, tracks_url, limit=5):
    """
    Fetch tracks from a Spotify playlist and add them as sample songs
    """
    try:
        token = get_spotify_access_token()

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


def home(request):
    """
    Display playlists from database, or fetch from Spotify if empty
    """
    playlists = Playlist.objects.all().order_by('-likes')

    # If no playlists exist, fetch from Spotify
    if not playlists.exists():
        spotify_playlists = fetch_spotify_playlists('popular', limit=10)
        for spotify_playlist in spotify_playlists:
            import_spotify_playlist(spotify_playlist)

        playlists = Playlist.objects.all().order_by('-likes')

    context = {
        'playlists': playlists,
    }

    return render(request, 'index.html', context)


def search(request):
    """
    Search view for finding playlists
    """
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
            spotify_playlists = fetch_spotify_playlists(query, limit=10)
            for spotify_playlist in spotify_playlists:
                playlist = import_spotify_playlist(spotify_playlist)
                if playlist:
                    playlists = list(playlists) + [playlist]
    else:
        playlists = Playlist.objects.all().order_by('-likes')

    context = {
        'playlists': playlists,
        'query': query,
        'results_count': len(playlists) if isinstance(playlists, list) else playlists.count(),
    }

    return render(request, 'search.html', context)


def login(request):
    return redirect('https://accounts.spotify.com/en/login')

def logout(request):
    pass