"""Lightweight adapters around the OpenAI LLMs used for playlist generation."""

# The routines in this module intentionally trade compact code for clarity when
# orchestrating the many OpenAI interactions required for playlist generation.
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
# pylint: disable=too-many-branches,too-many-statements

import json
import logging
import os
import re
from threading import local
from typing import Any, Callable, Dict, List, Optional, Set

from openai import OpenAI, OpenAIError

logger = logging.getLogger(__name__)

DEFAULT_ATTRIBUTES = {"mood": "chill", "genre": "pop", "energy": "medium"}
try:
    from django.conf import settings as django_settings  # type: ignore
except ImportError:  # pragma: no cover - optional dependency in some contexts
    DJANGO_SETTINGS = None
else:
    DJANGO_SETTINGS = django_settings

_GENRE_FALLBACKS = {
    "pop": [
        {"title": "Blinding Lights", "artist": "The Weeknd"},
        {"title": "Levitating", "artist": "Dua Lipa"},
        {"title": "Good 4 U", "artist": "Olivia Rodrigo"},
        {"title": "Watermelon Sugar", "artist": "Harry Styles"},
        {"title": "Don't Start Now", "artist": "Dua Lipa"},
    ],
    "rock": [
        {"title": "Mr. Brightside", "artist": "The Killers"},
        {"title": "Seven Nation Army", "artist": "The White Stripes"},
        {"title": "Everlong", "artist": "Foo Fighters"},
        {"title": "Use Somebody", "artist": "Kings of Leon"},
        {"title": "Sweet Child O' Mine", "artist": "Guns N' Roses"},
    ],
    "hip hop": [
        {"title": "SICKO MODE", "artist": "Travis Scott"},
        {"title": "Lose Yourself", "artist": "Eminem"},
        {"title": "HUMBLE.", "artist": "Kendrick Lamar"},
        {"title": "God's Plan", "artist": "Drake"},
        {"title": "POWER", "artist": "Kanye West"},
    ],
    "electronic": [
        {"title": "Midnight City", "artist": "M83"},
        {"title": "Strobe", "artist": "deadmau5"},
        {"title": "Titanium", "artist": "David Guetta ft. Sia"},
        {"title": "Wake Me Up", "artist": "Avicii"},
        {"title": "Animals", "artist": "Martin Garrix"},
    ],
    "jazz": [
        {"title": "So What", "artist": "Miles Davis"},
        {"title": "Take Five", "artist": "The Dave Brubeck Quartet"},
        {"title": "My Favorite Things", "artist": "John Coltrane"},
        {"title": "Blue in Green", "artist": "Bill Evans"},
        {"title": "Feeling Good", "artist": "Nina Simone"},
    ],
    "classical": [
        {"title": "Clair de Lune", "artist": "Claude Debussy"},
        {"title": "Nocturne Op.9 No.2", "artist": "Frédéric Chopin"},
        {"title": "Canon in D", "artist": "Johann Pachelbel"},
        {"title": "Spring (The Four Seasons)", "artist": "Antonio Vivaldi"},
        {"title": "Moonlight Sonata", "artist": "Ludwig van Beethoven"},
    ],
}
_DEFAULT_FALLBACKS = [
    {"title": "Dreams", "artist": "Fleetwood Mac"},
    {"title": "Africa", "artist": "Toto"},
    {"title": "Uptown Funk", "artist": "Mark Ronson ft. Bruno Mars"},
    {"title": "Stayin' Alive", "artist": "Bee Gees"},
    {"title": "September", "artist": "Earth, Wind & Fire"},
]

_CLIENT_STATE: Dict[str, Optional[OpenAI]] = {"client": None}
_JSON_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_THREAD_STATE = local()


def _default_usage_bucket() -> Dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def reset_llm_usage_tracker() -> None:
    """Reset the accumulated token counters for the current thread."""
    _THREAD_STATE.llm_usage = _default_usage_bucket()


def _usage_bucket() -> Dict[str, int]:
    usage = getattr(_THREAD_STATE, "llm_usage", None)
    if usage is None:
        usage = _default_usage_bucket()
        _THREAD_STATE.llm_usage = usage
    return usage


def _record_llm_usage(
    *,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> None:
    usage = _usage_bucket()
    if prompt_tokens:
        usage["prompt_tokens"] += max(int(prompt_tokens), 0)
    if completion_tokens:
        usage["completion_tokens"] += max(int(completion_tokens), 0)
    if total_tokens:
        usage["total_tokens"] += max(int(total_tokens), 0)


def get_llm_usage_snapshot() -> Dict[str, int]:
    """Return the current token counters for the active thread."""
    usage = getattr(_THREAD_STATE, "llm_usage", None)
    if not usage:
        return _default_usage_bucket()
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }


