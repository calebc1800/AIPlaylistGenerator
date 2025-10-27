import hashlib
import logging
from typing import Dict, List, Optional

from django.core.cache import cache
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .services.llm_handler import extract_playlist_attributes, suggest_seed_tracks
from .services.spotify_handler import (
    discover_top_tracks_for_genre,
    get_similar_tracks,
    resolve_seed_tracks,
)

logger = logging.getLogger(__name__)


def _cache_key(user_identifier: str, prompt: str) -> str:
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return f"recommender:{user_identifier}:{digest}"


@require_POST
def generate_playlist(request):
    prompt = request.POST.get("prompt", "").strip()
    debug_steps: List[str] = []

    if not prompt:
        debug_steps.append("Prompt missing; redirecting to dashboard.")
        logger.debug("generate_playlist: %s", debug_steps[-1])
        return redirect("spotify_auth:dashboard")

    debug_steps.append(f"Prompt received: {prompt}")
    logger.debug("generate_playlist: %s", debug_steps[-1])

    access_token = request.session.get("spotify_access_token")
    if not access_token:
        debug_steps.append("Missing Spotify access token; redirecting to login.")
        logger.debug("generate_playlist: %s", debug_steps[-1])
        return redirect("spotify_auth:login")

    user_id = "anonymous"
    if request.user.is_authenticated:
        user_id = str(request.user.pk)
    else:
        user_id = request.session.get("spotify_user_id", user_id)

    cache_key = _cache_key(user_id, prompt)
    cached_payload: Optional[Dict[str, object]] = cache.get(cache_key)

    playlist: List[str] = []
    attributes: Optional[Dict[str, str]] = None
    llm_suggestions: List[Dict[str, str]] = []
    resolved_seed_tracks: List[Dict[str, str]] = []
    seed_track_display: List[str] = []
    similar_tracks: List[str] = []

    if isinstance(cached_payload, dict):
        debug_steps.append("Loaded playlist from cache.")
        logger.debug("generate_playlist: %s", debug_steps[-1])
        playlist = cached_payload.get("playlist", [])
        attributes = cached_payload.get("attributes")
        llm_suggestions = cached_payload.get("llm_suggestions", [])
        resolved_seed_tracks = cached_payload.get("resolved_seed_tracks", [])
        seed_track_display = cached_payload.get("seed_track_display", [])
        similar_tracks = cached_payload.get("similar_tracks", [])
    elif cached_payload:
        debug_steps.append("Loaded legacy cached playlist format.")
        logger.debug("generate_playlist: %s", debug_steps[-1])
        playlist = cached_payload
    else:
        attributes = extract_playlist_attributes(prompt, debug_steps=debug_steps)
        debug_steps.append(f"Attributes after normalization: {attributes}")
        logger.debug("generate_playlist: %s", debug_steps[-1])

        llm_suggestions = suggest_seed_tracks(
            prompt,
            attributes,
            debug_steps=debug_steps,
        )
        resolved_seed_tracks = resolve_seed_tracks(
            llm_suggestions,
            access_token,
            debug_steps=debug_steps,
        )

        if not resolved_seed_tracks:
            debug_steps.append("No LLM seeds resolved; discovering top tracks from Spotify.")
            resolved_seed_tracks = discover_top_tracks_for_genre(
                attributes,
                access_token,
                debug_steps=debug_steps,
            )
            if resolved_seed_tracks:
                llm_suggestions = [
                    {"title": track["name"], "artist": track["artists"]}
                    for track in resolved_seed_tracks
                ]
        seed_track_display = [
            f"{track['name']} - {track['artists']}" for track in resolved_seed_tracks
        ]
        debug_steps.append(f"Resolved seed tracks ({len(seed_track_display)}): {seed_track_display}")
        logger.debug("generate_playlist: %s", debug_steps[-1])

        seed_track_ids = [track["id"] for track in resolved_seed_tracks]
        if not seed_track_ids:
            debug_steps.append("No seed track IDs resolved; skipping Spotify recommendations.")
            logger.debug("generate_playlist: %s", debug_steps[-1])
            playlist = []
        else:
            similar_tracks = get_similar_tracks(
                seed_track_ids,
                access_token,
                attributes,
                debug_steps=debug_steps,
            )
            debug_steps.append(
                f"Similar tracks from Spotify ({len(similar_tracks)}): {similar_tracks}"
            )
            logger.debug("generate_playlist: %s", debug_steps[-1])

            combined = seed_track_display + similar_tracks
            playlist = []
            for song in combined:
                if song not in playlist:
                    playlist.append(song)

            debug_steps.append(f"Final playlist ({len(playlist)} tracks) compiled from seeds and similar tracks.")
            logger.debug("generate_playlist: %s", debug_steps[-1])

        payload = {
            "playlist": playlist,
            "attributes": attributes,
            "llm_suggestions": llm_suggestions,
            "resolved_seed_tracks": resolved_seed_tracks,
            "seed_track_display": seed_track_display,
            "similar_tracks": similar_tracks,
        }
        cache.set(cache_key, payload, timeout=60 * 15)
        debug_steps.append("Playlist cached for 15 minutes.")
        logger.debug("generate_playlist: %s", debug_steps[-1])

    context = {
        "playlist": playlist,
        "prompt": prompt,
        "debug_steps": debug_steps,
        "attributes": attributes,
        "llm_suggestions": llm_suggestions,
        "seed_tracks": seed_track_display,
        "similar_tracks": similar_tracks,
    }
    return render(request, "recommender/playlist_result.html", context)
