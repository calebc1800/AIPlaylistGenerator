# **codex.md**  
### AI Playlist Generator — Recommender Module  

## **Overview**  
This document defines how to implement an AI-powered playlist generation feature in the `ai_playlist` Django project. The system interprets user text prompts, extracts mood and genre attributes via a local LLM (Ollama), retrieves seed tracks from the Spotify API, and refines the list with AI-driven recommendations.  

---

## **System Flow**
```
User prompt → Ollama (extracts mood/genre/energy) → Spotify API (seed tracks)
     ↓
Ollama (refines playlist / adds tracks)
     ↓
Output playlist (text view → Spotify playlist)
```

---

## **Architecture**

### **New Django App: `recommender`**
```
python manage.py startapp recommender
```



## **Workflow Details**

### 1. **User Input**

Add this or expand on the dashboard view

`prompt_box.html`
```html
<form method="post" action="{% url 'generate_playlist' %}">
  {% csrf_token %}
  <textarea name="prompt" placeholder="Describe your desired playlist..." required></textarea>
  <button type="submit">Generate Playlist</button>
</form>
```

---

### 2. **LLM Handler**
`recommender/services/llm_handler.py`
```python
import subprocess, json

def query_ollama(prompt: str, model: str = "mistral") -> str:
    cmd = ["ollama", "run", model, prompt]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()

def extract_playlist_attributes(prompt: str) -> dict:
    query = f"Extract the mood, genre, and energy level from this user playlist request: {prompt}. Return JSON."
    response = query_ollama(query)
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        return {"mood": "chill", "genre": "pop", "energy": "medium"}

def refine_playlist(seed_tracks: list, attributes: dict) -> list:
    track_list = "\n".join(seed_tracks)
    query = f"Given these seed tracks: {track_list}, and attributes {attributes}, recommend 5 additional songs."
    response = query_ollama(query)
    return seed_tracks + response.split("\n")
```

---

### 3. **Spotify Handler**
`recommender/services/spotify_handler.py`
```python
import spotipy
from spotipy.oauth2 import SpotifyOAuth

def get_spotify_recommendations(attributes: dict, token: str) -> list:
    sp = spotipy.Spotify(auth=token)
    results = sp.recommendations(
        seed_genres=[attributes["genre"]],
        target_energy=0.3 if attributes["energy"] == "low" else 0.8,
        limit=10
    )
    return [track["name"] + " - " + track["artists"][0]["name"] for track in results["tracks"]]
```

---

### 4. **View Logic**
`recommender/views.py`
```python
from django.shortcuts import render
from .services.llm_handler import extract_playlist_attributes, refine_playlist
from .services.spotify_handler import get_spotify_recommendations

def generate_playlist(request):
    if request.method == "POST":
        prompt = request.POST.get("prompt")
        token = request.user.social_auth.get(provider="spotify").extra_data["access_token"]
        attributes = extract_playlist_attributes(prompt)
        seed_tracks = get_spotify_recommendations(attributes, token)
        playlist = refine_playlist(seed_tracks, attributes)
        return render(request, "recommender/playlist_result.html", {"playlist": playlist})
```

---

### 5. **Output Template**
`recommender/templates/recommender/playlist_result.html`
```html
<h2>Your Generated Playlist</h2>
<ul>
  {% for song in playlist %}
    <li>{{ song }}</li>
  {% endfor %}
</ul>
```

---

## **Local LLM Setup — Ollama**

### **Install Ollama**
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull mistral
```

### **Test locally**
```bash
ollama run mistral "Extract mood, genre, and energy from 'upbeat K-pop for running'"
```

If response works, the Django handler will run correctly.

---

## **Future Extension: Spotify Playlist Creation**
```python
playlist = sp.user_playlist_create(user=request.user.username, name="AI Generated Playlist")
sp.playlist_add_items(playlist["id"], track_ids)
```

---

## **Clarifications Needed**
1. Should playlists and prompts be saved in the DB for user history?  for now, no
2. Should Ollama run on the same host as Django or be containerized separately?  same host
3. Should recommendations be cached per user prompt?  yes
