'''
Urls for explorer app
'''

from django.urls import path
from .views import SearchView, ProfileView, LogoutView, like_playlist

urlpatterns = [
    path('search/', SearchView.as_view(), name='search'),
    path('profile/<str:user_id>/', ProfileView.as_view(), name='profile'),
    path('accounts/logout/', LogoutView.as_view(), name='logout'),
    path('playlist/<str:user_id>/<str:playlist_id>/like/', like_playlist, name='like_playlist'),
]
