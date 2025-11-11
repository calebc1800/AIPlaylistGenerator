from __future__ import annotations

import spotipy
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views import View
from explorer.models import Playlist
from explorer.views import SpotifyAPIHelper
from spotify_auth.session import clear_spotify_session, ensure_valid_spotify_session
from recommender.services.spotify_handler import build_user_profile_seed_snapshot
from recommender.services.stats_service import (
    get_genre_breakdown,
    summarize_generation_stats,
)


def _resolve_generation_identifier(request, spotify_user_id: str | None = None) -> str:
    """Resolves the generation identifier using the User's Spotify ID

    Args:
        request (django request): http session information request
        spotify_user_id (str | None, optional): User's Spotify ID. Defaults to None.

    Returns:
        str: User's Spotify ID
    """
    if request.user.is_authenticated:
        return str(request.user.pk)
    if spotify_user_id:
        return spotify_user_id
    return str(request.session.get("spotify_user_id") or "anonymous")


def _ensure_session_key(request) -> str:
    """Checks for session key

    Args:
        request (django request): http session information request

    Returns:
        str: Session Key
    """
    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key or ""
    return session_key


def _fetch_spotify_highlights(request, sp: spotipy.Spotify) -> dict:
    """Uses Spotify API to get the current user's top artists and songs

    Args:
        request (django request): http session information request
        sp (spotipy.Spotify): Spotify API

    Returns:
        dict: Top genres, artists and tracks
    """
    cache_key = f"dashboard:spotify-highlights:{_ensure_session_key(request)}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    highlights = {
        "top_genres": [],
        "top_artists": [],
        "top_tracks": [],
    }

    try:
        top_artists_resp = sp.current_user_top_artists(limit=5, time_range="medium_term")
        top_tracks_resp = sp.current_user_top_tracks(limit=5, time_range="medium_term")
    except spotipy.exceptions.SpotifyException:
        return highlights

    top_artists = []
    genre_counter = {}
    for artist in top_artists_resp.get("items", []) or []:
        genres = [genre.title() for genre in artist.get("genres", [])[:3]]
        top_artists.append(
            {
                "name": artist.get("name"),
                "genres": genres,
                "image": artist.get("images", [{}])[0].get("url") if artist.get("images") else "",
            }
        )
        for genre in genres:
            genre_counter[genre] = genre_counter.get(genre, 0) + 1

    top_tracks = []
    for track in top_tracks_resp.get("items", []) or []:
        top_tracks.append(
            {
                "name": track.get("name"),
                "artists": ", ".join(artist.get("name") for artist in track.get("artists", [])),
                "album": track.get("album", {}).get("name"),
                "image": track.get("album", {}).get("images", [{}])[0].get("url")
                if track.get("album", {}).get("images")
                else "",
            }
        )

    top_genres = [
        {"genre": genre, "count": count}
        for genre, count in sorted(genre_counter.items(),
                                   key=lambda item: item[1], 
                                   reverse=True)[:5]
    ]

    highlights.update(
        {
            "top_genres": top_genres,
            "top_artists": top_artists,
            "top_tracks": top_tracks,
        }
    )
    cache.set(cache_key, highlights, 300)
    return highlights


class DashboardView(View):
    """Display user's Spotify dashboard"""

    def get(self, request):
        if not ensure_valid_spotify_session(request):
            return redirect('spotify_auth:login')
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
            if user_id:
                request.session['spotify_user_id'] = user_id

            if user_id:
                cache_key = f"recommender:user-profile:{user_id}"
                if not cache.get(cache_key):
                    snapshot = build_user_profile_seed_snapshot(sp)
                    if snapshot:
                        ttl = getattr(settings, "RECOMMENDER_USER_PROFILE_CACHE_TTL", 3600)
                        cache.set(cache_key, snapshot, ttl)

            last_song = None
            if recently_played and recently_played.get('items'):
                track = recently_played['items'][0]['track']
                last_song = {
                    'name': track['name'],
                    'artist': ', '.join([artist['name'] for artist in track['artists']]),
                    'album': track['album']['name'],
                    'image': (
                        track['album']['images'][0]['url'] 
                        if track['album']['images'] 
                        else None
                        ),
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
                session_provider = (
                    default_provider 
                    if default_provider in {"openai", "ollama"} 
                    else "openai"
                )
                request.session["llm_provider"] = session_provider
            else:
                request.session["llm_provider"] = session_provider

            generation_identifier = _resolve_generation_identifier(request, user_id)
            generated_stats = summarize_generation_stats(generation_identifier)
            genre_breakdown = get_genre_breakdown(generation_identifier)

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
                'generated_stats': generated_stats,
                'genre_breakdown': genre_breakdown,
                'spotify_highlights': {},
            }
            return render(request, 'dashboard/dashboard.html', context)

        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 401:
                clear_spotify_session(request.session)
                return redirect('spotify_auth:login')
            return render(request, 'dashboard/dashboard.html', {'error': f'Error fetching Spotify data: {str(e)}'})


class UserStatsAPIView(View):
    """Return combined generation + Spotify stats for the dashboard."""

    def get(self, request):
        if not ensure_valid_spotify_session(request):
            return JsonResponse({'error': 'Authentication required'}, status=401)
        access_token = request.session.get('spotify_access_token')
        if not access_token:
            return JsonResponse({'error': 'Authentication required'}, status=401)

        user_identifier = _resolve_generation_identifier(request)
        generated = summarize_generation_stats(user_identifier)
        genre_breakdown = get_genre_breakdown(user_identifier)

        try:
            sp = spotipy.Spotify(auth=access_token)
            spotify_highlights = _fetch_spotify_highlights(request, sp)
        except spotipy.exceptions.SpotifyException:
            spotify_highlights = {'top_genres': [], 'top_artists': [], 'top_tracks': []}

        payload = {
            'generated': generated,
            'genre_breakdown': genre_breakdown,
            'spotify': spotify_highlights,
        }
        return JsonResponse(payload)
