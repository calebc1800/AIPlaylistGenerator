#!/usr/bin/env python3
"""Load saved playlist seed data into the local database."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import django
from django.core.management import call_command
from django.core.management.base import CommandError


def main() -> int:
    """Load saved playlist seed data from fixture file into database."""
    project_root = Path(__file__).resolve().parents[1]
    seeds_dir = project_root / "seeds"
    fixture_path = seeds_dir / "saved_playlists.json"

    if not fixture_path.exists():
        print(f"Seed fixture not found at {fixture_path}", file=sys.stderr)
        return 1

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

    try:
        call_command("loaddata", fixture_path)
    except CommandError as exc:
        print(f"Failed to seed playlists: {exc}", file=sys.stderr)
        return 1

    print(f"Loaded seed playlists from {fixture_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
