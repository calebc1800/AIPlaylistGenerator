"""Data models for the recommender app."""

from django.db import models


class SavedPlaylist(models.Model):
    """Persist Spotify playlists saved through the application."""

    playlist_id = models.CharField(max_length=64, unique=True)
    like_count = models.PositiveIntegerField(default=0)
    creator_user_id = models.CharField(max_length=64)
    creator_display_name = models.CharField(max_length=64)

    def __str__(self) -> str:
        return f"{self.playlist_id} ({self.creator_user_id})"


class PlaylistGenerationStat(models.Model):
    """Historical record of each playlist the recommender generates."""

    user_identifier = models.CharField(max_length=128, db_index=True)
    prompt = models.TextField()
    track_count = models.PositiveIntegerField(default=0)
    total_duration_ms = models.BigIntegerField(default=0)
    top_genre = models.CharField(max_length=128, blank=True)
    avg_novelty = models.FloatField(null=True, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=("user_identifier", "-created_at")),
        ]
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.user_identifier} @ {self.created_at:%Y-%m-%d %H:%M}"
