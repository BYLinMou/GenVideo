from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image


MET_SEARCH_API = "https://collectionapi.metmuseum.org/public/collection/v1/search"
MET_OBJECT_API = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{object_id}"
DEFAULT_HEADERS = {
    "User-Agent": "GenVideo-AssetFetcher/1.0 (+local-script)",
    "Accept": "application/json,text/plain,*/*",
}

CN_CHARACTER_PRESETS: dict[str, list[str]] = {
    "cn-character": [
        "chinese portrait",
        "hanfu portrait",
        "chinese warrior",
        "wuxia swordsman",
        "ancient chinese lady",
        "chinese opera costume",
    ],
    "cn-female": [
        "hanfu woman portrait",
        "ancient chinese lady",
        "chinese court lady",
        "xianxia heroine",
    ],
    "cn-male": [
        "hanfu man portrait",
        "chinese scholar portrait",
        "wuxia hero",
        "ancient chinese warrior",
    ],
}

PERSON_TERMS = {
    "portrait",
    "person",
    "figure",
    "man",
    "woman",
    "lady",
    "gentleman",
    "scholar",
    "warrior",
    "actor",
    "goddess",
    "bodhisattva",
    "emperor",
    "empress",
    "queen",
    "king",
    "prince",
    "princess",
}

CHINESE_STYLE_TERMS = {
    "china",
    "chinese",
    "han",
    "ming",
    "qing",
    "song",
    "tang",
    "yuan",
    "manchu",
    "beijing",
    "peking",
    "hanfu",
    "daoist",
    "taoist",
}

EUROPEAN_STYLE_TERMS = {
    "europe",
    "european",
    "italy",
    "italian",
    "france",
    "french",
    "germany",
    "german",
    "spain",
    "spanish",
    "england",
    "english",
    "british",
    "netherlands",
    "dutch",
    "belgium",
    "austria",
    "hungary",
    "russia",
    "russian",
    "greek",
    "rome",
    "roman",
    "renaissance",
    "baroque",
    "rococo",
    "victorian",
}


@dataclass
class MetObject:
    object_id: int
    title: str
    artist: str
    object_date: str
    object_url: str
    image_url: str


def _to_text(*values: object) -> str:
    chunks: list[str] = []
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            chunks.append(value)
            continue
        chunks.append(str(value))
    return " ".join(chunks).lower()


def _has_any_term(text: str, terms: set[str]) -> bool:
    for term in terms:
        if term in text:
            return True
    return False


def _is_person_focused(payload: dict) -> bool:
    tags = payload.get("tags") or []
    tag_text = " ".join(
        str(item.get("term") or "") for item in tags if isinstance(item, dict)
    )
    combined = _to_text(
        payload.get("title"),
        payload.get("objectName"),
        payload.get("classification"),
        payload.get("culture"),
        payload.get("artistDisplayName"),
        tag_text,
    )
    return _has_any_term(combined, PERSON_TERMS)


def _is_chinese_style(payload: dict) -> bool:
    tags = payload.get("tags") or []
    tag_text = " ".join(
        str(item.get("term") or "") for item in tags if isinstance(item, dict)
    )
    combined = _to_text(
        payload.get("title"),
        payload.get("objectName"),
        payload.get("classification"),
        payload.get("culture"),
        payload.get("period"),
        payload.get("dynasty"),
        payload.get("reign"),
        payload.get("artistNationality"),
        tag_text,
    )
    return _has_any_term(combined, CHINESE_STYLE_TERMS)


def _is_european_style(payload: dict) -> bool:
    tags = payload.get("tags") or []
    tag_text = " ".join(
        str(item.get("term") or "") for item in tags if isinstance(item, dict)
    )
    combined = _to_text(
        payload.get("title"),
        payload.get("objectName"),
        payload.get("classification"),
        payload.get("culture"),
        payload.get("period"),
        payload.get("dynasty"),
        payload.get("reign"),
        payload.get("artistNationality"),
        payload.get("artistDisplayName"),
        tag_text,
    )
    return _has_any_term(combined, EUROPEAN_STYLE_TERMS)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", value).strip("-") or "query"


