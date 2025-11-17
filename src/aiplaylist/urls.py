"""
URL configuration for aiplaylist project.
"""
from django.contrib import admin
from django.urls import path, include
from explorer.views import SearchView, LogoutView
from dashboard.views import CreateView
from .views import HomeView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', HomeView.as_view(), name='home'),
    path('search/', SearchView.as_view(), name='search'),
    path('accounts/logout/', LogoutView.as_view(), name='logout'),
    path('dashboard/', include('dashboard.urls')),
    path('create/', CreateView.as_view(), name='create'),
    path('explorer/', include('explorer.urls')),
    path('spotify/', include('spotify_auth.urls')),
    path('recommender/', include('recommender.urls')),
]