def _get_setting(name: str, default=None):
    if DJANGO_SETTINGS is not None and hasattr(DJANGO_SETTINGS, name):
        return getattr(DJANGO_SETTINGS, name)
    return os.getenv(name, default)


def _get_openai_client() -> Optional[OpenAI]:
    """Lazily initialize the shared OpenAI client."""
    if _CLIENT_STATE["client"] is not None:
        return _CLIENT_STATE["client"]

    api_key = _get_setting("OPENAI_API_KEY")
    if not api_key:
        logger.warning(
            "OpenAI API key is not configured. Set OPENAI_API_KEY to enable LLM features."
        )
        return None

    client_kwargs: Dict[str, str] = {"api_key": api_key}
    base_url = _get_setting("OPENAI_API_BASE")
    if base_url:
        client_kwargs["base_url"] = base_url
    organization = _get_setting("OPENAI_ORGANIZATION")
    if organization:
        client_kwargs["organization"] = organization

    try:
        _CLIENT_STATE["client"] = OpenAI(**client_kwargs)
    except (OpenAIError, ValueError) as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to initialize OpenAI client: %s", exc)
        return None

    return _CLIENT_STATE["client"]



def _log(
    debug_steps: Optional[List[str]],
    log_step: Optional[Callable[[str], None]],
    message: str,
) -> None:
    """Collect debug output centrally so callers can display progress."""
    if log_step:
        log_step(message)
    elif debug_steps is not None:
        debug_steps.append(message)


def _json_candidates(raw: str) -> List[str]:
    """Yield plausible JSON substrings from a potentially messy LLM response."""
    if not raw:
        return []
    candidates: List[str] = []
    for match in _JSON_CODE_FENCE_RE.findall(raw):
        cleaned = match.strip()
        if cleaned:
            candidates.append(cleaned)

    stripped = raw.strip()
    if stripped:
        candidates.append(stripped)

    return candidates


def _parse_json_response(raw: str) -> Optional[Any]:
    """Attempt to parse JSON content from LLM output that may include extra text."""
    if not raw:
        return None

    decoder = json.JSONDecoder()
    for candidate in _json_candidates(raw):
        # Try the entire candidate first.
        try:
            return decoder.raw_decode(candidate)[0]
        except json.JSONDecodeError:
            pass

        # Look for the first JSON object/array within the candidate.
        for idx, ch in enumerate(candidate):
            if ch in "{[":
                try:
                    return decoder.raw_decode(candidate[idx:])[0]
                except json.JSONDecodeError:
                    continue

    return None


def query_openai(
    prompt: str,
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
) -> str:
    """Send a prompt to the configured OpenAI model and return the raw response text."""
    client = _get_openai_client()
    if client is None:
        return ""

    resolved_model = model or _get_setting("RECOMMENDER_OPENAI_MODEL", "gpt-4o-mini")
    resolved_temperature = (
        temperature
        if temperature is not None
        else _get_setting("RECOMMENDER_OPENAI_TEMPERATURE", 0.7)
    )
    resolved_max_tokens = (
        max_output_tokens
        if max_output_tokens is not None
        else _get_setting("RECOMMENDER_OPENAI_MAX_TOKENS", 512)
    )

    request_kwargs: Dict[str, object] = {
        "model": resolved_model,
        "input": prompt,
    }
    if resolved_temperature is not None:
        try:
            request_kwargs["temperature"] = float(resolved_temperature)
        except (TypeError, ValueError):
            request_kwargs["temperature"] = 0.7
    if resolved_max_tokens:
        try:
            request_kwargs["max_output_tokens"] = int(resolved_max_tokens)
        except (TypeError, ValueError):
            request_kwargs["max_output_tokens"] = 512

    try:
        response = client.responses.create(**request_kwargs)
    except (OpenAIError, ValueError, TypeError) as exc:  # pragma: no cover - defensive logging
        logger.error("OpenAI request failed: %s", exc)
        return ""

    _capture_openai_usage(response)

    output_text = getattr(response, "output_text", "")
    if output_text:
        return output_text.strip()

    # Fallback parsing for unexpected response structures.
    try:
        segments: List[str] = []
        for item in getattr(response, "output", []):
            for content in getattr(item, "content", []):
                text_value = getattr(content, "text", None)
                value = getattr(text_value, "value", None)
                if value:
                    segments.append(str(value))
        return "".join(segments).strip()
    except (AttributeError, TypeError):  # pragma: no cover - fallback only
        return ""


