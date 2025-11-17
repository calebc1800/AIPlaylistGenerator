#!/usr/bin/env python3
"""Load saved playlist seed data into the local database."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import django
from django.db import transaction
from dotenv import load_dotenv

# Ensure environment variables (especially Spotify credentials) are available when running standalone
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

# Fields that we will attempt to backfill on existing SavedPlaylist rows if empty
BACKFILL_FIELDS = {
    "playlist_name",
    "description",
    "cover_image",
    "creator_display_name",
    "creator_user_id",
    "track_count",
    "total_duration_ms",
    "spotify_uri",
}

def _is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (int, float)):
        return value == 0
    return False


def _normalize_entry(raw_entry: Dict[str, object], idx: int) -> Dict[str, object] | None:
    fields: Dict[str, object] = {}
    if not isinstance(raw_entry, dict):
        return None
    if "fields" in raw_entry:
        fields = raw_entry.get("fields") or {}
    else:
        fields = raw_entry

    playlist_id = (fields.get("playlist_id") or "").strip()
    if not playlist_id:
        print(f"Skipping seed entry #{idx + 1}: missing playlist_id", file=sys.stderr)
        return None

    def _as_int(value: object) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    normalized = {
        "playlist_id": playlist_id,
        "playlist_name": (fields.get("playlist_name") or "").strip(),
        "description": (fields.get("description") or "").strip(),
        "cover_image": (fields.get("cover_image") or "").strip(),
        "like_count": _as_int(fields.get("like_count")),
        "creator_user_id": (fields.get("creator_user_id") or "seed-user"),
        "creator_display_name": (fields.get("creator_display_name") or "").strip(),
        "track_count": _as_int(fields.get("track_count")),
        "total_duration_ms": _as_int(fields.get("total_duration_ms")),
        "spotify_uri": (fields.get("spotify_uri") or f"spotify:playlist:{playlist_id}"),
    }

    return normalized


def _load_seed_entries(fixture_path: Path) -> List[Dict[str, object]]:
    try:
        raw_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Seed fixture not found at {fixture_path}", file=sys.stderr)
        return []
    except json.JSONDecodeError as exc:
        print(f"Seed fixture {fixture_path} is not valid JSON: {exc}", file=sys.stderr)
        return []

    entries: List[Dict[str, object]] = []
    for idx, record in enumerate(raw_data):
        normalized = _normalize_entry(record, idx)
        if normalized:
            entries.append(normalized)
    return entries


def _build_spotify_client():
    """Return a Spotipy client configured via client credentials, if possible."""
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
    except ImportError:
        print("spotipy is not installed; skipping Spotify metadata hydration.", file=sys.stderr)
        return None

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are required to hydrate playlist metadata.",
            file=sys.stderr,
        )
        return None

    try:
        auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        return spotipy.Spotify(auth_manager=auth_manager)
    except Exception as exc:  # pragma: no cover - defensive guard
        print(f"Failed to initialize Spotify client credentials: {exc}", file=sys.stderr)
        return None


def _calculate_playlist_duration(sp, playlist_id: str, initial_tracks: Dict[str, object]) -> int:
    """Sum the duration of all tracks in the playlist."""
    total_duration = 0
    items = (initial_tracks or {}).get("items") or []
    for item in items:
        track = (item or {}).get("track") or {}
        total_duration += int(track.get("duration_ms") or 0)

    total_tracks = int((initial_tracks or {}).get("total") or len(items))
    offset = len(items)
    limit = 100
    while offset < total_tracks:
        try:
            page = sp.playlist_items(
                playlist_id,
                offset=offset,
                limit=limit,
                fields="items(track(duration_ms)),next",
            )
        except Exception as exc:  # pragma: no cover - best-effort logging
            print(f"Failed to fetch playlist tracks for {playlist_id}: {exc}", file=sys.stderr)
            break

        page_items = page.get("items") or []
        if not page_items:
            break
        for item in page_items:
            track = (item or {}).get("track") or {}
            total_duration += int(track.get("duration_ms") or 0)
        offset += len(page_items)
        if not page.get("next"):
            break

    return total_duration


def _fetch_spotify_metadata(sp, playlist_id: str) -> Dict[str, object]:
    """Retrieve playlist metadata directly from Spotify."""
    if sp is None:
        return {}

    try:
        from spotipy.exceptions import SpotifyException
    except ImportError:  # pragma: no cover - already guarded
        SpotifyException = Exception

    try:
        playlist = sp.playlist(playlist_id)
    except SpotifyException as exc:
        print(f"Spotify lookup failed for {playlist_id}: {exc}", file=sys.stderr)
        return {}
    except Exception as exc:  # pragma: no cover - defensive logging
        print(f"Unexpected error fetching playlist {playlist_id}: {exc}", file=sys.stderr)
        return {}

    if not playlist:
        return {}

    metadata: Dict[str, object] = {
        "playlist_name": (playlist.get("name") or "").strip(),
        "description": (playlist.get("description") or "").strip(),
        "cover_image": "",
        "creator_display_name": (playlist.get("owner", {}) or {}).get("display_name") or "",
        "creator_user_id": (playlist.get("owner", {}) or {}).get("id") or "",
        "spotify_uri": playlist.get("uri") or f"spotify:playlist:{playlist_id}",
        "track_count": int((playlist.get("tracks") or {}).get("total") or 0),
    }
    images = playlist.get("images") or []
    if images:
        metadata["cover_image"] = images[0].get("url", "")

    metadata["total_duration_ms"] = _calculate_playlist_duration(
        sp,
        playlist_id,
        playlist.get("tracks") or {},
    )

    return metadata


def _hydrate_entries_with_spotify(entries: List[Dict[str, object]]) -> List[Dict[str, object]]:
    sp = _build_spotify_client()
    if not sp:
        return entries

    for entry in entries:
        metadata = _fetch_spotify_metadata(sp, entry["playlist_id"])
        if not metadata:
            continue

        for key, value in metadata.items():
            if key == "creator_user_id":
                if not entry.get("creator_user_id") and value:
                    entry[key] = value
                continue
            if key == "creator_display_name":
                if (not entry.get("creator_display_name") or _is_blank(entry.get("creator_display_name"))) and value:
                    entry[key] = value
                continue
            if key == "playlist_name" and value:
                entry[key] = value
                continue
            if key in {"description", "cover_image", "spotify_uri"} and value:
                entry[key] = value
                continue
            if key in {"track_count", "total_duration_ms"}:
                entry[key] = int(value or 0)
                continue
    return entries


def _backfill_missing_fields(instance, defaults: Dict[str, object]) -> bool:
    dirty_fields: List[str] = []
    for field in BACKFILL_FIELDS:
        new_value = defaults.get(field)
        current_value = getattr(instance, field)
        if new_value is None:
            continue
        if isinstance(new_value, str) and not new_value.strip():
            continue
        if current_value != new_value:
            setattr(instance, field, new_value)
            dirty_fields.append(field)
    if dirty_fields:
        instance.save(update_fields=dirty_fields)
        return True
    return False


def seed_saved_playlists(entries: Iterable[Dict[str, object]]) -> Tuple[int, int]:
    from recommender.models import SavedPlaylist

    created = 0
    updated = 0
    with transaction.atomic():
        for entry in entries:
            playlist_id = entry["playlist_id"]
            defaults = dict(entry)
            defaults.pop("playlist_id", None)
            obj, was_created = SavedPlaylist.objects.get_or_create(
                playlist_id=playlist_id,
                defaults=defaults,
            )
            if was_created:
                created += 1
                continue
            if _backfill_missing_fields(obj, defaults):
                updated += 1
    return created, updated


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    seeds_dir = project_root / "seeds"
    fixture_path = seeds_dir / "saved_playlists.json"

    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aiplaylist.settings")

    try:
        django.setup()
    except ModuleNotFoundError as exc:
        missing = exc.name or "unknown dependency"
        print(
            "Django could not start because a dependency is missing. "
            f"Install your requirements (pip install -r requirements.txt). "
            f"Missing module: {missing}",
            file=sys.stderr,
        )
        return 1

    entries = _load_seed_entries(fixture_path)
    if not entries:
        return 1
    entries = _hydrate_entries_with_spotify(entries)

    created, updated = seed_saved_playlists(entries)
    print(
        f"Seeded {created} new playlist(s) and backfilled metadata for {updated} existing playlist(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
