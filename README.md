# GenVideo

Novel-to-video generation system with full backend + frontend implementation.

## Features

- Dynamic LLM model list (`/api/models`)
- AI character analysis + editable character config
- Text segmentation (`sentence` / `fixed` / `smart`)
- Video generation pipeline (image + TTS + subtitles)
- Job polling and video download
- Cost-aware image grouping: multiple segments can reuse one image

## Project Structure

- `backend/` FastAPI + MoviePy services
- `frontend/` Vue 3 + Element Plus + Vite
- `plans/` planning docs

## Requirements

1. Python 3.11+
2. Node.js 18+
3. FFmpeg (required by MoviePy)

## Environment Config

Copy `.env.example` to `.env` (or use `.env.local`).

Required:

- `LLM_API_KEY`
- `LLM_API_BASE_URL`
- `IMAGE_API_KEY`
- `IMAGE_API_URL`

Optional:

- `LLM_DEFAULT_MODEL`
- `IMAGE_MODEL`
- `TTS_API_URL` (if empty, fallback to `edge-tts`)

### Segment/Image Rules

- `max_segments` (request/frontend): max rendered segments per job.
  - `0` means process all segments.
  - Example: if segmentation returns 50 and `max_segments=20`, only first 20 are rendered.
- `segments_per_image` (request/frontend): how many segments share one generated image.
  - Default: `5`
  - Example: 50 segments with `segments_per_image=5` => about 10 generated images.

## Run Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Backend default: `http://localhost:8000`

## Run Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend default: `http://localhost:5173`

## Main APIs

- `GET /api/health`
- `GET /api/models`
- `POST /api/analyze-characters`
- `POST /api/confirm-characters`
- `POST /api/segment-text`
- `POST /api/generate-video`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/video`

### `POST /api/generate-video` key fields

- `text`
- `characters`
- `segment_method`
- `max_segments` (default 0 = all)
- `segments_per_image` (default 5)
- `resolution`
- `subtitle_style`
- `fps`
- `model_id`

## Notes

- If image API/TTS fails, backend uses fallback strategies (placeholder image/silent audio) so flow remains testable.
- Output files are under `outputs/`.
