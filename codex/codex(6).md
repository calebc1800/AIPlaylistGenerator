# Codex Agent Instructions: Dashboard Listening-Based Suggestions

## Objective
Implement a new feature for the AI Playlist Generator dashboard that displays listening-based suggestion prompts. These prompts are auto‑generated from the user's recent listening history using the recommender/statistics services. When clicked, a prompt autofills into the playlist creation page.

## Required Output
Produce:
1. Backend logic to retrieve user's recent genres, artists, and listening patterns.
2. A small prompt‑grid component rendered on the dashboard page.
3. API endpoint(s) or service functions to supply the suggestion data.
4. Frontend JavaScript to insert an autofill prompt into the `/create` page workflow.

---
## Files to Modify
- **dashboard template**: `/home/student/AIPlaylistGenerator/src/dashboard/templates/dashboard/dashboard.html`
- **services to reference**: `/home/student/AIPlaylistGenerator/src/recommender/services/stats_service.py` and related recommender services.
- If needed, extend recommender services (new file or modify existing).
- Add optional serializer/helper file for suggestion formatting.

---
## Steps

### 1. Inspect Existing Services
- Review `stats_service.py` for available methods that return:
  - Top genres
  - Top artists
  - Time‑based listening patterns
- Determine if history endpoints return track metadata useful for building suggestion prompts.
- Check other services in `/recommender/services` for any reusable logic (e.g., recommendation engine, similarity computation, clustering).

### 2. Create Suggestion Extraction Logic
- Implement a function, e.g. `generate_suggestions(user_id)`.
- Inputs:
  - User's top genres (list)
  - User's top artists (list)
  - Optional: mood / energy metrics if available
- Output:
  - A list of 6–12 short prompt strings such as:
    - "Create a playlist with my top indie tracks"
    - "Something like my favorite artist: {artist}"
    - "A blend of {genre1} and {genre2}"
    - "High‑energy tracks based on my weekly listening"
- Keep each prompt short and actionable.

### 3. Create Backend API for Dashboard
- Add a new endpoint in the dashboard backend, e.g. `/dashboard/api/listening_suggestions/`.
- Returns JSON structure:
```json
{
  "suggestions": ["prompt1", "prompt2", ...]
}
```

### 4. Update Dashboard Template
Modify `dashboard.html` to:
- Add a new grid section titled **"Suggestions Based on Your Listening"**.
- Dynamically load the suggestions via AJAX/Fetch.
- Render them as clickable cards or buttons.

### 5. JavaScript Interaction
Add JS under dashboard:
- On page load, fetch suggestions from the new endpoint.
- Render suggestion tiles.
- Clicking a tile should redirect user to `/create` with query parameter:
  - `/create?prompt=<encoded_prompt>`
- Ensure the create page already supports reading this query parameter; if not, implement.

### 6. Create Page Autofill Logic
In `/create` page:
- Detect `prompt` query parameter.
- Auto‑insert its value into the main text input for playlist creation.
- Optionally trigger UI highlight.

### 7. Verification Checklist
- Ensure suggestions are only shown if service can compute listening stats.
- Provide fallback messages if no stats available.
- Handle anonymous/first‑time users gracefully.
- Confirm prompt‑passing into `/create` functions correctly.

---
## Deliverables for Agent
- New backend suggestion generator.
- API route returning suggestion list.
- Updated dashboard template and JS.
- Create-page autofill behavior.
- Use existing stats and recommender services where possible.

---
## Additional Notes
- Keep prompts stylistically consistent with app tone.
- Ensure no duplicate prompts.
- Prefer lightweight computation; avoid heavy model inference for this feature.

---
## End of Instructions

