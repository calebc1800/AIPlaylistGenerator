from django.urls import path, include
from django.contrib import admin
from . import views


urlpatterns = [
    path('', views.index, name='index'),
    path('admin/', admin.site.urls),
    path('spotify/', include('spotify_auth.urls')),
]