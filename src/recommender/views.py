import hashlib
import logging
import re
import time
from dataclasses import asdict
from typing import Callable, Dict, List, Optional

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from spotipy import SpotifyException

from .services.llm_handler import extract_playlist_attributes, suggest_seed_tracks
from .services.spotify_handler import (
    discover_top_tracks_for_genre,
    get_similar_tracks,
    resolve_seed_tracks,
    create_playlist_with_tracks,
)
from .services.user_preferences import (
    describe_pending_options,
    get_preferences_for_request,
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


def _build_context_from_payload(payload: Dict[str, object]) -> Dict[str, object]:
    if not payload:
        return {}
    preferences = payload.get("user_preferences") or {}
    preference_descriptions = payload.get("preference_descriptions", [])
    if isinstance(preference_descriptions, dict):
        preference_descriptions = [
            {
                "key": key,
                "label": key.replace("_", " ").title(),
                "description": value,
            }
            for key, value in preference_descriptions.items()
        ]
    return {
        "playlist": payload.get("playlist", []),
        "prompt": payload.get("prompt", ""),
        "debug_steps": list(payload.get("debug_steps", [])),
        "errors": list(payload.get("errors", [])),
        "attributes": payload.get("attributes"),
        "llm_suggestions": payload.get("llm_suggestions", []),
        "seed_tracks": payload.get("seed_track_display") or payload.get("seed_tracks", []),
        "similar_tracks": payload.get("similar_tracks_display") or payload.get("similar_tracks", []),
        "cache_key": payload.get("cache_key"),
        "user_preferences": preferences,
        "preference_descriptions": preference_descriptions,
    }


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
    preferences = get_preferences_for_request(request)
    preference_snapshot = asdict(preferences)
    preference_descriptions = describe_pending_options()

    if isinstance(cached_payload, dict):
        updated_payload = {
            **cached_payload,
            "user_preferences": cached_payload.get("user_preferences", preference_snapshot),
            "preference_descriptions": cached_payload.get("preference_descriptions", preference_descriptions),
        }
        context = _build_context_from_payload(updated_payload)
        context.setdefault("cache_key", cache_key)
        return render(request, "recommender/playlist_result.html", context)

    playlist: List[str] = []
    attributes: Optional[Dict[str, str]] = None
    llm_suggestions: List[Dict[str, str]] = []
    resolved_seed_tracks: List[Dict[str, str]] = []
    seed_track_display: List[str] = []
    similar_display: List[str] = []
    payload: Dict[str, object] = {}

    if cached_payload:
        log("Loaded legacy cached playlist format.")
        playlist = cached_payload
        payload = {
            "playlist": playlist,
            "attributes": None,
            "llm_suggestions": [],
            "resolved_seed_tracks": [],
            "prompt": prompt,
            "debug_steps": list(debug_steps),
            "errors": list(errors),
            "seed_track_display": playlist,
            "similar_tracks_display": [],
            "similar_tracks": [],
            "track_ids": [],
            "cache_key": cache_key,
            "user_preferences": preference_snapshot,
            "preference_descriptions": preference_descriptions,
        }
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

        seed_artist_ids = {
            artist_id
            for track in resolved_seed_tracks
            for artist_id in (track.get("artist_ids") or [])
            if artist_id
        }
        seed_years = [track.get("year") for track in resolved_seed_tracks if track.get("year")]
        seed_year_avg = sum(seed_years) / len(seed_years) if seed_years else None
        prompt_keywords = {
            kw
            for kw in re.findall(r"[a-z0-9]+", prompt.lower())
            if len(kw) > 2
        }

        seed_track_ids = [track["id"] for track in resolved_seed_tracks]
        similar_tracks: List[Dict[str, str]] = []
        similar_display: List[str] = []
        if not seed_track_ids:
            log("No seed track IDs resolved; skipping local recommendation.")
            playlist = seed_track_display[:]
        else:
            similar_tracks = get_similar_tracks(
                seed_track_ids,
                seed_artist_ids,
                seed_year_avg,
                access_token,
                attributes,
                prompt_keywords,
                debug_steps=debug_steps,
                log_step=log,
            )
            log(f"Similarity engine produced {len(similar_tracks)} tracks.")

            similar_display = [
                f"{track['name']} - {track['artists']}" for track in similar_tracks
            ]

            combined = seed_track_display + similar_display
            playlist = []
            for song in combined:
                if song not in playlist:
                    playlist.append(song)

            log(f"Final playlist ({len(playlist)} tracks) compiled from seeds and similar tracks.")

        track_ids: List[str] = []
        for track in resolved_seed_tracks:
            if track.get("id") and track["id"] not in track_ids:
                track_ids.append(track["id"])
        for track in similar_tracks:
            if track.get("id") and track["id"] not in track_ids:
                track_ids.append(track["id"])

        payload = {
            "playlist": playlist,
            "attributes": attributes,
            "llm_suggestions": llm_suggestions,
            "resolved_seed_tracks": resolved_seed_tracks,
            "seed_track_display": seed_track_display,
            "similar_tracks_display": similar_display if similar_tracks else [],
            "similar_tracks": similar_tracks,
            "track_ids": track_ids,
            "prompt": prompt,
            "debug_steps": list(debug_steps),
            "errors": list(errors),
            "cache_key": cache_key,
            "user_preferences": preference_snapshot,
            "preference_descriptions": preference_descriptions,
        }
        cache.set(cache_key, payload, timeout=60 * 15)
        log("Playlist cached for 15 minutes.")

    context = _build_context_from_payload(payload)
    return render(request, "recommender/playlist_result.html", context)


@require_POST
def save_playlist(request):
    cache_key = request.POST.get("cache_key", "").strip()
    playlist_name = (request.POST.get("playlist_name") or "").strip()

    if not cache_key:
        messages.error(request, "Playlist session expired. Please generate a new playlist.")
        return redirect("spotify_auth:dashboard")

    payload = cache.get(cache_key)
    if not isinstance(payload, dict):
        messages.error(request, "Playlist session expired. Please generate a new playlist.")
        return redirect("spotify_auth:dashboard")

    context = _build_context_from_payload(payload)
    context.setdefault("cache_key", cache_key)

    if not playlist_name:
        messages.error(request, "Please provide a playlist name.")
        return render(request, "recommender/playlist_result.html", context)

    track_ids = payload.get("track_ids") or []
    if not track_ids:
        messages.error(request, "No tracks available to save.")
        return render(request, "recommender/playlist_result.html", context)

    access_token = request.session.get("spotify_access_token")
    if not access_token:
        messages.error(request, "Spotify authentication required.")
        return redirect("spotify_auth:login")

    try:
        result = create_playlist_with_tracks(
            token=access_token,
            track_ids=track_ids,
            playlist_name=playlist_name,
            prefix=getattr(settings, "RECOMMENDER_PLAYLIST_PREFIX", "TEST "),
            user_id=request.session.get("spotify_user_id"),
            public=getattr(settings, "RECOMMENDER_PLAYLIST_PUBLIC", False),
        )
    except SpotifyException as exc:
        messages.error(request, f"Spotify error: {exc}")
    except (ValueError, RuntimeError) as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        messages.error(request, f"Unexpected error: {exc}")
    else:
        resolved_name = result.get("playlist_name") or playlist_name
        resolved_user_id = result.get("user_id")
        if resolved_user_id:
            request.session["spotify_user_id"] = resolved_user_id
        messages.success(request, f"Playlist '{resolved_name}' saved to Spotify.")

    return render(request, "recommender/playlist_result.html", context)
