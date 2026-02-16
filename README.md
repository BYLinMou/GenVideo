# GenVideo

Novel-to-video generation system (FastAPI + Vue 3), updated for the current workflow:

- Better backend logging and error visibility
- Cancelable video jobs
- Segment-clip preview before final video
- Sentence-based grouping semantics
- Subtitle rendering with CJK font auto-detection
- Optional looping BGM mixed in final compose (frontend-controlled)
- Character reference-image library (select/upload/generate)
- Per-character TTS voice selection from real voice list
- Scene-image reuse cache with LLM text matching (cost reduction)
- SQLite-persisted job states with restart recovery
- Interrupted jobs auto-resume from generated clip checkpoints

## Workflow (Current)

1. Input novel text
2. Segment preview (sentence-aware)
3. Analyze characters
4. Confirm characters (voice + reference image)
5. Generate clips and final video
6. Preview each clip and download final video

## Core Semantics (Important)

- Sentence splitting supports CJK punctuation (e.g. `。！？；`).
- `sentences_per_segment = N` means each segment contains `N` sentences.
- One segment generates one clip.
- One segment triggers one image prompt call (with reference images if available).
- Scene-image cache matching is text-only (character/action/scene descriptors).
- Character config supports identity flags: `is_main_character` and `is_story_self` (frontend label: `第一人称`).
- Identity flags are uniqueness-constrained: at most one `is_main_character=true`, at most one `is_story_self=true`.
- Segment image generation uses single-call LLM output for both prompt metadata and character assignment (`primary_index` / `related_indexes`).
- `segment_groups_range` supports 1-based ranges, e.g. `1-80,81-90`.
- A single value like `60` means `1-60`.
- A single non-positive value like `0` or `-1` means all segments.

## Backend

### Start

Local run prerequisite: install `ffmpeg` first (Docker image already includes it).

Example install commands:

```bash
# Windows (winget)
winget install Gyan.FFmpeg

# macOS (Homebrew)
brew install ffmpeg

# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y ffmpeg
```

After install, verify:

```bash
ffmpeg -version
```

```bash
cd backend
conda create -n genvideo python=3.13 -y
conda activate genvideo
pip install -r requirements.txt
python run.py
```

If `genvideo` already exists, skip the `conda create` step.

Default: `http://localhost:8000`

### Key APIs

- `GET /api/health`
- `GET /api/workspace-auth/status`
- `POST /api/workspace-auth/login`
- `POST /api/workspace-auth/logout`
- `GET /api/models`
- `GET /api/tts/voices`
- `GET /api/logs/tail?lines=200`
- `POST /api/analyze-characters`
- `POST /api/generate-novel-aliases`
- `POST /api/confirm-characters`
- `POST /api/segment-text`
- `GET /api/character-reference-images`
- `POST /api/character-reference-images/upload`
- `POST /api/character-reference-images/generate`
- `POST /api/bgm/upload`
- `POST /api/watermark/upload`
- `GET /api/bgm/library`
- `POST /api/bgm/select`
- `DELETE /api/bgm/current`
- `GET /api/bgm`
- `POST /api/generate-video`
- `POST /api/jobs/{job_id}/remix-bgm` (replace BGM only, no full regeneration)
- `POST /api/jobs/{job_id}/cancel`
- `POST /api/jobs/{job_id}/resume` (continue cancelled/failed/interrupted job from checkpoint)
- `DELETE /api/jobs/{job_id}` (hard delete job record + payload + cancel flag + `outputs/temp/{job_id}` + `outputs/{job_id}.mp4` if exists)
- `GET /api/jobs?limit=100` (list recent jobs from SQLite, used by frontend recovery/sync)
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/clips/{clip_index}`
- `GET /api/jobs/{job_id}/clips/{clip_index}/thumb` (on-demand cached clip thumbnail, JPG)
- `GET /api/jobs/{job_id}/video`
- `GET /api/final-videos?limit=200` (list final videos sorted by creation time desc)
- `GET /api/final-videos/{filename}/thumb` (on-demand cached final-video thumbnail)
- `GET /api/final-videos/{filename}/download`
- `DELETE /api/workspace/final-videos/{filename}` (workspace-protected delete; removes final video + final-video thumb; if stem matches an existing job id, job is rolled back to pre-compose state)

Character identity fields (`CharacterSuggestion`):

- `is_main_character` (bool): marks one main character
- `is_story_self` (bool): marks the first-person narrator role (if the novel perspective is first-person)
- These fields are accepted in `POST /api/confirm-characters` and in `POST /api/generate-video` within `characters[]`

## Frontend

### Start

```bash
cd frontend
npm install
npm run dev
```

Default: `http://localhost:5173`

Page paths (history mode):

- Workspace: `/workspace`
- Final videos library: `/final-videos`

Final videos library behavior:

- List is sorted by creation time (newest first)
- First load shows thumbnails only (no full video preload)
- Each card provides `View Video` and `Download` actions

Workspace draft behavior:

- Workspace form inputs (including text, character configs, and selected model) are auto-saved to browser local storage
- On next visit, the workspace auto-loads the latest single local draft (new changes overwrite old draft)

## Environment

See `.env.example`.

Workspace auth gate:

- `ADMIN_PASSWORD` (optional): when set, workspace APIs require header `x-workspace-password`
- Workspace login also sets an auth cookie for media tags (`img/video`) so clip thumbnails and video preview can load normally
- Public final videos endpoints remain open: `/api/final-videos/*`

Important paths:

- `OUTPUT_DIR`: final videos
- `TEMP_DIR`: per-job clip assets
- `CHARACTER_REF_DIR`: reusable character reference image library
- `SCENE_CACHE_DIR`: generated scene-image cache files
- `SCENE_CACHE_INDEX_PATH`: metadata index for scene-image reuse
- `SCENE_CACHE_DB_PATH`: sqlite storage for scene cache (reference-image bindings)
- `JOBS_DB_PATH`: sqlite storage for job status + payload (resume support)
- `JOB_CLIP_PREVIEW_LIMIT`: max clip preview URLs returned per job status
- `LOG_DIR`: backend log files

Request field (generate video):

- `enable_scene_image_reuse` (default `true`):
  - `true`: try cache matching first, generate only if no suitable match
  - `false`: always generate new image for each segment

Subtitle font:

- `SUBTITLE_FONT_PATH` (optional): explicit font file for subtitles
- If empty, backend tries common CJK fonts automatically (e.g. `msyh.ttc` on Windows)

BGM during final compose:

- Controlled by frontend request fields: `bgm_enabled` and `bgm_volume`
- Active BGM file: `assets/bgm.mp3`
- BGM library folder: `assets/bgm`
- You can upload BGM into library, select one as active, or delete current active BGM
- BGM is looped to match final video duration and mixed at low volume
- If BGM file is missing, compose will continue without BGM

## Notes

- If LLM/image/TTS API partially fails, fallback paths are used where possible.
- Backend exceptions are logged to `logs/backend.log` and can be viewed in frontend via `/api/logs/tail`.

## Docker Compose

### Start (recommended)

```bash
docker compose up --build
```

After startup:

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`

### Stop

```bash
docker compose down
```

### Notes

- `docker-compose.yml` reads env vars from `.env.local` for backend API keys/settings.
- These folders are mounted for persistence: `assets/`, `outputs/`, `logs/`.
- Backend image includes `ffmpeg` and `fonts-noto-cjk` for video/audio compose and CJK subtitles.
