import hashlib
import logging
import time
from typing import Callable, Dict, List, Optional

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


def _make_logger(debug_steps: List[str], errors: List[str]) -> Callable[[str], None]:
    start = time.perf_counter()

    def _log(message: str) -> None:
        elapsed = time.perf_counter() - start
        formatted = f"[{elapsed:0.2f}s] {message}"
        debug_steps.append(formatted)
        lower_msg = message.lower()
        if any(keyword in lower_msg for keyword in ("error", "failed", "missing", "unavailable")):
            errors.append(message)
        logger.debug("generate_playlist: %s", formatted)

    return _log


@require_POST
def generate_playlist(request):
    prompt = request.POST.get("prompt", "").strip()
    debug_steps: List[str] = []
    errors: List[str] = []
    log = _make_logger(debug_steps, errors)

    if not prompt:
        log("Prompt missing; redirecting to dashboard.")
        return redirect("spotify_auth:dashboard")

    log(f"Prompt received: {prompt}")

    access_token = request.session.get("spotify_access_token")
    if not access_token:
        log("Missing Spotify access token; redirecting to login.")
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
        log("Loaded playlist from cache.")
        playlist = cached_payload.get("playlist", [])
        attributes = cached_payload.get("attributes")
        llm_suggestions = cached_payload.get("llm_suggestions", [])
        resolved_seed_tracks = cached_payload.get("resolved_seed_tracks", [])
        seed_track_display = cached_payload.get("seed_track_display", [])
        similar_tracks = cached_payload.get("similar_tracks", [])
    elif cached_payload:
        log("Loaded legacy cached playlist format.")
        playlist = cached_payload
    else:
        attributes = extract_playlist_attributes(
            prompt,
            debug_steps=debug_steps,
            log_step=log,
        )
        log(f"Attributes after normalization: {attributes}")

        llm_suggestions = suggest_seed_tracks(
            prompt,
            attributes,
            debug_steps=debug_steps,
            log_step=log,
        )
        resolved_seed_tracks = resolve_seed_tracks(
            llm_suggestions,
            access_token,
            debug_steps=debug_steps,
            log_step=log,
        )

        if not resolved_seed_tracks:
            log("No LLM seeds resolved; discovering top tracks from Spotify.")
            resolved_seed_tracks = discover_top_tracks_for_genre(
                attributes,
                access_token,
                debug_steps=debug_steps,
                log_step=log,
            )
            if resolved_seed_tracks:
                llm_suggestions = [
                    {"title": track["name"], "artist": track["artists"]}
                    for track in resolved_seed_tracks
                ]

        seed_track_display = [
            f"{track['name']} - {track['artists']}" for track in resolved_seed_tracks
        ]
        log(f"Resolved seed tracks ({len(seed_track_display)}): {seed_track_display}")

        seed_track_ids = [track["id"] for track in resolved_seed_tracks]
        if not seed_track_ids:
            log("No seed track IDs resolved; skipping local recommendation.")
            playlist = seed_track_display[:]
        else:
            similar_tracks = get_similar_tracks(
                seed_track_ids,
                access_token,
                attributes,
                debug_steps=debug_steps,
                log_step=log,
            )
            log(
                f"Similarity engine produced {len(similar_tracks)} tracks."
            )

            combined = seed_track_display + similar_tracks
            playlist = []
            for song in combined:
                if song not in playlist:
                    playlist.append(song)

            log(f"Final playlist ({len(playlist)} tracks) compiled from seeds and similar tracks.")

        payload = {
            "playlist": playlist,
            "attributes": attributes,
            "llm_suggestions": llm_suggestions,
            "resolved_seed_tracks": resolved_seed_tracks,
            "seed_track_display": seed_track_display,
            "similar_tracks": similar_tracks,
        }
        cache.set(cache_key, payload, timeout=60 * 15)
        log("Playlist cached for 15 minutes.")

    context = {
        "playlist": playlist,
        "prompt": prompt,
        "debug_steps": debug_steps,
        "errors": errors,
        "attributes": attributes,
        "llm_suggestions": llm_suggestions,
        "seed_tracks": seed_track_display,
        "similar_tracks": similar_tracks,
    }
    return render(request, "recommender/playlist_result.html", context)
