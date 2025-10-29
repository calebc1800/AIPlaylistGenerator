# Recommender Pipeline Overview

## Quick Flow (Shareable)
- Warm the cache when the dashboard opens: pull the user’s top/recent Spotify tracks, normalize genres, and stash them for later seeds.
- Parse each prompt with the LLM to grab mood/genre/energy plus any explicit artist names.
- Build the seed list in priority order: explicit artist top tracks → cached user tracks for the target genre → resolved LLM suggestions → genre fallback playlists.
- Harvest additional candidates by mixing curated playlists with randomized Spotify searches while filtering for market/genre fit.
- Score every candidate locally (popularity, seed overlap, prompt keywords, year/energy alignment, user cache affinity, novelty) and keep the top results.
- Merge seeds + ranked candidates, cache the full payload, and expose verbose debug data (steps, scores, cache snapshot) when debug mode is on.

The current pipeline blends what the user just asked for with what they already listen to. We still avoid Spotify’s recommendations endpoint; instead we hydrate a user-specific cache, stitch together seed tracks, and locally rank every candidate with a transparent scoring breakdown.

## 0. Login Warm-Up (Dashboard)
1. As soon as the dashboard loads, `build_user_profile_seed_snapshot()` is invoked with the user’s access token.
2. We pull the user’s top tracks (medium-term) and fall back to recently played items if necessary. Each track is serialized with popularity, release year, artists, and genre fingerprints.
3. Artists are batched through `sp.artists` so we can normalize genres (`lofi-hip-hop`, `r-b`, etc.) and compute per-genre buckets.
4. The snapshot is cached (`recommender:user-profile:{user}`) for ~1 hour, including:
   - top 50 track IDs with metadata,
   - genre buckets with counts and average popularity/year,
   - artist play counts and normalized name → ID map.

## 1. Prompt Intake
1. The user submits a free-form request (UI prevents prompts longer than 128 characters).
2. `extract_playlist_attributes()` now asks the LLM for `mood`, `genre`, `energy`, plus optional `artist` and `artists` fields. This is logged verbosely when debug mode is enabled.
3. We determine the cache key (`user_id + prompt`) so repeat prompts within 15 minutes reuse their payload.

## 2. Seed Assembly
Seed selection has multiple contributors, merged in order of trust:

1. **Explicit artist intent** – If the LLM detected a primary artist (e.g., “nmixx”), `ensure_artist_seed()`
   - resolves that artist ID from the cached snapshot or Spotify search,
   - injects cached top tracks when available,
   - otherwise pulls the artist’s top tracks from Spotify.

2. **User genre cache** – Using the normalized genre from the attributes, we pull a handful of cached tracks from the user snapshot (`cached_tracks_for_genre`). This keeps the playlist anchored to what they already enjoy.

3. **LLM suggestions** – `suggest_seed_tracks()` proposes up to five title/artist pairs. `resolve_seed_tracks()` resolves each suggestion to a concrete Spotify track, tagging the origin (`seed_source`).

4. **Genre fallback** – If we still don’t have enough seeds, `discover_top_tracks_for_genre()` scrapes a rotating mix of public playlists and genre searches (with randomized offsets) to backfill mainstream anchors.

Every seed is deduped (`id` + track name/artist key) and tagged with the source that produced it (`artist_top_tracks`, `user_genre_cache`, `llm_seed`, `genre_discovery`, etc.). Seed tracks are inserted into the playlist first and also feed the similarity engine.

## 3. Candidate Harvest
1. `_discover_playlist_seeds()` runs a randomized set of playlist queries (e.g., “top {genre}”, “{genre} mix”) while skipping Spotify-owned editorial lists when required.
2. Search-based candidates expand the pool (`genre:"{genre}" year:2015-2025`, `"{mood}" {genre}`) with randomized offsets so we don’t always hit the same 50 tracks.
3. Every candidate is filtered for market availability, genre alignment with the target genre, and optional Latin-script enforcement.
4. Candidates are deduped against the seed IDs so we never resurface an existing track in the similarity stage.

## 4. Local Similarity Scoring

`get_similar_tracks()` scores each candidate with `_score_track_basic()`, now returning both the numeric score and a detailed breakdown that the UI displays.

Factors:

- **Popularity**: baseline weight (45% of the score).
- **Seed artist overlap**: +0.20 if any candidate artist appears in the seed set.
- **Focus artist bonus**: +0.30 when the user explicitly named the artist and the candidate matches.
- **Keyword match**: up to +0.10 for prompt keywords appearing in the track title.
- **Temporal alignment**: +0.18 when the release year is close to the average seed year. Energy preference adds ±0.05 nudging newer (high) or older (low) tracks.
- **User cache affinity**:
  - +0.18 if the track was already in the user profile snapshot.
  - +0.12 if the track belongs to the dominant cached genre bucket.
- **Novelty**: encourages variety by rewarding rarely-played artists (+0.05) and gently penalizing artists that already appear many times in the snapshot.

Scores are clamped at ≥0 and sorted descending. We iterate the sorted list, enforcing that no artist appears more than twice in the final playlist.

## 5. Playlist Construction & Remix
1. The final playlist concatenates the ordered seeds with the top N scored recommendations; duplicates are avoided with a shared dedupe set.
2. `generate_playlist` caches the full payload (tracks, scores, sources, debug log, snapshot summary) for 15 minutes.
3. Remix requests reuse the cached payload, ask the LLM for replacement suggestions, resolve them, and fall back to the similarity engine when the remix seeds run short.

## 6. Debug & Observability
When `RECOMMENDER_DEBUG_VIEW_ENABLED` is true, the playlist result page surfaces:

- Step-by-step debug log with timings.
- Seed inventory table with source labels and popularity/year.
- Similarity table showing each recommendation’s score, breakdown components, and overlap badges.
- Cached profile snapshot (top tracks, top artists, genre buckets) to explain why certain seeds or bonuses were applied.

## Configuration Knobs

- `RECOMMENDER_POPULARITY_THRESHOLD` / `RECOMMENDER_GENRE_POPULARITY_OVERRIDES` – minimum popularity per genre.
- `RECOMMENDER_REQUIRE_LATIN` – enforce Latin-script titles.
- `RECOMMENDER_SEED_LIMIT` – minimum number of seed tracks before falling back to genre discovery.
- `RECOMMENDER_USER_PROFILE_CACHE_TTL` – duration (seconds) to keep the user snapshot warm.
- `RECOMMENDER_CACHE_TIMEOUT_SECONDS` – how long playlist payloads remain cached.

This architecture keeps API usage predictable, leans on cached first-party data to personalize genre and artist selection, and surfaces enough diagnostics to debug or tweak future heuristics quickly.
