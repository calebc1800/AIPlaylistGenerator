from django.urls import path
from .views import HomeView, SearchView, ProfileView, LogoutView

urlpatterns = [
    path('', HomeView.as_view(), name='explorer'),
    path('search/', SearchView.as_view(), name='search'),
    path('profile/<int:user_id>/', ProfileView.as_view(), name='profile'),
    path('accounts/logout/', LogoutView.as_view(), name='logout'),
]