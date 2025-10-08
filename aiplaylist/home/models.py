from django.db import models

class User(models.Model):
    name = models.CharField(max_length=100)
    biography = models.TextField()

    def __str__(self):
        return self.name + ' ' + self.biography[:20] + '...'


class Playlist(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    creator = models.ForeignKey(User, on_delete=models.CASCADE)
    likes = models.IntegerField(default=0)
    cover_image = models.URLField(blank=True)
    spotify_id = models.CharField(max_length=255, unique=True, blank=True)
    spotify_uri = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name


class Song(models.Model):
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name='sample_songs')
    name = models.CharField(max_length=255)
    artist = models.CharField(max_length=255, blank=True)
    spotify_id = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name