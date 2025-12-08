'''
Config for explorer app
'''

from django.apps import AppConfig

'''
Explorer app
'''
class ExplorerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'explorer'
