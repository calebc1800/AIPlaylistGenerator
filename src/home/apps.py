from django.apps import AppConfig


class HomeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'home'

class SpotifyAuthConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'spotify_auth'