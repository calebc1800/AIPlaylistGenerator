import spotipy
from django.conf import settings
from django.shortcuts import redirect, render
from django.views import View
from explorer.models import Playlist
from explorer.views import SpotifyAPIHelper


class DashboardView(View):
    """Display user's Spotify dashboard"""

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

            # Fetch playlists from database
            playlists = Playlist.objects.all().order_by('-likes')

            # If no playlists exist, fetch from Spotify
            if not playlists.exists():
                spotify_playlists = SpotifyAPIHelper.fetch_playlists('popular', limit=10)
                for spotify_playlist in spotify_playlists:
                    SpotifyAPIHelper.import_playlist(spotify_playlist)

                playlists = Playlist.objects.all().order_by('-likes')

            debug_enabled = getattr(settings, "RECOMMENDER_DEBUG_VIEW_ENABLED", False)
            default_provider = str(
                getattr(settings, "RECOMMENDER_LLM_DEFAULT_PROVIDER", "openai")
            ).lower()
            session_provider = (request.session.get("llm_provider") or "").strip().lower()
            if session_provider not in {"openai", "ollama"}:
                session_provider = default_provider
            if not debug_enabled:
                session_provider = default_provider if default_provider in {"openai", "ollama"} else "openai"
                request.session["llm_provider"] = session_provider
            else:
                request.session["llm_provider"] = session_provider

            context = {
                'username': username,
                'user_id': user_id,
                'email': email,
                'followers': followers,
                'last_song': last_song,
                'profile_url': user_profile.get('external_urls', {}).get('spotify'),
                'playlists': playlists,
                'debug_enabled': debug_enabled,
                'llm_toggle_visible': debug_enabled,
                'llm_provider': session_provider,
                'llm_provider_default': default_provider,
            }
            return render(request, 'dashboard/dashboard.html', context)

        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 401:
                return redirect('spotify_auth:login')
            return render(request, 'dashboard/dashboard.html', {'error': f'Error fetching Spotify data: {str(e)}'})
