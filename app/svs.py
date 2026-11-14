"""NASA Scientific Visualization Studio (SVS) API client.

SVS exposes JSON by prefixing any page URL with /api:
  search: https://svs.gsfc.nasa.gov/api/search/?search=<term>&limit=<n>
  item:   https://svs.gsfc.nasa.gov/api/<id>
The API's CORS header only allows one foreign origin, so the browser cannot call
it directly; this server proxies it. SVS pages also forbid iframing
(X-Frame-Options: DENY), so the UI builds its own card from this data.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from typing import Any

import httpx

BASE = "https://svs.gsfc.nasa.gov"
HEADERS = {"User-Agent": "EarthSpins/0.1 (NASA Space Apps 2026)"}
MAX_IMAGE_BYTES = 15 * 1024 * 1024
CACHE_TTL = 3600

_client: httpx.AsyncClient | None = None
_cache: dict[str, tuple[float, Any]] = {}


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=BASE, headers=HEADERS, timeout=30, follow_redirects=True)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _cached(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    return None


def _https(url: str | None) -> str | None:
    if url and url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def clean_text(value: str | None, limit: int | None = None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def _names(items: Any) -> list[str]:
    out: list[str] = []
    for item in items or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("title") or item.get("name") or item.get("keyword")
            if name:
                out.append(str(name))
    return out


async def _search_raw(params: dict[str, Any], eic: bool, limit: int = 25) -> list[dict[str, Any]]:
    resp = await client().get("/api/search/", params={**params, "limit": limit})
    resp.raise_for_status()
    rows = []
    for row in resp.json().get("results", []):
        if not row.get("id") or not row.get("title"):
            continue
        rows.append({
            "id": int(row["id"]),
            "title": clean_text(row["title"]),
            "type": row.get("result_type"),
            "release_date": (row.get("release_date") or "")[:10],
            "description": clean_text(row.get("description"), 240),
            "eic": eic,
        })
    # Prefer data visualizations (what the challenge is about), keep SVS order otherwise.
    rows.sort(key=lambda r: 0 if r["type"] == "Visualization" else 1)
    return rows


EIC_LIMIT = 100      # all Earth Information Center pages for a term (EIC has ~100 in total)
GENERAL_LIMIT = 60   # general SVS results appended after them (10 pages of 6)


async def _merged(term: str) -> list[dict[str, Any]]:
    """Earth Information Center (made_for=EIC) results first, then general SVS results.

    An empty term returns the latest EIC visualizations only.
    """
    term = term.strip()
    key = f"merged:{term.lower()}"
    if (hit := _cached(key)) is not None:
        return hit
    params: dict[str, Any] = {"made_for": "EIC"}
    if term:
        params["search"] = term
        eic, general = await asyncio.gather(
            _search_raw(params, eic=True, limit=EIC_LIMIT),
            _search_raw({"search": term}, eic=False, limit=GENERAL_LIMIT),
        )
        seen = {r["id"] for r in eic}
        results = eic + [r for r in general if r["id"] not in seen]
    else:
        results = await _search_raw(params, eic=True, limit=EIC_LIMIT)
    _cache[key] = (time.time(), results)
    return results


async def search(term: str, page: int = 0, per_page: int = 6) -> dict[str, Any]:
    results = await _merged(term)
    pages = max(1, -(-len(results) // per_page))
    page = max(0, min(page, pages - 1))
    return {
        "page": page,
        "pages": pages,
        "total": len(results),
        "results": results[page * per_page:(page + 1) * per_page],
    }


def _media(data: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    images: list[dict] = []
    videos: list[dict] = []

    def add(inst: dict | None) -> None:
        if not inst or not inst.get("url"):
            return
        kind = inst.get("media_type")
        url = inst["url"].lower()
        if kind == "Image" and url.endswith((".png", ".jpg", ".jpeg", ".webp")):
            images.append(inst)
        elif kind in {"Movie", "Video"} and url.endswith(".mp4"):
            videos.append(inst)

    add(data.get("main_image"))
    add(data.get("main_video"))
    for group in data.get("media_groups") or []:
        for item in group.get("items") or []:
            add(item.get("instance"))
    return images, videos


def _pick_video(videos: list[dict]) -> dict | None:
    if not videos:
        return None
    reasonable = [v for v in videos if (v.get("pixels") or 0) <= 1920 * 1080]
    pool = reasonable or videos
    return max(pool, key=lambda v: v.get("pixels") or 0) if reasonable else min(pool, key=lambda v: v.get("pixels") or 0)


async def get_item(svs_id: int) -> dict[str, Any]:
    key = f"item:{svs_id}"
    if (hit := _cached(key)) is not None:
        return hit
    resp = await client().get(f"/api/{svs_id}")
    resp.raise_for_status()
    data = resp.json()
    images, videos = _media(data)
    video = _pick_video(videos)

    captions = []
    for group in data.get("media_groups") or []:
        text = clean_text(group.get("caption")) or clean_text(group.get("description"))
        if text:
            captions.append(text)
    item = {
        "id": int(data.get("id", svs_id)),
        "title": clean_text(data.get("title")),
        "url": _https(data.get("url")) or f"{BASE}/{svs_id}/",
        "page_type": data.get("page_type"),
        "release_date": (data.get("release_date") or "")[:10],
        "description": clean_text(data.get("description")),
        "image_url": _https(images[0]["url"]) if images else None,
        "image_candidates": [_https(i["url"]) for i in images[:6]],
        "video_url": _https(video["url"]) if video else None,
        "keywords": _names(data.get("keywords"))[:12],
        "missions": _names(data.get("missions"))[:8],
        "captions": captions[:4],
    }
    _cache[key] = (time.time(), item)
    return item


async def fetch_image(item: dict[str, Any]) -> tuple[bytes, str] | None:
    """Download the first usable visualization frame for Gemini (server side, no CORS)."""
    for url in item.get("image_candidates") or []:
        try:
            async with client().stream("GET", url) as resp:
                if resp.status_code != 200:
                    continue
                size = int(resp.headers.get("content-length") or 0)
                if size > MAX_IMAGE_BYTES:
                    continue
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_IMAGE_BYTES:
                        break
                if len(body) > MAX_IMAGE_BYTES:
                    continue
                mime = resp.headers.get("content-type", "").split(";")[0] or "image/png"
                return bytes(body), mime
        except httpx.HTTPError:
            continue
    return None
