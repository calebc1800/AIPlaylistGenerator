"""Lightweight adapters around the local LLM used for playlist generation."""

import json
import logging
import os
import subprocess
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_ATTRIBUTES = {"mood": "chill", "genre": "pop", "energy": "medium"}
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))
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


def query_ollama(prompt: str, model: str = "mistral") -> str:
    """Run a prompt against the local Ollama model and return the raw response."""
    cmd = ["ollama", "run", model, prompt]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Ollama request timed out after %s seconds. Prompt snippet: %s",
            OLLAMA_TIMEOUT_SECONDS,
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
    return result.stdout.strip()


def extract_playlist_attributes(
    prompt: str,
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
) -> Dict[str, str]:
    """Pull mood, genre, and energy descriptors from a free-form user prompt."""
    query = (
        "Extract the mood, genre, and energy level from this user playlist request: "
        f"{prompt}. Return JSON."
    )
    _log(debug_steps, log_step, f"LLM prompt (attribute extraction): {query}")
    response = query_ollama(query)
    snippet = response if len(response) <= 300 else response[:297] + "..."
    _log(debug_steps, log_step, f"LLM raw response (attributes): {snippet}")
    if not response:
        _log(
            debug_steps,
            log_step,
            "LLM attribute extraction failed; using default attributes.",
        )
        return DEFAULT_ATTRIBUTES.copy()

    try:
        parsed = json.loads(response)
        attributes = {
            "mood": parsed.get("mood", DEFAULT_ATTRIBUTES["mood"]),
            "genre": parsed.get("genre", DEFAULT_ATTRIBUTES["genre"]),
            "energy": parsed.get("energy", DEFAULT_ATTRIBUTES["energy"]),
        }
        _log(debug_steps, log_step, f"LLM parsed attributes: {attributes}")
        return attributes
    except json.JSONDecodeError:
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
) -> List[Dict[str, str]]:
    """Use the LLM to propose seed tracks as title/artist pairs."""
    query = (
        "You are selecting seed songs for a Spotify playlist.\n"
        f"Playlist request: \"{prompt}\"\n"
        f"Extracted attributes: {attributes}\n"
        "Return a JSON array with at most five objects, each containing the keys "
        "\"title\" and \"artist\". Choose well-known songs that fit the mood/genre/"
        "energy and are likely available on Spotify."
    )
    _log(debug_steps, log_step, f"LLM prompt (seed suggestions): {query}")
    response = query_ollama(query)
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
        try:
            parsed = json.loads(response)
            if isinstance(parsed, dict):
                if "tracks" in parsed:
                    parsed = parsed["tracks"]
                elif "playlist" in parsed:
                    parsed = parsed["playlist"]
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    title = item.get("title") or item.get("song") or item.get("name")
                    artist = item.get("artist") or item.get("artists")
                    if isinstance(artist, list):
                        artist = ", ".join(artist)
                    if title:
                        _add_suggestion(title, artist or "")
        except json.JSONDecodeError:
            lines = [line.strip() for line in response.splitlines() if line.strip()]
            for line in lines:
                if " - " in line:
                    title, artist = line.split(" - ", 1)
                else:
                    title, artist = line, ""
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

    return suggestions[:max_suggestions]


def refine_playlist(
    seed_tracks: List[str],
    attributes: Dict[str, str],
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
    query_fn: Optional[Callable[[str], str]] = None,
) -> List[str]:
    """Ask the LLM for additional tracks based on the current seed list and attributes."""
    track_list = "\n".join(seed_tracks)
    query = (
        f"Given these seed tracks: {track_list}, and attributes {attributes}, "
        "recommend 5 additional widely known songs that are available on Spotify US. "
        "Return each song on a new line and prefer artists that match the requested genre."
    )
    _log(debug_steps, log_step, f"LLM prompt (playlist refinement): {query}")
    query_llm = query_fn or query_ollama
    response = query_llm(query)
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
