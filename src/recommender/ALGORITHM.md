# Recommender Pipeline Overview

This module builds Spotify playlists from a free-form user prompt without calling the Spotify recommendations endpoint. The flow combines LLM prompt parsing, curated Spotify data, and local audio-feature scoring.

## 1. Prompt Intake
1. User submits a description such as “high energy pop workout”.
2. `extract_playlist_attributes()` (LLM) parses the prompt into `mood`, `genre`, and `energy` while logging the raw request/response with timestamps.
3. We hash the prompt + user ID to cache results for 15 minutes and avoid redundant work.

## 2. Seed Discovery
1. `suggest_seed_tracks()` (LLM) tries to produce song/artist pairs.
2. `resolve_seed_tracks()` calls `search` to turn those suggestions into Spotify track IDs, keeping only tracks available in the target market.
3. If nothing resolves, `discover_top_tracks_for_genre()` pulls popular tracks from genre playlists and searches to form the initial seed set.
4. Resolved seeds become the base playlist entries and the anchor for similarity scoring.

## 3. Candidate Collection
1. `_discover_playlist_seeds()` fetches a few “Top {genre}” playlists and caches their track lists.
2. Targeted track searches (`genre:"pop" year:2015-2025`, `"energetic" pop`, etc.) grow the pool.
3. Candidates are filtered for market availability, configurable popularity thresholds (per genre), and artist-genre alignment. Non-Latin titles are optionally filtered (disabled by default via `RECOMMENDER_REQUIRE_LATIN`).

## 4. Audio Feature Cache
1. `_fetch_audio_features()` batches track IDs into `audio_features` calls and stores the result in Django’s cache (TTL 1 hour).
2. `_fetch_track_years()` retrieves release-year metadata to support temporal diversity.
3. Seeds and candidates now share a consistent feature vector with weighted dimensions (danceability, energy, valence, tempo, acousticness, instrumentalness, speechiness, loudness). Weights are configurable through `RECOMMENDER_FEATURE_WEIGHTS`.

## 5. Local Similarity Scoring
1. `_compute_centroid()` averages seed vectors to create a weighted “sound profile” and derives the target energy directly from the seed audio features (falling back to mood defaults if unavailable).
2. `_fetch_track_years()` also gives us the mean seed release year; `_score_track()` encourages temporal diversity by rewarding candidates far from that mean.
3. `_score_track()` mixes weighted audio distance, energy alignment, temporal bonus, and popularity. Artists are deduplicated so no artist appears more than twice.

## 6. Output & Debug Trail
1. The ordered debug list is rendered first with per-step timestamps, raw LLM prompts/responses, Spotify endpoints, and any warnings. Errors are bubbled up separately so the UI can warn the user if something failed.
2. The final playlist merges seed tracks with the top N scored candidates.
3. All steps are cached for the prompt/user pair to accelerate subsequent runs.

## Configuration Knobs
- `RECOMMENDER_POPULARITY_THRESHOLD` and `RECOMMENDER_GENRE_POPULARITY_OVERRIDES` control how strict we are about mainstream popularity when filtering candidates.
- `RECOMMENDER_REQUIRE_LATIN` toggles the optional non-Latin title filter.
- `RECOMMENDER_FEATURE_WEIGHTS` lets you rebalance the importance of each audio attribute.

These settings enable genre-specific tuning without touching the core algorithm.

This design keeps resource usage low (no external APIs, small cache footprint) while remaining fully Spotify-only—ready to benefit from the official recommendations endpoint if it becomes available later.***
