"""
URL configuration for aiplaylist project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('explorer.urls')),
    path('spotify/', include('spotify_auth.urls')),
]