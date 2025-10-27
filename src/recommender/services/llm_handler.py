import json
import subprocess
from typing import Dict, List

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


def extract_playlist_attributes(prompt: str) -> Dict[str, str]:
    """Pull mood, genre, and energy descriptors from a free-form user prompt."""
    query = (
        "Extract the mood, genre, and energy level from this user playlist request: "
        f"{prompt}. Return JSON."
    )
    response = query_ollama(query)
    if not response:
        return DEFAULT_ATTRIBUTES.copy()

    try:
        parsed = json.loads(response)
        return {
            "mood": parsed.get("mood", DEFAULT_ATTRIBUTES["mood"]),
            "genre": parsed.get("genre", DEFAULT_ATTRIBUTES["genre"]),
            "energy": parsed.get("energy", DEFAULT_ATTRIBUTES["energy"]),
        }
    except json.JSONDecodeError:
        return DEFAULT_ATTRIBUTES.copy()


def refine_playlist(seed_tracks: List[str], attributes: Dict[str, str]) -> List[str]:
    """Ask the LLM for additional tracks based on the current seed list and attributes."""
    track_list = "\n".join(seed_tracks)
    query = (
        f"Given these seed tracks: {track_list}, and attributes {attributes}, "
        "recommend 5 additional songs. Return each song on a new line."
    )
    response = query_ollama(query)
    if not response:
        return seed_tracks

    additions = [
        line.strip()
        for line in response.splitlines()
        if line.strip() and line.strip() not in seed_tracks
    ]
    return seed_tracks + additions
