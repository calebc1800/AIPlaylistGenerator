"""App configuration for spotify_auth."""

from django.apps import AppConfig

class SpotifyAuthConfig(AppConfig):
    """Register the spotify_auth Django application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "spotify_auth"
