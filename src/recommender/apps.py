"""App configuration for the recommender module."""

from django.apps import AppConfig


class RecommenderConfig(AppConfig):
    """Connect the recommender app with Django's app registry."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'recommender'
