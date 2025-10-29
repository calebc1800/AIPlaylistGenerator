"""Lightweight adapters around the OpenAI/Ollama LLMs used for playlist generation."""

import json
import logging
import os
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional, Set

from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_ATTRIBUTES = {"mood": "chill", "genre": "pop", "energy": "medium"}
try:
    from django.conf import settings  # type: ignore
except Exception:  # pragma: no cover - settings may not be ready during import time
    settings = None

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

_OPENAI_CLIENT: Optional[OpenAI] = None
_JSON_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def _get_setting(name: str, default=None):
    if settings is not None and hasattr(settings, name):
        return getattr(settings, name)
    return os.getenv(name, default)


def _get_openai_client() -> Optional[OpenAI]:
    """Lazily initialize the shared OpenAI client."""
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is not None:
        return _OPENAI_CLIENT

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
        _OPENAI_CLIENT = OpenAI(**client_kwargs)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to initialize OpenAI client: %s", exc)
        return None

    return _OPENAI_CLIENT



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


def _resolve_provider(provider: Optional[str]) -> str:
    default_provider = (_get_setting("RECOMMENDER_LLM_DEFAULT_PROVIDER", "openai") or "openai").lower()
    candidates = {"openai", "ollama"}
    if provider:
        normalized = provider.strip().lower()
        if normalized in candidates:
            return normalized
    return default_provider if default_provider in candidates else "openai"


def query_ollama(
    prompt: str,
    *,
    model: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    """Send a prompt to a local Ollama model and return the response text."""
    resolved_model = model or (_get_setting("RECOMMENDER_OLLAMA_MODEL", None) or "mistral")
    resolved_timeout = timeout
    if resolved_timeout is None:
        timeout_setting = _get_setting("RECOMMENDER_OLLAMA_TIMEOUT_SECONDS")
        if timeout_setting is not None:
            try:
                resolved_timeout = int(timeout_setting)
            except (TypeError, ValueError):
                resolved_timeout = None
    if resolved_timeout is None:
        if settings is not None and getattr(settings, "DEBUG", False):
            resolved_timeout = 600
        else:
            resolved_timeout = 60

    cmd = ["ollama", "run", resolved_model, prompt]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(resolved_timeout, 1),
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Ollama request timed out after %s seconds. Prompt snippet: %s",
            resolved_timeout,
            prompt[:120],
        )
        return ""
    except FileNotFoundError:
        logger.error("Ollama executable not found. Ensure Ollama is installed and on PATH.")
        return ""
    except subprocess.SubprocessError as exc:
        logger.error("Ollama execution failed: %s", exc)
        return ""
    if result.returncode != 0:
        logger.error(
            "Ollama returned non-zero exit code %s. stderr: %s",
            result.returncode,
            (result.stderr or "").strip(),
        )
        return ""
    return (result.stdout or "").strip()


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
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("OpenAI request failed: %s", exc)
        return ""

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
    except Exception:  # pragma: no cover - fallback only
        return ""


def dispatch_llm_query(
    prompt: str,
    *,
    provider: Optional[str] = None,
    **kwargs: object,
) -> str:
    """Route LLM prompts to the active provider (OpenAI by default)."""
    resolved_provider = _resolve_provider(provider)
    if resolved_provider == "ollama":
        model = kwargs.get("ollama_model") or kwargs.get("model")
        timeout = kwargs.get("timeout") or kwargs.get("ollama_timeout")
        return query_ollama(prompt, model=model if isinstance(model, str) else None, timeout=timeout if isinstance(timeout, int) else None)
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
) -> Dict[str, str]:
    """Pull mood, genre, and energy descriptors from a free-form user prompt."""
    query = (
        "Extract the mood, genre, and energy level from this user playlist request: "
        f"{prompt}. Return JSON."
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
            "genre": lowered.get("genre") or lowered.get("music_genre", DEFAULT_ATTRIBUTES["genre"]),
            "energy": lowered.get("energy")
            or lowered.get("energy_level")
            or lowered.get("energylevel")
            or DEFAULT_ATTRIBUTES["energy"],
        }
        attributes = {key: (value or DEFAULT_ATTRIBUTES[key]) for key, value in attributes.items()}
        _log(debug_steps, log_step, f"LLM parsed attributes: {attributes}")
        return attributes

    _log(
        debug_steps,
        log_step,
        f"Failed to parse LLM attribute response; using defaults. Response snippet: {snippet}",
    )
    return DEFAULT_ATTRIBUTES.copy()


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

    numbered_tracks = "\n".join(f"{index + 1}. {entry}" for index, entry in enumerate(track_snapshot))
    attribute_label = json.dumps(attributes, ensure_ascii=False)
    prompt_label = prompt or "Unnamed playlist request"
    query = (
        "You are refreshing an existing Spotify playlist for a user.\n"
        f"Original request: \"{prompt_label}\"\n"
        f"Target attributes: {attribute_label}\n"
        "Current playlist tracks:\n"
        f"{numbered_tracks}\n\n"
        f"Remix the playlist by returning exactly {desired_count} songs that match the same mood, genre, and energy."
        " You may keep some of the existing songs, but avoid duplicates overall and ensure the list feels refreshed."
        " Return a JSON array where each object contains the keys \"title\" and \"artist\"."
        " Prefer well-known tracks that are likely available on Spotify US."
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