def _capture_openai_usage(response: object) -> None:
    """Best-effort extraction of token usage metadata from OpenAI responses."""
    usage_obj = getattr(response, "usage", None)
    if usage_obj is None and isinstance(response, dict):
        usage_obj = response.get("usage")

    if usage_obj is None:
        for item in getattr(response, "output", []) or []:
            candidate = getattr(item, "usage", None)
            if candidate is None and isinstance(item, dict):
                candidate = item.get("usage")
            if candidate is not None:
                usage_obj = candidate
                break

    if usage_obj is None:
        return

    prompt_tokens = _extract_usage_value(usage_obj, "prompt_tokens", "input_tokens")
    completion_tokens = _extract_usage_value(usage_obj, "completion_tokens", "output_tokens")
    total_tokens = _extract_usage_value(usage_obj, "total_tokens")
    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    _record_llm_usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _extract_usage_value(source: object, *keys: str) -> Optional[int]:
    for key in keys:
        value = None
        if isinstance(source, dict):
            value = source.get(key)
        if value is None:
            try:
                value = getattr(source, key)
            except AttributeError:
                value = None
        if value is None and hasattr(source, "get"):
            try:
                value = source.get(key)
            except (AttributeError, TypeError):  # pragma: no cover - defensive fallback
                value = None
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def dispatch_llm_query(
    prompt: str,
    *,
    provider: Optional[str] = None,
    **kwargs: object,
) -> str:
    """Route LLM prompts to OpenAI (provider retained for backward compatibility)."""
    _ = provider  # provider toggles are deprecated; OpenAI is always used.
    model = kwargs.get("model")
    temperature = kwargs.get("temperature")
    max_tokens = kwargs.get("max_output_tokens")
    return query_openai(
        prompt,
        model=model if isinstance(model, str) else None,
        temperature=temperature if isinstance(temperature, (int, float)) else None,
        max_output_tokens=max_tokens if isinstance(max_tokens, int) else None,
    )


def extract_playlist_attributes(
    prompt: str,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    provider: Optional[str] = None,
) -> Dict[str, object]:
    """Pull mood, genre, and energy descriptors from a free-form user prompt."""
    query = (
        "Extract the mood, genre, energy level, and any explicitly referenced primary artists "
        "or bands from this user playlist request. Respond with JSON containing the keys "
        "`mood`, `genre`, and `energy`, plus optional `artist` (string) and `artists` "
        "(array of strings) when specific performers are mentioned. "
        "If no artist is present, set those fields to null or an empty list. "
        f"Request: {prompt}"
    )
    _log(debug_steps, log_step, f"LLM prompt (attribute extraction): {query}")
    response = dispatch_llm_query(query, provider=provider)
    snippet = response if len(response) <= 300 else response[:297] + "..."
    _log(debug_steps, log_step, f"LLM raw response (attributes): {snippet}")
    if not response:
        _log(
            debug_steps,
            log_step,
            "LLM attribute extraction failed; using default attributes.",
        )
        return DEFAULT_ATTRIBUTES.copy()

    parsed = _parse_json_response(response)
    if isinstance(parsed, dict):
        lowered = {str(key).lower(): value for key, value in parsed.items()}
        attributes = {
            "mood": lowered.get("mood", DEFAULT_ATTRIBUTES["mood"]),
            "genre": lowered.get("genre")
            or lowered.get("music_genre", DEFAULT_ATTRIBUTES["genre"]),
            "energy": lowered.get("energy")
            or lowered.get("energy_level")
            or lowered.get("energylevel")
            or DEFAULT_ATTRIBUTES["energy"],
        }

        artist_hint = lowered.get("artist") or lowered.get("primary_artist") or ""
        if isinstance(artist_hint, list):
            artist_hint = artist_hint[0] if artist_hint else ""
        artist_hint = str(artist_hint).strip() if artist_hint else ""

        artists_field = lowered.get("artists") or lowered.get("artist_list") or []
        if isinstance(artists_field, str) and artists_field.strip():
            artists_list = [artists_field.strip()]
        elif isinstance(artists_field, list):
            artists_list = [
                str(item).strip()
                for item in artists_field
                if isinstance(item, (str, int)) and str(item).strip()
            ]
        else:
            artists_list = []
        if artist_hint:
            lower_names = {name.lower() for name in artists_list}
            if artist_hint.lower() not in lower_names:
                artists_list.insert(0, artist_hint)

        attributes = {key: (value or DEFAULT_ATTRIBUTES[key]) for key, value in attributes.items()}
        attributes["artist"] = artist_hint
        attributes["artists"] = artists_list
        _log(debug_steps, log_step, f"LLM parsed attributes: {attributes}")
        return attributes

    _log(
        debug_steps,
        log_step,
        f"Failed to parse LLM attribute response; using defaults. Response snippet: {snippet}",
    )
    fallback = DEFAULT_ATTRIBUTES.copy()
    fallback.setdefault("artist", "")
    fallback.setdefault("artists", [])
    return fallback


