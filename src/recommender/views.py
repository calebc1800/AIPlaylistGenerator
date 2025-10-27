import hashlib
from typing import List, Optional

from django.core.cache import cache
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .services.llm_handler import extract_playlist_attributes, refine_playlist
from .services.spotify_handler import get_spotify_recommendations


def _cache_key(user_identifier: str, prompt: str) -> str:
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return f"recommender:{user_identifier}:{digest}"


@require_POST
def generate_playlist(request):
    prompt = request.POST.get("prompt", "").strip()
    if not prompt:
        return redirect("spotify_auth:dashboard")

    access_token = request.session.get("spotify_access_token")
    if not access_token:
        return redirect("spotify_auth:login")

    user_id = "anonymous"
    if request.user.is_authenticated:
        user_id = str(request.user.pk)
    else:
        user_id = request.session.get("spotify_user_id", user_id)

    cache_key = _cache_key(user_id, prompt)
    playlist: Optional[List[str]] = cache.get(cache_key)

    if playlist is None:
        attributes = extract_playlist_attributes(prompt)
        seed_tracks = get_spotify_recommendations(attributes, access_token)

        if not seed_tracks:
            playlist = []
        else:
            playlist = refine_playlist(seed_tracks, attributes)

        cache.set(cache_key, playlist, timeout=60 * 15)

    context = {"playlist": playlist, "prompt": prompt}
    return render(request, "recommender/playlist_result.html", context)
