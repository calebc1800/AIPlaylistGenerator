from django.shortcuts import redirect, render
from django.views import View

from spotify_auth.session import ensure_valid_spotify_session


class HomeView(View):
    """Display the main landing page"""

    def get(self, request):
        if ensure_valid_spotify_session(request):
            return redirect('dashboard:dashboard')

        return render(request, 'index.html', {})