def suggest_seed_tracks(
    prompt: str,
    attributes: Dict[str, str],
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    max_suggestions: int = 5,
    provider: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Use the LLM to propose seed tracks as title/artist pairs."""
    suggestion_cap = max(1, int(max_suggestions or 5))
    query = (
        "You are selecting seed songs for a Spotify playlist.\n"
        f"Playlist request: \"{prompt}\"\n"
        f"Extracted attributes: {attributes}\n"
        f"Return a JSON array with at most {suggestion_cap} objects, each containing the keys "
        "\"title\" and \"artist\". Choose well-known songs that fit the mood/genre/"
        "energy and are likely available on Spotify."
    )
    _log(debug_steps, log_step, f"LLM prompt (seed suggestions): {query}")
    response = dispatch_llm_query(query, provider=provider)
    snippet = response if len(response) <= 400 else response[:397] + "..."
    _log(debug_steps, log_step, f"LLM raw response (seed suggestions): {snippet}")
    suggestions: List[Dict[str, str]] = []

    def _add_suggestion(title: str, artist: str):
        title = (title or "").strip()
        artist = (artist or "").strip()
        if not title:
            return
        suggestions.append({"title": title, "artist": artist})

    if response:
        parsed = _parse_json_response(response)
        if isinstance(parsed, dict):
            if "tracks" in parsed:
                parsed = parsed["tracks"]
            elif "playlist" in parsed:
                parsed = parsed["playlist"]
            elif "songs" in parsed:
                parsed = parsed["songs"]
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    title = item.get("title") or item.get("song") or item.get("name")
                    artist = item.get("artist") or item.get("artists") or item.get("singer")
                    if isinstance(artist, list):
                        artist = ", ".join(str(part) for part in artist)
                    if title:
                        _add_suggestion(str(title), str(artist or ""))
                elif isinstance(item, str):
                    if " - " in item:
                        title, artist = item.split(" - ", 1)
                    else:
                        title, artist = item, ""
                    _add_suggestion(title, artist)
        else:
            lines = [line.strip() for line in response.splitlines() if line.strip()]
            for line in lines:
                if " - " in line:
                    title, artist = line.split(" - ", 1)
                else:
                    continue
                _add_suggestion(title, artist)

    if suggestions:
        _log(
            debug_steps,
            log_step,
            f"LLM parsed seed suggestions: {suggestions[:max_suggestions]}",
        )
    else:
        _log(
            debug_steps,
            log_step,
            "LLM seed suggestions unavailable; will rely on Spotify fallback.",
        )
        genre_key = (attributes.get("genre") or "").lower()
        canonical = genre_key.replace("-", " ").strip()
        fallbacks = _GENRE_FALLBACKS.get(canonical) or _DEFAULT_FALLBACKS
        suggestions = fallbacks[:max_suggestions]
        _log(
            debug_steps,
            log_step,
            f"Provided fallback seed suggestions for genre '{canonical or 'default'}'.",
        )

    return suggestions[:suggestion_cap]


def suggest_remix_tracks(
    existing_tracks: List[str],
    attributes: Dict[str, str],
    *,
    prompt: str,
    target_count: int,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    provider: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Ask the LLM to remix a playlist using the current cached tracks as inspiration."""
    desired_count = max(int(target_count or 0), 0)
    if desired_count <= 0:
        return []

    unique_existing: List[str] = []
    seen_existing: Set[str] = set()
    for track in existing_tracks:
        normalized = (track or "").strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen_existing:
            continue
        seen_existing.add(lowered)
        unique_existing.append(normalized)
    snapshot_limit = max(1, min(desired_count, 25))
    track_snapshot = unique_existing[:snapshot_limit]
    if not track_snapshot:
        track_snapshot = ["(playlist currently empty)"]

    numbered_tracks = "\n".join(
        f"{index + 1}. {entry}" for index, entry in enumerate(track_snapshot)
    )
    attribute_label = json.dumps(attributes, ensure_ascii=False)
    prompt_label = prompt or "Unnamed playlist request"
    query = (
        "You are refreshing an existing Spotify playlist for a user.\n"
        f"Original request: \"{prompt_label}\"\n"
        f"Target attributes: {attribute_label}\n"
        "Current playlist tracks:\n"
        f"{numbered_tracks}\n\n"
        "Remix the playlist by returning exactly "
        f"{desired_count} songs that match the same mood, genre, and energy. "
        "You may keep some of the existing songs, but avoid duplicates overall "
        "and ensure the list feels refreshed. Return a JSON array where each "
        "object contains the keys \"title\" and \"artist\". Prefer well-known "
        "tracks that are likely available on Spotify US."
    )
    _log(debug_steps, log_step, f"LLM prompt (remix suggestions): {query}")
    response = dispatch_llm_query(query, provider=provider)
    snippet = response if len(response) <= 400 else response[:397] + "..."
    _log(debug_steps, log_step, f"LLM raw response (remix suggestions): {snippet}")

    suggestions: List[Dict[str, str]] = []
    seen_pairs: Set[tuple[str, str]] = set()

    def _add_suggestion(title: str, artist: str):
        title = (title or "").strip()
        artist = (artist or "").strip()
        if not title:
            return
        key = (title.lower(), artist.lower())
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        suggestions.append({"title": title, "artist": artist})

    if response:
        parsed = _parse_json_response(response)
        if isinstance(parsed, dict):
            if "tracks" in parsed:
                parsed = parsed["tracks"]
            elif "playlist" in parsed:
                parsed = parsed["playlist"]
            elif "songs" in parsed:
                parsed = parsed["songs"]
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    title = item.get("title") or item.get("song") or item.get("name")
                    artist = item.get("artist") or item.get("artists") or item.get("singer")
                    if isinstance(artist, list):
                        artist = ", ".join(str(part) for part in artist)
                    if title:
                        _add_suggestion(str(title), str(artist or ""))
                elif isinstance(item, str):
                    if " - " in item:
                        title, artist = item.split(" - ", 1)
                    else:
                        title, artist = item, ""
                    _add_suggestion(title, artist)
        else:
            lines = [line.strip() for line in response.splitlines() if line.strip()]
            for line in lines:
                if " - " in line:
                    title, artist = line.split(" - ", 1)
                else:
                    title, artist = line, ""
                _add_suggestion(title, artist)

    if len(suggestions) < desired_count:
        _log(
            debug_steps,
            log_step,
            "LLM remix suggestions insufficient; filling with existing playlist tracks.",
        )
        for track in unique_existing:
            if " - " in track:
                title, artist = track.split(" - ", 1)
            else:
                title, artist = track, ""
            _add_suggestion(title, artist)
            if len(suggestions) >= desired_count:
                break

    if not suggestions:
        _log(
            debug_steps,
            log_step,
            "Remix suggestions unavailable; returning empty list.",
        )

    if suggestions:
        preview = suggestions[: min(5, len(suggestions))]
        _log(debug_steps, log_step, f"LLM parsed remix suggestions: {preview}")

    return suggestions[:desired_count]


def refine_playlist(
    seed_tracks: List[str],
    attributes: Dict[str, str],
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    query_fn: Optional[Callable[[str], str]] = None,
    provider: Optional[str] = None,
) -> List[str]:
    """Ask the LLM for additional tracks based on the current seed list and attributes."""
    track_list = "\n".join(seed_tracks)
    query = (
        f"Given these seed tracks: {track_list}, and attributes {attributes}, "
        "recommend 5 additional widely known songs that are available on Spotify US. "
        "Return each song on a new line and prefer artists that match the requested genre."
    )
    _log(debug_steps, log_step, f"LLM prompt (playlist refinement): {query}")
    llm_query_fn = query_fn or (lambda text: dispatch_llm_query(text, provider=provider))
    response = llm_query_fn(query)
    snippet = response if len(response) <= 400 else response[:397] + "..."
    _log(debug_steps, log_step, f"LLM raw response (refinement): {snippet}")
    if not response:
        _log(
            debug_steps,
            log_step,
            "LLM refinement returned no response; using seed tracks only.",
        )
        return seed_tracks

    additions = [
        line.strip()
        for line in response.splitlines()
        if line.strip() and line.strip() not in seed_tracks
    ]
    _log(debug_steps, log_step, f"LLM suggested additions: {additions}")
    return seed_tracks + additions
