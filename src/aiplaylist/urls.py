"""
URL configuration for aiplaylist project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('home.urls')),
    path('spotify/', include('spotify_auth.urls')),
    path('recommender/', include('recommender.urls')),
]
