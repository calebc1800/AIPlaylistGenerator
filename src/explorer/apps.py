'''
Config for explorer app
'''

from django.apps import AppConfig

class ExplorerConfig(AppConfig):
    """Explorer app"""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'explorer'
