"""Data models for the recommender app."""

from django.db import models


class SavedPlaylist(models.Model):
    """Persist Spotify playlists saved through the application."""

    playlist_id = models.CharField(max_length=64, unique=True)
    like_count = models.PositiveIntegerField(default=0)
    creator_user_id = models.CharField(max_length=64)

    def __str__(self) -> str:
        return f"{self.playlist_id} ({self.creator_user_id})"
