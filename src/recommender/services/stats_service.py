"""Aggregation helpers for playlist generation history."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List

from django.db.models import Avg, Count, Sum

from ..models import PlaylistGenerationStat


@dataclass
class GenerationSummary:
    total_playlists: int = 0
    total_tracks: int = 0
    total_duration_ms: int = 0
    total_tokens: int = 0
    avg_novelty: float | None = None
    top_genre: str = ""
    last_generated_at: str | None = None

    @property
    def total_hours(self) -> float:
        return round(self.total_duration_ms / 1000 / 3600, 2) if self.total_duration_ms else 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "total_playlists": self.total_playlists,
            "total_tracks": self.total_tracks,
            "total_duration_ms": self.total_duration_ms,
            "total_hours": self.total_hours,
            "total_tokens": self.total_tokens,
            "avg_novelty": self.avg_novelty,
            "top_genre": self.top_genre,
            "last_generated_at": self.last_generated_at,
        }


def summarize_generation_stats(user_identifier: str | None) -> Dict[str, object]:
    """Return aggregate metrics for the provided user identifier."""
    if not user_identifier:
        return GenerationSummary().as_dict()

    queryset = PlaylistGenerationStat.objects.filter(user_identifier=user_identifier)
    if not queryset.exists():
        return GenerationSummary().as_dict()

    aggregates = queryset.aggregate(
        total_playlists=Count("id"),
        total_tracks=Sum("track_count"),
        total_duration_ms=Sum("total_duration_ms"),
        total_tokens=Sum("total_tokens"),
        avg_novelty=Avg("avg_novelty"),
    )
    top_genre_entry = (
        queryset.exclude(top_genre="")
        .values("top_genre")
        .annotate(total=Count("id"))
        .order_by("-total", "top_genre")
        .first()
    )
    latest = queryset.order_by("-created_at").first()

    summary = GenerationSummary(
        total_playlists=int(aggregates.get("total_playlists") or 0),
        total_tracks=int(aggregates.get("total_tracks") or 0),
        total_duration_ms=int(aggregates.get("total_duration_ms") or 0),
        total_tokens=int(aggregates.get("total_tokens") or 0),
        avg_novelty=round(float(aggregates["avg_novelty"]), 1)
        if aggregates.get("avg_novelty") is not None
        else None,
        top_genre=(top_genre_entry or {}).get("top_genre", "") or "",
        last_generated_at=latest.created_at.isoformat() if latest else None,
    )
    return summary.as_dict()


def get_genre_breakdown(user_identifier: str | None, sample_size: int = 25) -> List[Dict[str, object]]:
    """Return the most common genres across the user's recent generations."""
    if not user_identifier:
        return []

    queryset = PlaylistGenerationStat.objects.filter(user_identifier=user_identifier).order_by("-created_at")[
        :sample_size
    ]
    genre_counts: Counter[str] = Counter()

    for stat in queryset:
        genre_top = []
        if isinstance(stat.stats, dict):
            genre_top = stat.stats.get("genre_top") or []
        for entry in _normalize_genre_entries(genre_top):
            genre_counts[entry["genre"]] += entry["weight"]

        if stat.top_genre and stat.top_genre not in genre_counts:
            genre_counts[stat.top_genre] += 1

    if not genre_counts:
        return []

    total_weight = sum(genre_counts.values())
    breakdown = [
        {
            "genre": genre,
            "percentage": round((count / total_weight) * 100, 1),
        }
        for genre, count in genre_counts.most_common(5)
    ]
    return breakdown


def _normalize_genre_entries(entries: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    normalized: List[Dict[str, object]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        genre = (entry.get("genre") or "").strip()
        if not genre:
            continue
        weight = float(entry.get("percentage") or 0)
        normalized.append({"genre": genre, "weight": weight or 1})
    return normalized