def _default_output_dir() -> Path:
    return _repo_root() / "assets" / "character_refs" / "free_refs"


def _default_index_path(output_dir: Path) -> Path:
    return output_dir / "_source_index.json"


def _load_index(path: Path) -> dict:
    if not path.exists():
        return {"entries": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": []}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return {"entries": []}
    return {"entries": entries}


def _save_index(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def _search_met_ids(client: httpx.AsyncClient, query: str) -> list[int]:
    response = await client.get(MET_SEARCH_API, params={"q": query, "hasImages": "true"}, timeout=30)
    if response.status_code == 403:
        # Some edges throttle anonymous traffic. Brief backoff + one retry.
        time.sleep(0.6)
        response = await client.get(MET_SEARCH_API, params={"q": query, "hasImages": "true"}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    object_ids = payload.get("objectIDs") or []
    if not isinstance(object_ids, list):
        return []
    return [int(item) for item in object_ids if isinstance(item, int)]


async def _fetch_met_object(
    client: httpx.AsyncClient,
    object_id: int,
    person_only: bool,
    chinese_style_only: bool,
    exclude_european_style: bool,
) -> MetObject | None:
    response = await client.get(MET_OBJECT_API.format(object_id=object_id), timeout=30)
    response.raise_for_status()
    payload = response.json()

    if not payload.get("isPublicDomain"):
        return None

    if person_only and not _is_person_focused(payload):
        return None

    if chinese_style_only and not _is_chinese_style(payload):
        return None

    if exclude_european_style and _is_european_style(payload):
        return None

    image_url = str(payload.get("primaryImage") or payload.get("primaryImageSmall") or "").strip()
    if not image_url:
        return None

    return MetObject(
        object_id=object_id,
        title=str(payload.get("title") or "").strip(),
        artist=str(payload.get("artistDisplayName") or "").strip(),
        object_date=str(payload.get("objectDate") or "").strip(),
        object_url=str(payload.get("objectURL") or "").strip(),
        image_url=image_url,
    )


def _image_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(image_bytes)) as image:
        return image.width, image.height


async def _download_image(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url, timeout=60)
    response.raise_for_status()
    return response.content


async def run(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = Path(args.index_path).resolve()

    index_payload = _load_index(index_path)
    existing_entries = [item for item in index_payload.get("entries", []) if isinstance(item, dict)]
    existing_key_set = {
        f"{item.get('provider','')}:{item.get('provider_id','')}"
        for item in existing_entries
        if item.get("provider") and item.get("provider_id")
    }

    new_entries: list[dict] = []
    total_saved = 0

    queries = list(args.query)
    if args.preset in CN_CHARACTER_PRESETS:
        queries.extend(CN_CHARACTER_PRESETS[args.preset])

    # Keep order and deduplicate
    dedup_queries: list[str] = []
    seen_queries: set[str] = set()
    for raw in queries:
        key = raw.strip().lower()
        if not key or key in seen_queries:
            continue
        seen_queries.add(key)
        dedup_queries.append(raw.strip())

    async with httpx.AsyncClient(follow_redirects=True, headers=DEFAULT_HEADERS) as client:
        for query in dedup_queries:
            query = query.strip()
            if not query:
                continue

            print(f"\n[MET] Searching query: {query}")
            try:
                object_ids = await _search_met_ids(client, query)
            except Exception as error:
                print(f"  ! Search failed: {error}")
                continue

            if not object_ids:
                print("  - No results")
                continue

            saved_for_query = 0
            checked_count = 0
            for object_id in object_ids:
                if saved_for_query >= args.limit_per_query:
                    break
                if checked_count >= args.max_candidates_per_query:
                    break
                checked_count += 1

                key = f"met:{object_id}"
                if key in existing_key_set:
                    continue

                try:
                    met_object = await _fetch_met_object(
                        client,
                        object_id,
                        person_only=args.person_only,
                        chinese_style_only=args.chinese_style_only,
                        exclude_european_style=args.exclude_european_style,
                    )
                except Exception as error:
                    print(f"  ! Object {object_id} fetch failed: {error}")
                    continue

                if not met_object:
                    continue

                try:
                    image_bytes = await _download_image(client, met_object.image_url)
                    width, height = _image_size(image_bytes)
                except Exception as error:
                    print(f"  ! Object {object_id} image failed: {error}")
                    continue

                if width < args.min_width or height < args.min_height:
                    continue

                filename = f"met_{object_id}_{_slugify(query)}.jpg"
                target = output_dir / filename

                if args.dry_run:
                    print(
                        f"  - [dry-run] {filename} | {width}x{height} | {met_object.title or 'Untitled'}"
                    )
                    saved_for_query += 1
                    total_saved += 1
                    continue

                target.write_bytes(image_bytes)

                entry = {
                    "provider": "met",
                    "provider_id": str(object_id),
                    "query": query,
                    "file": target.as_posix(),
                    "filename": filename,
                    "width": width,
                    "height": height,
                    "title": met_object.title,
                    "artist": met_object.artist,
                    "object_date": met_object.object_date,
                    "source_image_url": met_object.image_url,
                    "source_page_url": met_object.object_url,
                    "license": "CC0 (The Met Open Access)",
                    "license_url": "https://www.metmuseum.org/hubs/open-access",
                    "filters": {
                        "person_only": bool(args.person_only),
                        "chinese_style_only": bool(args.chinese_style_only),
                        "exclude_european_style": bool(args.exclude_european_style),
                    },
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                }
                new_entries.append(entry)
                existing_key_set.add(key)

                print(f"  + Saved: {filename} ({width}x{height})")
                saved_for_query += 1
                total_saved += 1

            print(f"  = Saved for query '{query}': {saved_for_query}")

    if args.dry_run:
        print(f"\nDone (dry-run). Candidate images matched: {total_saved}")
        return

    if new_entries:
        index_payload["entries"] = existing_entries + new_entries
        _save_index(index_path, index_payload)
        print(f"\nDone. Downloaded {total_saved} images.")
        print(f"Metadata index updated: {index_path}")
    else:
        print("\nDone. No new images downloaded.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download commercial-friendly character reference images (CC0) into assets.",
    )
    parser.add_argument(
        "--query",
        nargs="+",
        default=[],
        help="Search keywords, e.g. --query 古风女侠 黑发剑客",
    )
    parser.add_argument(
        "--preset",
        choices=["none", "cn-character", "cn-female", "cn-male"],
        default="cn-character",
        help="Quick query preset for Chinese-audience-friendly character references.",
    )
    parser.add_argument(
        "--limit-per-query",
        type=int,
        default=6,
        help="How many images to save for each query.",
    )
    parser.add_argument(
        "--max-candidates-per-query",
        type=int,
        default=120,
        help="How many candidate objects to scan before stopping.",
    )
    parser.add_argument(
        "--min-width",
        type=int,
        default=512,
        help="Minimum accepted image width.",
    )
    parser.add_argument(
        "--min-height",
        type=int,
        default=512,
        help="Minimum accepted image height.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_default_output_dir()),
        help="Output image directory.",
    )
    parser.add_argument(
        "--index-path",
        default="",
        help="Metadata JSON path. Default: <output-dir>/_source_index.json",
    )
    parser.add_argument(
        "--person-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep only person/portrait-like references.",
    )
    parser.add_argument(
        "--chinese-style-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep only Chinese-style related references (culture/period/tag text match).",
    )
    parser.add_argument(
        "--exclude-european-style",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exclude references with strong European-style keywords.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only discover candidates, do not save files.",
    )
    return parser


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = build_arg_parser()
    args = parser.parse_args()

    if not args.query and args.preset == "none":
        parser.error("Please provide --query, or use a preset other than 'none'.")

    if not args.index_path:
        args.index_path = str(_default_index_path(Path(args.output_dir).resolve()))

    import asyncio

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
