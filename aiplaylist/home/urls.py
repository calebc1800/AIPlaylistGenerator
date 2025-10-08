from django.contrib import admin
from django.urls import path, include
from home import views as home_views  # Add this import

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home_views.index, name='home'),  # Add this line for the homepage
    path('spotify/', include('spotify_auth.urls')),
]