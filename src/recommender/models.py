"""Data models for the recommender app."""

from django.db import models
from django.utils import timezone

class UniqueLike(models.Model):
    """Restricts likes to a unique combination of user_id and playlist_id."""
    user_id = models.CharField(max_length=64)
    playlist_id = models.CharField(max_length=64)

    class Meta:
        unique_together = (("user_id", "playlist_id"),)

"""class Likes(models.Model):
    # Wrapper for array of unique likes 
    unique_likes = []

    def add(self, user_id, playlist_id):
        self.unique_likes.append(UniqueLike.objects.create(user_id=user_id, playlist_id=playlist_id))

    def remove(self, user_id, playlist_id):
        self.unique_likes.remove(UniqueLike())

    def __count__(self):
        return len(self.unique_likes)"""

class SavedPlaylist(models.Model):
    """Persist Spotify playlists saved through the application."""

    playlist_id = models.CharField(max_length=64, unique=True)
    playlist_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    cover_image = models.URLField(blank=True)
    creator_user_id = models.CharField(max_length=64)
    creator_display_name = models.CharField(max_length=64)
    track_count = models.PositiveIntegerField(default=0)
    total_duration_ms = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    spotify_uri = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def like_count(self):
        """Calculate the number of likes for this playlist."""
        return UniqueLike.objects.filter(playlist_id=self.playlist_id).count()

    def __str__(self) -> str:
        return f"{self.playlist_name} ({self.creator_display_name})"


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
