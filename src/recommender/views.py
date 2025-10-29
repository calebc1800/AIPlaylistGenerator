"""Django views for generating and saving Spotify playlists."""

import json
import hashlib
import logging
import re
import time
from dataclasses import asdict
from typing import Callable, Dict, List, Optional, Set

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from spotipy import SpotifyException

from .models import SavedPlaylist
from .services.llm_handler import (
    extract_playlist_attributes,
    suggest_seed_tracks,
    suggest_remix_tracks,
)
from .services.spotify_handler import (
    cached_tracks_for_genre,
    ensure_artist_seed,
    discover_top_tracks_for_genre,
    normalize_genre,
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
    """Return a deterministically hashed cache key for a user/prompt pair."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return f"recommender:{user_identifier}:{digest}"


def _make_logger(debug_steps: List[str], errors: List[str]) -> Callable[[str], None]:
    """Capture diagnostic messages and surface potential errors for the UI."""
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
    """Translate cached playlist payloads into template-friendly context."""
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
    debug_enabled = getattr(settings, "RECOMMENDER_DEBUG_VIEW_ENABLED", False)
    default_provider = str(
        getattr(settings, "RECOMMENDER_LLM_DEFAULT_PROVIDER", "openai")
    ).lower()
    context_debug_steps: List[str] = []
    if debug_enabled:
        context_debug_steps = list(payload.get("debug_steps", []))

    track_details = payload.get("track_details")
    if not isinstance(track_details, list):
        track_details = []
        playlist_strings = payload.get("playlist") or []
        track_ids = payload.get("track_ids") or []
        for index, label in enumerate(playlist_strings):
            name_part, _, artist_part = (label or "").partition(" - ")
            track_details.append(
                {
                    "id": track_ids[index] if index < len(track_ids) else "",
                    "name": name_part.strip() or (label or ""),
                    "artists": artist_part.strip() or "",
                    "album_name": "",
                    "album_image_url": "",
                    "duration_ms": 0,
                }
            )

    seed_track_details_raw = payload.get("seed_track_details") or payload.get("resolved_seed_tracks") or []
    seed_track_details: List[Dict[str, object]] = []
    for item in seed_track_details_raw:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        seed_label = (
            normalized.get("seed_source")
            or normalized.get("source")
            or normalized.get("origin")
            or ""
        )
        normalized.setdefault("seed_source", seed_label)
        normalized.setdefault("source", seed_label)
        seed_track_details.append(normalized)

    similar_track_details_raw = payload.get("similar_tracks_debug") or payload.get("similar_tracks") or []
    similar_track_details = [item for item in similar_track_details_raw if isinstance(item, dict)]
    profile_snapshot = payload.get("profile_snapshot")
    if profile_snapshot and not isinstance(profile_snapshot, dict):
        profile_snapshot = None

    return {
        "playlist": payload.get("playlist", []),
        "prompt": payload.get("prompt", ""),
        "debug_steps": context_debug_steps,
        "debug_enabled": debug_enabled,
        "llm_toggle_visible": debug_enabled,
        "llm_provider": payload.get("llm_provider") or default_provider,
        "llm_provider_default": default_provider,
        "errors": list(payload.get("errors", [])),
        "attributes": payload.get("attributes"),
        "llm_suggestions": payload.get("llm_suggestions", []),
        "seed_tracks": payload.get("seed_track_display") or payload.get("seed_tracks", []),
        "similar_tracks": payload.get("similar_tracks_display") or payload.get("similar_tracks", []),
        "seed_track_details": seed_track_details,
        "similar_track_details": similar_track_details,
        "seed_source_counts": payload.get("seed_sources", {}) if isinstance(payload.get("seed_sources"), dict) else {},
        "prompt_artist_ids": payload.get("prompt_artist_ids", []),
        "prompt_artist_candidates": payload.get("prompt_artist_candidates", []),
        "profile_snapshot": profile_snapshot,
        "cache_key": payload.get("cache_key"),
        "user_preferences": preferences,
        "preference_descriptions": preference_descriptions,
        "playlist_tracks": track_details,
        "suggested_playlist_name": payload.get("suggested_playlist_name", ""),
        "playlist_name": payload.get("suggested_playlist_name", ""),
    }


@require_POST
def generate_playlist(request):
    """Generate a playlist based on the submitted prompt and render results."""
    prompt = request.POST.get("prompt", "").strip()
    debug_steps: List[str] = []
    errors: List[str] = []
    log = _make_logger(debug_steps, errors)

    if not prompt:
        log("Prompt missing; redirecting to dashboard.")
        return redirect("dashboard:dashboard")

    debug_enabled = getattr(settings, "RECOMMENDER_DEBUG_VIEW_ENABLED", False)
    default_provider = str(
        getattr(settings, "RECOMMENDER_LLM_DEFAULT_PROVIDER", "openai")
    ).lower()
    provider_choices = {"openai", "ollama"}
    requested_provider = (request.POST.get("llm_provider") or "").strip().lower()
    session_provider = (request.session.get("llm_provider") or "").strip().lower()
    if debug_enabled and requested_provider in provider_choices:
        llm_provider = requested_provider
    elif session_provider in provider_choices:
        llm_provider = session_provider
    else:
        llm_provider = default_provider if default_provider in provider_choices else "openai"
    if not debug_enabled and llm_provider != (default_provider if default_provider in provider_choices else "openai"):
        llm_provider = default_provider if default_provider in provider_choices else "openai"
    request.session["llm_provider"] = llm_provider

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

    profile_cache: Optional[Dict[str, object]] = None
    if user_id:
        profile_cache = cache.get(f"recommender:user-profile:{user_id}")

    profile_snapshot: Optional[Dict[str, object]] = None
    if isinstance(profile_cache, dict):
        track_map = profile_cache.get("tracks")
        if isinstance(track_map, dict):
            top_tracks: List[Dict[str, object]] = []
            for track_id in (profile_cache.get("top_track_ids") or [])[:10]:
                track_entry = track_map.get(track_id)
                if not isinstance(track_entry, dict):
                    continue
                top_tracks.append(
                    {
                        "id": track_id,
                        "name": track_entry.get("name"),
                        "artists": track_entry.get("artists"),
                        "popularity": track_entry.get("popularity"),
                        "year": track_entry.get("year"),
                    }
                )

            genre_debug: List[Dict[str, object]] = []
            genre_buckets = profile_cache.get("genre_buckets")
            if isinstance(genre_buckets, dict):
                for genre, bucket in genre_buckets.items():
                    if not isinstance(bucket, dict):
                        continue
                    bucket_tracks: List[Dict[str, object]] = []
                    for track_id in (bucket.get("track_ids") or [])[:5]:
                        track_entry = track_map.get(track_id)
                        if not isinstance(track_entry, dict):
                            continue
                        bucket_tracks.append(
                            {
                                "id": track_id,
                                "name": track_entry.get("name"),
                                "artists": track_entry.get("artists"),
                                "popularity": track_entry.get("popularity"),
                                "year": track_entry.get("year"),
                            }
                        )
                    avg_popularity = bucket.get("avg_popularity")
                    if isinstance(avg_popularity, (int, float)):
                        avg_popularity = round(float(avg_popularity), 2)
                    avg_year = bucket.get("avg_year")
                    if isinstance(avg_year, (int, float)):
                        avg_year = round(float(avg_year), 1)

                    genre_debug.append(
                        {
                            "genre": genre,
                            "track_count": bucket.get("track_count")
                            or len(bucket.get("track_ids") or []),
                            "avg_popularity": avg_popularity,
                            "avg_year": avg_year,
                            "tracks": bucket_tracks,
                        }
                    )

            artist_debug: List[Dict[str, object]] = []
            artist_counts = profile_cache.get("artist_counts")
            artist_map = profile_cache.get("artists") if isinstance(profile_cache.get("artists"), dict) else {}
            if isinstance(artist_counts, dict):
                top_artists = sorted(
                    ((artist_id, int(count)) for artist_id, count in artist_counts.items()),
                    key=lambda item: item[1],
                    reverse=True,
                )[:10]
                for artist_id, play_count in top_artists:
                    artist_entry = artist_map.get(artist_id, {}) if isinstance(artist_map, dict) else {}
                    artist_debug.append(
                        {
                            "id": artist_id,
                            "name": artist_entry.get("name", artist_id),
                            "genres": artist_entry.get("genres", []),
                            "play_count": play_count,
                        }
                    )

            genre_debug.sort(key=lambda item: str(item.get("genre", "")))

            created_at_value = profile_cache.get("created_at")
            created_at_label: Optional[str] = None
            if isinstance(created_at_value, (int, float)):
                try:
                    created_at_label = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at_value))
                except (ValueError, OSError):
                    created_at_label = None

            profile_snapshot = {
                "source": profile_cache.get("source"),
                "sample_size": profile_cache.get("sample_size"),
                "created_at": created_at_label or created_at_value,
                "created_at_raw": created_at_value,
                "genres": genre_debug,
                "top_tracks": top_tracks,
                "top_artists": artist_debug,
            }

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
            "llm_provider": cached_payload.get("llm_provider") or llm_provider,
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
            "llm_provider": llm_provider,
        }
    else:
        attributes = extract_playlist_attributes(
            prompt,
            debug_steps=debug_steps,
            log_step=log,
            provider=llm_provider,
        )
        log(f"Attributes after normalization: {attributes}")

        normalized_genre = normalize_genre(attributes.get("genre", "pop") or "pop")

        prompt_artist_candidates: List[str] = []
        primary_artist = attributes.get("artist")
        if isinstance(primary_artist, str) and primary_artist.strip():
            prompt_artist_candidates.append(primary_artist.strip())
        additional_artists = attributes.get("artists")
        if isinstance(additional_artists, list):
            for candidate in additional_artists:
                if isinstance(candidate, str) and candidate.strip():
                    normalized = candidate.strip()
                    if normalized.lower() not in {name.lower() for name in prompt_artist_candidates}:
                        prompt_artist_candidates.append(normalized)

        prompt_artist_ids: Set[str] = set()
        resolved_seed_tracks: List[Dict[str, object]] = []
        seed_seen: Set[str] = set()
        seed_sources: Dict[str, int] = {}

        def _append_seed_entry(track: Dict[str, object], source_label: str) -> None:
            if not isinstance(track, dict):
                return
            track_id = track.get("id")
            dedupe_key = track_id or f"{track.get('name')}::{track.get('artists')}"
            if not dedupe_key:
                return
            if dedupe_key in seed_seen:
                return
            seed_seen.add(dedupe_key)
            enriched = dict(track)
            if source_label:
                enriched.setdefault("seed_source", source_label)
                enriched.setdefault("source", source_label)
            resolved_seed_tracks.append(enriched)
            seed_sources[source_label] = seed_sources.get(source_label, 0) + 1

        primary_artist_hint = prompt_artist_candidates[0] if prompt_artist_candidates else ""
        artist_seed_info = None
        if primary_artist_hint:
            artist_seed_info = ensure_artist_seed(
                primary_artist_hint,
                access_token,
                profile_cache=profile_cache,
                debug_steps=debug_steps,
                log_step=log,
            )
            if artist_seed_info:
                artist_id = artist_seed_info.get("artist_id")
                if isinstance(artist_id, str) and artist_id:
                    prompt_artist_ids.add(artist_id)
                artist_tracks = artist_seed_info.get("tracks", [])
                for track in artist_tracks:
                    _append_seed_entry(track, artist_seed_info.get("source", "artist_seed"))
                log(
                    f"Artist seed ensured {len(artist_tracks)} tracks for '{artist_seed_info.get('artist_name', primary_artist_hint)}'."
                )

        if profile_cache and normalized_genre:
            cached_genre_tracks = cached_tracks_for_genre(profile_cache, normalized_genre, limit=5)
            if cached_genre_tracks:
                for track in cached_genre_tracks:
                    _append_seed_entry(track, "user_genre_cache")
                log(
                    f"User cache contributed {len(cached_genre_tracks)} seed tracks for genre '{normalized_genre}'."
                )

        llm_suggestions = suggest_seed_tracks(
            prompt,
            attributes,
            debug_steps=debug_steps,
            log_step=log,
            provider=llm_provider,
        )
        llm_seed_tracks = resolve_seed_tracks(
            llm_suggestions,
            access_token,
            debug_steps=debug_steps,
            log_step=log,
        )
        for track in llm_seed_tracks:
            _append_seed_entry(track, "llm_seed")
        if llm_seed_tracks:
            log(f"LLM resolved {len(llm_seed_tracks)} seed tracks via Spotify search.")

        seed_limit = getattr(settings, "RECOMMENDER_SEED_LIMIT", 5)
        seed_count = len(resolved_seed_tracks)
        if seed_count < seed_limit:
            if seed_count:
                log(
                    "Seed count below threshold but primary sources provided seeds; skipping genre discovery."
                )
            else:
                log("Seed count below threshold; discovering top tracks from Spotify.")
                fallback_tracks = discover_top_tracks_for_genre(
                    attributes,
                    access_token,
                    debug_steps=debug_steps,
                    log_step=log,
                )
                if fallback_tracks:
                    for track in fallback_tracks:
                        _append_seed_entry(track, "genre_discovery")
                    if not llm_seed_tracks:
                        llm_suggestions = [
                            {"title": track["name"], "artist": track["artists"]}
                            for track in fallback_tracks
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

        seed_track_ids = [track["id"] for track in resolved_seed_tracks if track.get("id")]
        similar_tracks: List[Dict[str, str]] = []
        similar_display: List[str] = []
        ordered_tracks: List[Dict[str, str]] = []
        seen_keys: Set[str] = set()

        def _append_track(track_dict: Dict[str, str]) -> None:
            track_id = track_dict.get("id")
            dedupe_key = track_id or f"{track_dict.get('name')}::{track_dict.get('artists')}"
            if dedupe_key in seen_keys:
                return
            seen_keys.add(dedupe_key)
            ordered_tracks.append(
                {
                    "id": track_id,
                    "name": track_dict.get("name", "Unknown"),
                    "artists": track_dict.get("artists", "Unknown"),
                    "album_name": track_dict.get("album_name", ""),
                    "album_image_url": track_dict.get("album_image_url", ""),
                    "duration_ms": track_dict.get("duration_ms", 0),
                }
            )

        for track in resolved_seed_tracks:
            _append_track(track)

        if not seed_track_ids:
            log("No seed track IDs resolved; skipping local recommendation.")
            playlist = [
                f"{track['name']} - {track['artists']}" for track in ordered_tracks
            ]
        else:
            # Merge seeded tracks with context-aware recommendations from Spotify APIs.
            similar_tracks = get_similar_tracks(
                seed_track_ids,
                seed_artist_ids,
                seed_year_avg,
                access_token,
                attributes,
                prompt_keywords,
                debug_steps=debug_steps,
                log_step=log,
                profile_cache=profile_cache,
                focus_artist_ids=prompt_artist_ids,
            )
            log(f"Similarity engine produced {len(similar_tracks)} tracks.")

            similar_display = [
                f"{track['name']} - {track['artists']}" for track in similar_tracks
            ]

            for track in similar_tracks:
                _append_track(track)

            playlist = [
                f"{track['name']} - {track['artists']}" for track in ordered_tracks
            ]

            log(f"Final playlist ({len(playlist)} tracks) compiled from seeds and similar tracks.")

        track_ids: List[str] = [track["id"] for track in ordered_tracks if track.get("id")]

        prompt_label = prompt.strip()
        suggested_playlist_name = prompt_label.title()[:100] if prompt_label else "AI Playlist"

        payload = {
            "playlist": playlist,
            "attributes": attributes,
            "llm_suggestions": llm_suggestions,
            "resolved_seed_tracks": resolved_seed_tracks,
            "seed_sources": seed_sources,
            "prompt_artist_ids": list(prompt_artist_ids),
            "seed_track_display": seed_track_display,
            "similar_tracks_display": similar_display if similar_tracks else [],
            "similar_tracks": similar_tracks,
            "similar_tracks_debug": similar_tracks,
            "seed_track_details": resolved_seed_tracks,
            "track_ids": track_ids,
            "track_details": ordered_tracks,
            "prompt": prompt,
            "suggested_playlist_name": suggested_playlist_name,
            "debug_steps": list(debug_steps),
            "errors": list(errors),
            "cache_key": cache_key,
            "user_preferences": preference_snapshot,
            "preference_descriptions": preference_descriptions,
            "llm_provider": llm_provider,
            "profile_snapshot": profile_snapshot,
            "prompt_artist_candidates": prompt_artist_candidates,
        }
        cache_timeout = getattr(settings, "RECOMMENDER_CACHE_TIMEOUT_SECONDS", 60 * 15)
        cache.set(cache_key, payload, timeout=cache_timeout)
        request.session["recommender_last_cache_key"] = cache_key
        log("Playlist cached for 15 minutes.")

    context = _build_context_from_payload(payload)
    context["cache_key"] = cache_key
    return render(request, "recommender/playlist_result.html", context)


@require_POST
def remix_playlist(request):
    """Regenerate the cached playlist using the current tracks as seeds."""
    cache_key = request.POST.get("cache_key", "").strip()
    if not cache_key:
        cache_key = str(request.session.get("recommender_last_cache_key", "") or "").strip()
    if not cache_key:
        messages.error(request, "Playlist session expired. Please generate a new playlist.")
        return redirect("dashboard:dashboard")

    cached_payload = cache.get(cache_key)
    if not isinstance(cached_payload, dict):
        fallback_key = str(request.session.get("recommender_last_cache_key", "") or "").strip()
        if fallback_key and fallback_key != cache_key:
            cache_key = fallback_key
            cached_payload = cache.get(cache_key)
    if not isinstance(cached_payload, dict):
        messages.error(request, "Playlist session expired. Please generate a new playlist.")
        return redirect("dashboard:dashboard")

    track_details = cached_payload.get("track_details")
    if not isinstance(track_details, list) or not track_details:
        messages.error(request, "No tracks available to remix yet. Generate a playlist first.")
        context = _build_context_from_payload(cached_payload)
        context.setdefault("cache_key", cache_key)
        return render(request, "recommender/playlist_result.html", context, status=409)

    access_token = request.session.get("spotify_access_token")
    if not access_token:
        messages.error(request, "Spotify authentication required.")
        return redirect("spotify_auth:login")

    user_id = "anonymous"
    if request.user.is_authenticated:
        user_id = str(request.user.pk)
    else:
        user_id = request.session.get("spotify_user_id", user_id)

    profile_cache: Optional[Dict[str, object]] = None
    if user_id:
        profile_cache = cache.get(f"recommender:user-profile:{user_id}")

    debug_steps: List[str] = []
    errors: List[str] = []
    log = _make_logger(debug_steps, errors)

    prompt = (cached_payload.get("prompt") or request.POST.get("prompt") or "").strip()

    debug_enabled = getattr(settings, "RECOMMENDER_DEBUG_VIEW_ENABLED", False)
    default_provider = str(
        getattr(settings, "RECOMMENDER_LLM_DEFAULT_PROVIDER", "openai")
    ).lower()
    provider_choices = {"openai", "ollama"}
    requested_provider = (request.POST.get("llm_provider") or "").strip().lower()
    session_provider = (request.session.get("llm_provider") or "").strip().lower()
    if debug_enabled and requested_provider in provider_choices:
        llm_provider = requested_provider
    elif session_provider in provider_choices:
        llm_provider = session_provider
    else:
        llm_provider = default_provider if default_provider in provider_choices else "openai"
    if not debug_enabled and llm_provider != (
        default_provider if default_provider in provider_choices else "openai"
    ):
        llm_provider = default_provider if default_provider in provider_choices else "openai"
    request.session["llm_provider"] = llm_provider

    raw_attributes = cached_payload.get("attributes")
    attributes = raw_attributes if isinstance(raw_attributes, dict) else None
    if not attributes:
        log("Cached attributes missing; extracting again from prompt.")
        attributes = extract_playlist_attributes(
            prompt,
            debug_steps=debug_steps,
            log_step=log,
            provider=llm_provider,
        )

    target_count = sum(1 for entry in track_details if isinstance(entry, dict))
    seed_snapshot = [
        f"{entry.get('name', 'Unknown')} - {entry.get('artists', 'Unknown')}".strip()
        for entry in track_details
        if isinstance(entry, dict)
    ]
    log(f"Remix target track count: {target_count}")

    remix_suggestions = suggest_remix_tracks(
        seed_snapshot,
        attributes,
        prompt=prompt,
        target_count=target_count,
        debug_steps=debug_steps,
        log_step=log,
        provider=llm_provider,
    )

    resolved_seed_tracks = resolve_seed_tracks(
        remix_suggestions,
        access_token,
        debug_steps=debug_steps,
        log_step=log,
        limit=target_count,
    )
    log(f"Resolved {len(resolved_seed_tracks)} remix tracks via Spotify search.")

    ordered_tracks: List[Dict[str, object]] = []
    similar_used: List[Dict[str, object]] = []
    seen_keys: Set[str] = set()

    def _append_track(entry: Dict[str, object], *, force: bool = False) -> bool:
        track_id = str(entry.get("id") or "")
        dedupe_key = track_id or f"{entry.get('name')}::{entry.get('artists')}"
        if not dedupe_key:
            dedupe_key = f"anon::{entry.get('name')}::{entry.get('artists')}"
        if not force and dedupe_key in seen_keys:
            return False
        unique_key = dedupe_key
        if force and unique_key in seen_keys:
            unique_key = f"{dedupe_key}::{len(ordered_tracks)}::force"
        seen_keys.add(unique_key)
        ordered_tracks.append(
            {
                "id": track_id,
                "name": entry.get("name", "Unknown"),
                "artists": entry.get("artists", "Unknown"),
                "album_name": entry.get("album_name", ""),
                "album_image_url": entry.get("album_image_url", ""),
                "duration_ms": int(entry.get("duration_ms") or 0),
                "artist_ids": entry.get("artist_ids", []),
                "year": entry.get("year"),
            }
        )
        return True

    for track in resolved_seed_tracks:
        _append_track(track)

    seed_track_ids = [track.get("id") for track in resolved_seed_tracks if track.get("id")]
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

    if len(ordered_tracks) < target_count and seed_track_ids:
        log("Resolved remix seeds below target; fetching Spotify similarity tracks.")
        similarity_candidates = get_similar_tracks(
            seed_track_ids,
            seed_artist_ids,
            seed_year_avg,
            access_token,
            attributes,
            prompt_keywords,
            debug_steps=debug_steps,
            log_step=log,
            limit=max(target_count - len(ordered_tracks), 5),
            profile_cache=profile_cache,
            focus_artist_ids=set(),
        )
        for candidate in similarity_candidates:
            if len(ordered_tracks) >= target_count:
                break
            if _append_track(candidate):
                similar_used.append(candidate)

    if len(ordered_tracks) < target_count:
        log("Falling back to original playlist tracks to maintain length.")
        for entry in track_details:
            if not isinstance(entry, dict):
                continue
            if len(ordered_tracks) >= target_count:
                break
            fallback_entry = {
                "id": entry.get("id"),
                "name": entry.get("name", "Unknown"),
                "artists": entry.get("artists", "Unknown"),
                "album_name": entry.get("album_name", ""),
                "album_image_url": entry.get("album_image_url", ""),
                "duration_ms": int(entry.get("duration_ms") or 0),
            }
            _append_track(fallback_entry, force=True)

    playlist_strings = [
        f"{entry.get('name', 'Unknown')} - {entry.get('artists', 'Unknown')}".strip()
        for entry in ordered_tracks
    ]
    track_ids = [entry.get("id") for entry in ordered_tracks if entry.get("id")]

    payload = {
        **cached_payload,
        "playlist": playlist_strings,
        "track_details": ordered_tracks,
        "track_ids": track_ids,
        "resolved_seed_tracks": resolved_seed_tracks,
        "llm_suggestions": remix_suggestions,
        "seed_track_display": seed_snapshot,
        "similar_tracks": similar_used,
        "similar_tracks_display": [
            f"{track.get('name', 'Unknown')} - {track.get('artists', 'Unknown')}".strip()
            for track in similar_used
        ],
        "similar_tracks_debug": similar_used,
        "seed_track_details": resolved_seed_tracks,
        "debug_steps": debug_steps,
        "errors": errors,
        "llm_provider": llm_provider,
        "prompt": prompt,
        "cache_key": cache_key,
    }

    cache_timeout = getattr(settings, "RECOMMENDER_CACHE_TIMEOUT_SECONDS", 60 * 15)
    cache.set(cache_key, payload, timeout=cache_timeout)
    request.session["recommender_last_cache_key"] = cache_key
    log("Remixed playlist cached for 15 minutes.")

    context = _build_context_from_payload(payload)
    context["cache_key"] = cache_key

    messages.success(request, "Playlist remixed with fresh recommendations.")
    return render(request, "recommender/playlist_result.html", context)


@require_POST
def update_cached_playlist(request):
    """Mutate cached playlist payloads (e.g., removing tracks) via AJAX."""
    if request.content_type != "application/json":
        return JsonResponse({"error": "Expected JSON payload."}, status=400)

    try:
        request_payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    action = (request_payload.get("action") or "").strip().lower()
    cache_key = (request_payload.get("cache_key") or "").strip()
    if not cache_key:
        cache_key = request.session.get("recommender_last_cache_key", "")
    if not cache_key or action not in {"remove"}:
        return JsonResponse({"error": "Invalid request."}, status=400)

    payload = cache.get(cache_key)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Playlist session expired."}, status=404)

    track_details = payload.get("track_details")
    if not isinstance(track_details, list):
        return JsonResponse({"error": "Playlist does not support editing yet."}, status=409)

    removed = False
    updated_tracks: List[Dict[str, object]] = []
    track_id = (request_payload.get("track_id") or "").strip()
    if track_id:
        for entry in track_details:
            if not removed and entry.get("id") == track_id:
                removed = True
                continue
            updated_tracks.append(entry)
    else:
        updated_tracks = list(track_details)

    if not removed:
        position = request_payload.get("position")
        index = None
        if position is not None:
            try:
                index = int(position)
            except (TypeError, ValueError):
                index = None
        if index is not None and 0 <= index < len(track_details):
            removed = True
            updated_tracks = track_details[:index] + track_details[index + 1 :]

    if not removed:
        return JsonResponse({"error": "Track could not be located."}, status=404)

    payload["track_details"] = list(updated_tracks)
    payload["track_ids"] = [
        entry.get("id") for entry in updated_tracks if entry.get("id")
    ]
    payload["playlist"] = [
        f"{entry.get('name', 'Unknown')} - {entry.get('artists', 'Unknown')}".strip()
        for entry in updated_tracks
    ]

    cache_timeout = getattr(settings, "RECOMMENDER_CACHE_TIMEOUT_SECONDS", 60 * 15)
    cache.set(cache_key, payload, timeout=cache_timeout)

    return JsonResponse(
        {
            "status": "ok",
            "track_count": len(updated_tracks),
            "track_ids": payload["track_ids"],
            "tracks": updated_tracks,
        }
    )


@require_POST
def save_playlist(request):
    """Create a Spotify playlist for the cached tracks and display feedback."""
    cache_key = request.POST.get("cache_key", "").strip()
    playlist_name = (request.POST.get("playlist_name") or "").strip()

    if not cache_key:
        messages.error(request, "Playlist session expired. Please generate a new playlist.")
        return redirect("dashboard:dashboard")

    payload = cache.get(cache_key)
    if not isinstance(payload, dict):
        messages.error(request, "Playlist session expired. Please generate a new playlist.")
        return redirect("dashboard:dashboard")

    context = _build_context_from_payload(payload)
    context.setdefault("cache_key", cache_key)
    context["playlist_name"] = playlist_name

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
        playlist_id = result.get("playlist_id")
        resolved_user_id = result.get("user_id")
        if resolved_user_id:
            request.session["spotify_user_id"] = resolved_user_id
        if playlist_id and resolved_user_id:
            try:
                SavedPlaylist.objects.update_or_create(
                    playlist_id=playlist_id,
                    defaults={"creator_user_id": resolved_user_id},
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("Failed to persist saved playlist %s: %s", playlist_id, exc)
        context["playlist_name"] = resolved_name
        messages.success(request, f"Playlist '{resolved_name}' saved to Spotify.")

    return render(request, "recommender/playlist_result.html", context)
