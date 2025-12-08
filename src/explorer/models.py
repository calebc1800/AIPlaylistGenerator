"""Models for the explorer app."""
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Playlist(models.Model):
    """Model representing a playlist with songs and metadata."""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    creator = models.ForeignKey(User, on_delete=models.CASCADE)
    likes = models.IntegerField(default=0)
    cover_image = models.URLField(blank=True)
    spotify_id = models.CharField(max_length=255, unique=True, blank=True)
    spotify_uri = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-likes']


class Song(models.Model):
    """Model representing a song in a playlist."""
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name='sample_songs')
    name = models.CharField(max_length=255)
    artist = models.CharField(max_length=255, blank=True)
    spotify_id = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name
