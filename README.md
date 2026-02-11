# GenVideo

Novel-to-video generation system (FastAPI + Vue 3), updated for the current workflow:

- Better backend logging and error visibility
- Cancelable video jobs
- Segment-clip preview before final video
- Sentence-based grouping semantics
- Character reference-image library (select/upload/generate)
- Per-character TTS voice selection from real voice list

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
- One segment group generates one clip
- One segment group generates one image prompt call (reference image is passed when available)
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
- `POST /api/generate-video`
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
- `LOG_DIR`: backend log files

## Notes

- If LLM/image/TTS API partially fails, fallback paths are used where possible.
- Backend exceptions are logged to `logs/backend.log` and can be viewed in frontend via `/api/logs/tail`.

