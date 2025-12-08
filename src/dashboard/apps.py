"""App configuration for the dashboard app."""

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    """Register the dashboard Django application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"
