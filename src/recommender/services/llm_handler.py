import json
import subprocess
from typing import Callable, Dict, List, Optional

DEFAULT_ATTRIBUTES = {"mood": "chill", "genre": "pop", "energy": "medium"}


def _log(
    debug_steps: Optional[List[str]],
    log_step: Optional[Callable[[str], None]],
    message: str,
) -> None:
    if log_step:
        log_step(message)
    elif debug_steps is not None:
        debug_steps.append(message)


def query_ollama(prompt: str, model: str = "mistral") -> str:
    """Run a prompt against the local Ollama model and return the raw response."""
    cmd = ["ollama", "run", model, prompt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return ""
    if result.returncode != 0:
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

    return suggestions[:max_suggestions]


def refine_playlist(
    seed_tracks: List[str],
    attributes: Dict[str, str],
    debug_steps: Optional[List[str]] = None,
    log_step: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """Ask the LLM for additional tracks based on the current seed list and attributes."""
    track_list = "\n".join(seed_tracks)
    query = (
        f"Given these seed tracks: {track_list}, and attributes {attributes}, "
        "recommend 5 additional widely known songs that are available on Spotify US. "
        "Return each song on a new line and prefer artists that match the requested genre."
    )
    _log(debug_steps, log_step, f"LLM prompt (playlist refinement): {query}")
    response = query_ollama(query)
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

    return suggestions[:max_suggestions]
