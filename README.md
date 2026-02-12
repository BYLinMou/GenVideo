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

## Workflow (Current)

1. Input novel text
2. Segment preview (sentence-aware)
3. Analyze characters
4. Confirm characters (voice + reference image)
5. Generate clips and final video
6. Preview each clip and download final video

## Core Semantics (Important)

- Sentence splitting supports punctuation: `。！？!?；;，,`
- `sentences_per_segment = N` means **N sentences per segment group**
- Recommended default is `sentences_per_segment = 1` for sentence-by-sentence pacing
- One segment group generates one clip
- One segment group generates one image prompt call (reference image is passed when available)
- Before generating a new segment image, backend checks reusable cached images by structured text descriptor
- Cache matching is text-only (character/action/scene descriptors), no image input required
- `max_segment_groups = 0` means process all groups
- `max_segment_groups = 2` with `sentences_per_segment = 5` means process up to `2 * 5 = 10` sentences

## Backend

### Start

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Default: `http://localhost:8000`

### Key APIs

- `GET /api/health`
- `GET /api/models`
- `GET /api/tts/voices`
- `GET /api/logs/tail?lines=200`
- `POST /api/analyze-characters`
- `POST /api/confirm-characters`
- `POST /api/segment-text`
- `GET /api/character-reference-images`
- `POST /api/character-reference-images/upload`
- `POST /api/character-reference-images/generate`
- `POST /api/bgm/upload`
- `GET /api/bgm`
- `POST /api/generate-video`
- `POST /api/jobs/{job_id}/remix-bgm` (replace BGM only, no full regeneration)
- `POST /api/jobs/{job_id}/cancel`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/clips/{clip_index}`
- `GET /api/jobs/{job_id}/video`

## Frontend

### Start

```bash
cd frontend
npm install
npm run dev
```

Default: `http://localhost:5173`

## Environment

See `.env.example`.

Important paths:

- `OUTPUT_DIR`: final videos
- `TEMP_DIR`: per-job clip assets
- `CHARACTER_REF_DIR`: reusable character reference image library
- `SCENE_CACHE_DIR`: generated scene-image cache files
- `SCENE_CACHE_INDEX_PATH`: metadata index for scene-image reuse
- `SCENE_CACHE_DB_PATH`: sqlite storage for scene cache (reference-image bindings)
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
