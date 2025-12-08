# Codex.md — Playlist Statistics Display Feature

## Overview
This feature adds **playlist analytics** to the result view after a user generates a playlist. The statistics appear in the HTML template:
```
AIPlaylistGenerator/src/recommender/templates/recommender/playlist_result.html
```
The statistics quantify aspects of the generated playlist such as genre composition, novelty, popularity, length, and more.  

---

## Objectives
1. **Enhance feedback**: Give users insight into what kind of playlist was generated.
2. **Leverage cached Spotify data**: Compute novelty by comparing tracks against the user’s top tracks cache.
3. **Render analytics dynamically**: Use Django context variables to send the computed stats to the HTML template.

---

## Implementation Steps

### 1. Backend Changes
File:  
`AIPlaylistGenerator/src/recommender/views.py`

Add or modify the playlist generation view (commonly `playlist_result` or equivalent) to:
- Compute the following:
  - **Genre breakdown** (% of tracks per genre)
  - **Average popularity** (Spotify popularity field)
  - **Novelty score** (percentage of tracks not found in cached top tracks)
  - **Total number of tracks**
  - **Total duration** (sum of all track durations, formatted hh:mm:ss)
  - **Average danceability, energy, or valence** (if available via Spotify audio features)
- Add a helper to fetch the user's cached top tracks for novelty comparison.
- Pass a dictionary `stats` to the template context.

Example snippet:
```python
# views.py (inside playlist_result or similar)
from .services.spotify_handler import get_audio_features, get_user_top_tracks

def compute_playlist_stats(tracks, user_id):
    total_tracks = len(tracks)
    total_duration = sum(t['duration_ms'] for t in tracks)
    avg_popularity = sum(t['popularity'] for t in tracks) / total_tracks

    # Compute genres
    genre_counts = {}
    for t in tracks:
        for g in t.get('genres', []):
            genre_counts[g] = genre_counts.get(g, 0) + 1
    genre_percentage = {g: round((c / total_tracks) * 100, 1) for g, c in genre_counts.items()}

    # Novelty
    cached_top = get_user_top_tracks(user_id)
    cached_ids = {t['id'] for t in cached_top}
    novel_count = len([t for t in tracks if t['id'] not in cached_ids])
    novelty_score = round((novel_count / total_tracks) * 100, 1)

    return {
        "num_tracks": total_tracks,
        "total_duration": f"{total_duration // 60000} min",
        "avg_popularity": round(avg_popularity, 1),
        "genre_percentage": genre_percentage,
        "novelty": novelty_score,
    }
```

Then:
```python
stats = compute_playlist_stats(generated_tracks, request.user.id)
return render(request, "recommender/playlist_result.html", {"playlist": generated_tracks, "stats": stats})
```

---

### 2. Template Updates
File:  
`AIPlaylistGenerator/src/recommender/templates/recommender/playlist_result.html`

Add a statistics panel below or beside the playlist section.

Example snippet:
```html
<div class="playlist-stats">
  <h3>Playlist Statistics</h3>
  <ul>
    <li><strong>Total Tracks:</strong> {{ stats.num_tracks }}</li>
    <li><strong>Total Duration:</strong> {{ stats.total_duration }}</li>
    <li><strong>Average Popularity:</strong> {{ stats.avg_popularity }}</li>
    <li><strong>Novelty:</strong> {{ stats.novelty }}%</li>
  </ul>

  <h4>Genre Distribution</h4>
  <ul>
    {% for genre, pct in stats.genre_percentage.items %}
      <li>{{ genre }} — {{ pct }}%</li>
    {% endfor %}
  </ul>
</div>
```

Optional: Add a simple horizontal bar chart using inline CSS or Chart.js for better visualization.

---

### 3. Optional Enhancements
- Include **energy**, **valence**, and **danceability averages**.
- Add a **“similarity index”** comparing to user’s average preferences.
- Cache computed stats in Redis or database for fast reloads.

---

## Testing
- Generate playlists for different users and verify:
  - Stats display correctly and sum to expected totals.
  - Novelty decreases as playlist overlaps with top tracks.
  - Duration matches Spotify values.

---

## Deliverable Summary
| Component | File | Description |
|------------|------|-------------|
| Stats computation | `views.py` | Add `compute_playlist_stats()` and integrate into playlist view |
| Template display | `playlist_result.html` | Add statistics panel |
| Optional analytics | `spotify_handler.py` | Extend with audio feature fetch for deeper metrics |

---

## Future Extension
Integrate a small D3 or Chart.js visualization showing genre proportions and novelty vs popularity trends over time for each user.
