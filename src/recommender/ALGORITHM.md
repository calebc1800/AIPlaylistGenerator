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

## 4. Metadata Collection
1. For seeds and candidates we retain artist IDs, release years, and popularity so we can score without Spotify’s recommendations or audio-feature endpoints.
2. Timestamps and raw responses from Spotify searches make it easy to trace how each candidate entered the pool.

## 5. Local Similarity Scoring
1. Energy preferences come from the prompt (e.g., `high`, `low`) and we bias scores toward newer or older releases accordingly.
2. The scoring function combines popularity, artist overlap, prompt keyword matches, and temporal diversity (rewarding songs far from the average seed release year).
3. Artists are deduplicated so no artist appears more than twice.

## 6. Output & Debug Trail
1. The ordered debug list is rendered first with per-step timestamps, raw LLM prompts/responses, Spotify endpoints, and any warnings. Errors are bubbled up separately so the UI can warn the user if something failed.
2. The final playlist merges seed tracks with the top N scored candidates.
3. All steps are cached for the prompt/user pair to accelerate subsequent runs.

## Configuration Knobs
- `RECOMMENDER_POPULARITY_THRESHOLD` and `RECOMMENDER_GENRE_POPULARITY_OVERRIDES` control how strict we are about mainstream popularity when filtering candidates.
- `RECOMMENDER_REQUIRE_LATIN` toggles the optional non-Latin title filter.

These settings enable genre-specific tuning without touching the core algorithm.

This design keeps resource usage low (no external APIs, small cache footprint) while remaining fully Spotify-only—ready to benefit from the official recommendations endpoint if it becomes available later.***
