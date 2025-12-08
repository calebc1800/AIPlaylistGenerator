# codex.md --- Artist Recommendation Feature

## Feature Name

Artist Recommendations Based on Favorite Artists

## Feature Summary

Add a dashboard section that recommends new artists to the user based on
their Spotify favorite artists (from `stats_service`). Each recommended
artist can be explored (top tracks, metadata) and used to generate a
playlist. The user can also select multiple recommended artists to
generate a blended playlist.

## Objectives

1.  Use the user's top artists (stats_service) to fetch related or
    similar artists.
2.  Display "Recommended Artists" on the dashboard with images + genres.
3.  Allow the user to click into any recommended artist to view top
    tracks and metadata.
4.  Allow the user to generate a playlist from:
    -   One recommended artist
    -   Multiple selected recommended artists
5.  Use the existing AI playlist-generation pipeline with a new prompt
    template.

## Context

-   Dashboard template:\
    `src/dashboard/templates/dashboard/dashboard.html`
-   Spotify/statistics functionality:\
    `src/recommender/services/stats_service.py`\
    `src/recommender/services/spotify_handler.py`
-   Playlist creation pipeline:\
    `src/recommender/services/playlist_service.py`
    `src/recommender/views/create_playlist.py`
-   AI prompt creation:\
    `src/recommender/ai/`

## High-Level Flow

### Dashboard

1.  Stats service retrieves top artists for the user.
2.  Recommendation service finds related artists.
3.  Dashboard displays recommended artists in a grid with:
    -   Image\
    -   Name\
    -   Genres\
    -   Checkbox\
    -   "Details" button\
4.  Button to "Create Playlist from Selection".

### Artist Detail View

-   Shows top tracks, metadata, and playlist creation button.

### Playlist Creation

-   Single artist prompt.
-   Multi-artist blended prompt.
-   Uses existing playlist generation pipeline.

## New Components Required

### 1. Recommendation Service

`artist_recommendation_service.py`: - Inputs: userID\
- Fetches top artists\
- Retrieves related artists\
- Deduplicates + ranks\
- Outputs structured recommended artist list

### 2. New Views

#### Dashboard Injection

Add recommended artists to dashboard context.

#### Artist Details View

`artist_detail(request, artist_id)`: - Fetches top tracks + metadata\
- Renders template

#### Playlist Generation Endpoint

POST `/playlist/from_artists/`: - Accepts one or multiple artist IDs

### 3. Templates

Dashboard snippet example:

    <div class="recommended-artists">
        <h3>Recommended Artists</h3>
        {% for artist in recommended_artists %}
            <div class="artist-card">
                <img src="{{ artist.image }}">
                <p>{{ artist.name }}</p>
                <p>{{ artist.genres|join:", " }}</p>
                <input type="checkbox" name="artist_select" value="{{ artist.id }}">
                <button data-artist="{{ artist.id }}" class="view-details">Details</button>
            </div>
        {% endfor %}
        <button id="create-from-selection">Create Playlist From Selection</button>
    </div>

### 4. Prompt Templates

#### Single Artist

    Create a playlist inspired by {{artist_name}}.
    Use my top genres and listening profile as influence.

#### Multi-Artist

    Create a playlist blending these artists: {{artist_list}}.
    Use my listening history to guide cohesion.

## Acceptance Criteria

### Functional

-   Dashboard renders recommendations\
-   Artist detail view functions\
-   Playlist creation works for single and multiple artists

### UI/UX

-   Matches dashboard style\
-   Modal loads quickly\
-   Smooth playlist creation

### Tech

-   Uses spotify_handler\
-   Logic isolated in services

## Tasks

### Backend

-   Create recommendation service\
-   Add related artists to Spotify handler\
-   Add dashboard injection\
-   Add detail + playlist endpoints\
-   Add prompt helper\
-   Update playlist_service

### Frontend

-   Dashboard UI section\
-   Artist detail modal/page\
-   Multi-select interactions\
-   Call new endpoints

### Testing

-   Recommendation ranking\
-   Spotify handler integration\
-   Playlist generation tests\
-   Dashboard UI tests

## Notes for the Agent

-   Prefer spotify_handler for all Spotify operations\
-   Avoid duplicating stats_service logic\
-   Cache where reasonable\
-   Follow existing project style
