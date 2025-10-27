import json
import subprocess
from typing import Dict, List, Optional

DEFAULT_ATTRIBUTES = {"mood": "chill", "genre": "pop", "energy": "medium"}


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


def extract_playlist_attributes(prompt: str, debug_steps: Optional[List[str]] = None) -> Dict[str, str]:
    """Pull mood, genre, and energy descriptors from a free-form user prompt."""
    query = (
        "Extract the mood, genre, and energy level from this user playlist request: "
        f"{prompt}. Return JSON."
    )
    response = query_ollama(query)
    if not response:
        if debug_steps is not None:
            debug_steps.append("LLM attribute extraction failed; using default attributes.")
        return DEFAULT_ATTRIBUTES.copy()

    try:
        parsed = json.loads(response)
        attributes = {
            "mood": parsed.get("mood", DEFAULT_ATTRIBUTES["mood"]),
            "genre": parsed.get("genre", DEFAULT_ATTRIBUTES["genre"]),
            "energy": parsed.get("energy", DEFAULT_ATTRIBUTES["energy"]),
        }
        if debug_steps is not None:
            debug_steps.append(f"LLM returned attributes: {attributes}")
        return attributes
    except json.JSONDecodeError:
        if debug_steps is not None:
            debug_steps.append(
                f"Failed to parse LLM attribute response '{response}'; using defaults."
            )
        return DEFAULT_ATTRIBUTES.copy()


def suggest_seed_tracks(
    prompt: str,
    attributes: Dict[str, str],
    debug_steps: Optional[List[str]] = None,
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
    response = query_ollama(query)
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
            if isinstance(parsed, dict) and "tracks" in parsed:
                parsed = parsed["tracks"]
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

    # Fallback suggestions if the model response failed
    if debug_steps is not None:
        if suggestions:
            debug_steps.append(f"LLM seed suggestions: {suggestions[:max_suggestions]}")
        else:
            debug_steps.append("LLM seed suggestions unavailable; will rely on Spotify fallback.")

    return suggestions[:max_suggestions]


def refine_playlist(
    seed_tracks: List[str],
    attributes: Dict[str, str],
    debug_steps: Optional[List[str]] = None,
) -> List[str]:
    """Ask the LLM for additional tracks based on the current seed list and attributes."""
    track_list = "\n".join(seed_tracks)
    query = (
        f"Given these seed tracks: {track_list}, and attributes {attributes}, "
        "recommend 5 additional widely known songs that are available on Spotify US. "
        "Return each song on a new line and prefer artists that match the requested genre."
    )
    if debug_steps is not None:
        debug_steps.append("Querying LLM for refined playlist suggestions.")
    response = query_ollama(query)
    if not response:
        if debug_steps is not None:
            debug_steps.append("LLM refinement returned no response; using seed tracks only.")
        return seed_tracks

    additions = [
        line.strip()
        for line in response.splitlines()
        if line.strip() and line.strip() not in seed_tracks
    ]
    if debug_steps is not None:
        debug_steps.append(f"LLM suggested additions: {additions}")
    return seed_tracks + additions
