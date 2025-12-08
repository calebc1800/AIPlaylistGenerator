"""Dashboard views for the AI Playlist Generator application."""
from __future__ import annotations

from typing import Dict, List, Optional

import json
import spotipy
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views import View
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from dashboard.models import UserFollow
from recommender.models import SavedPlaylist
from recommender.services import artist_recommendation_service
from recommender.services.artist_ai_service import generate_ai_artist_cards
from recommender.services.listening_suggestions import generate_listening_suggestions
from recommender.services.spotify_handler import build_user_profile_seed_snapshot
from recommender.services.stats_service import (
    get_genre_breakdown,
    summarize_generation_stats,
)
from spotify_auth.session import clear_spotify_session, ensure_valid_spotify_session


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
                if track.get("album", {}).get("images") else "",
            }
        )

    top_genres = [
        {"genre": genre, "count": count}
        for genre, count in sorted(
            genre_counter.items(),
            key=lambda item: item[1],
            reverse=True
        )[:5]
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


def _cached_user_top_artists(
    sp: spotipy.Spotify,
    user_id: Optional[str],
    *,
    limit: int = 12,
    ttl: int = 600,
) -> List[Dict[str, object]]:
    """Cache and return Spotify's top artists endpoint for the current user."""
    if not user_id:
        return []
    cache_key = f"dashboard:top-artists:{user_id}:{limit}"
    cached = cache.get(cache_key)
    if isinstance(cached, list):
        return cached
    try:
        response = sp.current_user_top_artists(limit=limit, time_range="medium_term")
    except spotipy.exceptions.SpotifyException:
        return []
    items = response.get("items", []) if isinstance(response, dict) else []
    artists: List[Dict[str, object]] = []
    for artist in items:
        if not isinstance(artist, dict):
            continue
        artist_id = artist.get("id")
        if not artist_id:
            continue
        artists.append(
            {
                "id": artist_id,
                "name": artist.get("name", ""),
                "image": (
                    artist.get("images", [{}])[0].get("url")
                    if artist.get("images")
                    else ""
                ),
                "genres": artist.get("genres", []),
                "popularity": int(artist.get("popularity") or 0),
                "followers": int((artist.get("followers") or {}).get("total") or 0),
                "url": (artist.get("external_urls") or {}).get("spotify", ""),
                "play_count": int(artist.get("popularity") or 0),
            }
        )
    cache.set(cache_key, artists, ttl)
    return artists


def _get_ai_artist_suggestions(
    request,
    user_id: Optional[str],
    sp: Optional[spotipy.Spotify],
    profile_cache: Optional[Dict[str, object]],
    *,
    limit: int = 6,
) -> List[Dict[str, object]]:
    if not user_id:
        return []
    session_key = f"artist_ai_suggestions:{user_id}"
    cached = request.session.get(session_key)
    if isinstance(cached, dict) and isinstance(cached.get("artists"), list):
        return cached["artists"]
    cards = generate_ai_artist_cards(
        user_id,
        sp=sp,
        profile_cache=profile_cache,
        limit=limit,
    )
    request.session[session_key] = {"artists": cards}
    request.session.modified = True
    return cards


class DashboardView(View):
    """Display user's Spotify dashboard"""

    default_tab = "explore"

    def _build_context(self, request, data):
        """Build the context dictionary for the dashboard template."""
        user_profile = data['user_profile']
        playlists = data['playlists']
        generated_stats = data['generated_stats']
        genre_breakdown = data['genre_breakdown']
        favorite_artists = data['favorite_artists']
        ai_artist_suggestions = data['ai_artist_suggestions']
        last_song = data['last_song']

        debug_enabled = getattr(settings, "RECOMMENDER_DEBUG_VIEW_ENABLED", False)
        session_provider = "openai"
        request.session["llm_provider"] = session_provider
        default_provider = "openai"

        allowed_tabs = {"explore", "create", "artists", "stats", "account"}
        requested_tab = (request.GET.get('tab') or "").strip().lower()
        default_tab = requested_tab if requested_tab in allowed_tabs else (
            self.default_tab or "explore")
        default_tab = (default_tab or "explore").lower()
        if request.GET.get('prompt'):
            default_tab = "create"
        if default_tab not in allowed_tabs:
            default_tab = "explore"

        return {
            'username': user_profile.get('display_name') or user_profile.get('id'),
            'user_id': user_profile.get('id'),
            'email': user_profile.get('email'),
            'followers': user_profile.get('followers', {}).get('total', 0),
            'last_song': last_song,
            'profile_url': user_profile.get('external_urls', {}).get('spotify'),
            'playlists': playlists,
            'debug_enabled': debug_enabled,
            'llm_toggle_visible': False,
            'llm_provider': session_provider,
            'llm_provider_default': default_provider,
            'generated_stats': generated_stats,
            'genre_breakdown': genre_breakdown,
            'spotify_highlights': {},
            'favorite_artists': favorite_artists,
            'ai_artist_suggestions': ai_artist_suggestions,
            'default_tab': default_tab,
        }

    def get(self, request):
        """
        Render the dashboard page with user profile and playlist data.

        Args:
            request: The HTTP request object.

        Returns:
            HttpResponse: Rendered dashboard template.
        """
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
            if user_id:
                request.session['spotify_user_id'] = user_id
                request.session['spotify_display_name'] = username

            profile_cache: Optional[Dict[str, object]] = None
            if user_id:
                cache_key = f"recommender:user-profile:{user_id}"
                profile_cache = cache.get(cache_key)
                if not profile_cache:
                    snapshot = build_user_profile_seed_snapshot(sp)
                    if snapshot:
                        ttl = getattr(settings, "RECOMMENDER_USER_PROFILE_CACHE_TTL", 3600)
                        cache.set(cache_key, snapshot, ttl)
                        profile_cache = snapshot

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

            # Fetch saved playlists from database
            playlists = sorted(SavedPlaylist.objects.all(),
                               key=lambda p: p.like_count, reverse=True)

            generation_identifier = _resolve_generation_identifier(request, user_id)
            generated_stats = summarize_generation_stats(generation_identifier)
            genre_breakdown = get_genre_breakdown(generation_identifier)
            favorite_artists = _cached_user_top_artists(sp, user_id, limit=10)
            if not favorite_artists and user_id:
                favorite_artists = artist_recommendation_service.fetch_seed_artists(user_id,
                                                                                    limit=10)
            ai_artist_suggestions = _get_ai_artist_suggestions(
                request,
                user_id,
                sp,
                profile_cache,
                limit=8,
            )

            data = {
                'user_profile': user_profile,
                'playlists': playlists,
                'generated_stats': generated_stats,
                'genre_breakdown': genre_breakdown,
                'favorite_artists': favorite_artists,
                'ai_artist_suggestions': ai_artist_suggestions,
                'last_song': last_song
            }
            context = self._build_context(request, data)
            return render(request, 'dashboard/dashboard.html', context)

        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 401:
                clear_spotify_session(request.session)
                return redirect('spotify_auth:login')
            return render(
                request, 'dashboard/dashboard.html',
                {'error': f'Error fetching Spotify data: {str(e)}'}
            )

class CreateView(DashboardView):
    """Dedicated entry point that loads the create tab by default."""

    default_tab = "create"


class UserStatsAPIView(View):
    """Return combined generation + Spotify stats for the dashboard."""

    def get(self, request):
        """
        Get combined generation and Spotify stats for the dashboard.

        Args:
            request: The HTTP request object.

        Returns:
            JsonResponse: JSON response with stats data.
        """
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


class ListeningSuggestionsAPIView(View):
    """Return listening-based prompt suggestions for the dashboard grid."""

    def get(self, request):
        """
        Get listening-based prompt suggestions for the dashboard.

        Args:
            request: The HTTP request object.

        Returns:
            JsonResponse: JSON response with suggestions data.
        """
        if not ensure_valid_spotify_session(request):
            return JsonResponse({'error': 'Authentication required'}, status=401)
        access_token = request.session.get('spotify_access_token')
        if not access_token:
            return JsonResponse({'error': 'Authentication required'}, status=401)

        user_identifier = _resolve_generation_identifier(request)
        spotify_user_id = request.session.get('spotify_user_id')
        profile_cache = (
            cache.get(f"recommender:user-profile:{spotify_user_id}")
            if spotify_user_id
            else None
        )
        suggestions = generate_listening_suggestions(user_identifier, profile_cache=profile_cache)

        return JsonResponse({'suggestions': suggestions})


class RecommendedArtistsAPIView(View):
    """Serve recommended artists for the dashboard tab."""

    def get(self, request):
        """
        Get recommended artists for the dashboard.

        Args:
            request: The HTTP request object.

        Returns:
            JsonResponse: JSON response with recommended artists data.
        """
        if not ensure_valid_spotify_session(request):
            return JsonResponse({'error': 'Authentication required'}, status=401)
        access_token = request.session.get('spotify_access_token')
        user_id = request.session.get('spotify_user_id')
        if not access_token or not user_id:
            return JsonResponse({'error': 'Authentication required'}, status=401)

        try:
            requested_limit = int(request.GET.get('limit', 8))
        except (TypeError, ValueError):
            requested_limit = 8
        limit = max(1, min(requested_limit, 12))

        sp = spotipy.Spotify(auth=access_token)
        profile_cache = cache.get(f"recommender:user-profile:{user_id}") if user_id else None
        recommended_artists = _get_ai_artist_suggestions(
            request,
            user_id,
            sp,
            profile_cache,
            limit=limit,
        )
        meta_seed_count = sum(
            len(entry.get('seed_artist_ids') or []) for entry in recommended_artists
        )
        payload = {
            'recommended_artists': recommended_artists,
            'meta': {
                'seed_count': meta_seed_count,
                'limit': limit,
            },
        }
        return JsonResponse(payload)

@require_POST
@csrf_exempt
def toggle_follow(request):
    """Toggle follow status for a user"""
    try:
        data = json.loads(request.body)
        following_user_id = data.get('following_user_id')
        following_display_name = data.get('following_display_name')
    except (json.JSONDecodeError, KeyError):
        response_data = {'error': 'Invalid request'}
        status_code = 400
    else:
        # Validate required fields
        if not following_user_id or not following_display_name:
            response_data = {'error': 'Missing required fields'}
            status_code = 400
        else:
            follower_user_id = request.session.get('spotify_user_id')
            follower_display_name = request.session.get('spotify_display_name', follower_user_id)

            if not follower_user_id:
                response_data = {'error': 'Authentication required'}
                status_code = 401
            elif follower_user_id == following_user_id:
                response_data = {'error': 'Cannot follow yourself'}
                status_code = 400
            else:
                # Check if already following
                try:
                    existing_follow = UserFollow.objects.filter(
                        follower_user_id=follower_user_id,
                        following_user_id=following_user_id
                    ).first()

                    if existing_follow:
                        # Unfollow
                        existing_follow.delete()
                        response_data = {
                            'success': True,
                            'following': False,
                            'message': f'Unfollowed {following_display_name}'
                        }
                        status_code = 200
                    else:
                        # Follow
                        UserFollow.objects.create(
                            follower_user_id=follower_user_id,
                            follower_display_name=follower_display_name,
                            following_user_id=following_user_id,
                            following_display_name=following_display_name
                        )
                        response_data = {
                            'success': True,
                            'following': True,
                            'message': f'Now following {following_display_name}'
                        }
                        status_code = 200
                except IntegrityError:
                    # Handle potential database errors
                    response_data = {'error': 'Database integrity error occurred'}
                    status_code = 500

    return JsonResponse(response_data, status=status_code)


def get_following_list(request):
    """Get list of users the current user is following"""
    user_id = request.session.get('spotify_user_id')

    if not user_id:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    # Get all users this user is following
    following = UserFollow.objects.filter(
        follower_user_id=user_id
    ).values(
        'following_user_id',
        'following_display_name',
        'created_at'
    )

    # Get playlist counts for each followed user
    following_list = []
    for follow in following:
        playlist_count = SavedPlaylist.objects.filter(
            creator_user_id=follow['following_user_id']
        ).count()

        following_list.append({
            'user_id': follow['following_user_id'],
            'display_name': follow['following_display_name'],
            'playlist_count': playlist_count,
            'followed_at': follow['created_at'].isoformat()
        })

    return JsonResponse({
        'following': following_list,
        'count': len(following_list)
    })


def get_user_playlists(request, user_id):
    """Get all playlists for a specific user"""
    current_user_id = request.session.get('spotify_user_id')

    if not current_user_id:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    # Get user's playlists
    playlists = SavedPlaylist.objects.filter(
        creator_user_id=user_id
    ).order_by('-created_at')

    # Check if current user is following this user
    is_following = UserFollow.objects.filter(
        follower_user_id=current_user_id,
        following_user_id=user_id
    ).exists()

    playlist_data = []
    for playlist in playlists:
        playlist_data.append({
            'playlist_id': playlist.playlist_id,
            'playlist_name': playlist.playlist_name,
            'description': playlist.description,
            'cover_image': playlist.cover_image,
            'track_count': playlist.track_count,
            'like_count': playlist.like_count,
            'created_at': playlist.created_at.isoformat(),
            'spotify_uri': playlist.spotify_uri
        })

    user_display_name = playlists.first().creator_display_name if playlists.exists() else user_id

    return JsonResponse({
        'user_id': user_id,
        'display_name': user_display_name,
        'is_following': is_following,
        'playlists': playlist_data,
        'count': len(playlist_data)
    })
