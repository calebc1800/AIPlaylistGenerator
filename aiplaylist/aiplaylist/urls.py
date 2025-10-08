"""
URL configuration for aiplaylist project.
"""
from django.contrib import admin
from django.urls import path, include
from home import views as home_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home_views.index, name='home'),  # Add this line
    path('spotify/', include('spotify_auth.urls')),
]