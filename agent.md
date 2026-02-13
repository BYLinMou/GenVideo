# GenVideo Agent Notes (Current)

This file documents the **actual current behavior** of this repo.
Legacy init/planning prompts have been removed.

## 1) Project Overview

- Stack:
  - Backend: FastAPI (`backend/app`)
  - Frontend: Vue 3 + Vite (`frontend/src`)
- Goal: Convert novel text into segmented short clips, then compose final video.
- Main flow:
  1. Segment text
  2. Analyze/confirm characters
  3. Generate per-segment image + TTS + subtitle clip
  4. Compose final video (with optional BGM)

## 2) Important Current Rules

### 2.1 Sentence splitting / segmentation

- Core splitter: `backend/app/services/llm_service.py` -> `split_sentences()`
- Current intent:
  - Comma participates in sentence split (`，`, `,`)
  - Preserve punctuation clusters sanely (avoid splitting `，。】` into garbage fragments)
  - Keep closing marks attached when needed (`】`, `」`, `”`, etc.)
  - Remove heading lines like `#1（5 句）` before splitting

### 2.2 Scene image reuse

- Controlled by request fields:
  - `enable_scene_image_reuse`
  - `scene_reuse_no_repeat_window` (default 3)
- No-repeat semantics:
  - Current scene cannot reuse entries used by previous N scenes (`current-1..current-N`)
- Reuse matching is text-first and conservative:
  - Character/action/location must align
  - Weak candidates are skipped early
  - If uncertain, do not reuse

### 2.3 Image metadata policy

- Assume LLM cannot read generated images.
- Prompt + scene metadata are produced **at generation time** in one pass.
- No post-generation image analysis backfill for metadata.

### 2.4 Voice/TTS policy

- Narrator voice: `zh-CN-YunxiNeural`
- Character voices are sanitized to avoid narrator collision and reduce duplicates.
- Dialogue routing is strict rule-based:
  - Only text inside double-quote style dialogue (`"..."` / `“...”`) uses character voices
  - All non-dialogue text is narrator voice
  - No semantic hint inference required for narrator/character decision

## 3) Video Rendering / Performance

- Render mode request field: `render_mode`
  - `fast`, `balanced`, `quality`
- Current default: `balanced`
- Fast compose path:
  - Prefer ffmpeg concat for final merge
  - With BGM in fast/balanced path, prefer video stream copy when possible
- Audio defaults:
  - BGM default volume: `0.08`
  - TTS gain in clip rendering: `1.15`

## 4) Key Files

- Backend main API: `backend/app/main.py`
- Data models: `backend/app/models.py`
- LLM + segmentation: `backend/app/services/llm_service.py`
- Video pipeline: `backend/app/services/video_service.py`
- Scene cache: `backend/app/services/scene_cache_service.py`
- TTS service: `backend/app/services/tts_service.py`
- Voice catalog: `backend/app/voice_catalog.py`
- Frontend page: `frontend/src/App.vue`
- Frontend i18n: `frontend/src/i18n/zh-CN.js`

## 5) Runtime Notes

- Frontend dev server proxies `/api` to `http://localhost:8000`.
- If Vite shows `ECONNREFUSED` on `/api/*`, backend is not reachable on 8000.
- After backend rule changes, restart backend before validating from frontend.

## 6) Editing Conventions for This Repo

- Prefer minimal, targeted changes.
- Keep frontend strings in i18n when possible.
- All new frontend user-facing text must use i18n keys (no hardcoded UI strings).
- Use English for all Git commit messages/titles.
- Use conservative fallback behavior:
  - If uncertain in reuse/match logic, choose safer non-reuse path.
- Do not reintroduce legacy planning/init prompt content into this file.
