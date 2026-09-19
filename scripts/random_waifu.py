#!/usr/bin/env python3
"""Download a random SFW portrait from Waifu.im and build assets/random-waifu.svg.

Run: python scripts/random_waifu.py
Requires Pillow. README.md is never modified.
API documentation: https://docs.waifu.im/docs/getting-started/
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import random
import sys
import textwrap
import time
from datetime import date, datetime, timedelta, timezone
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

API_URL = "https://api.waifu.im/images"
MAX_BYTES = 20 * 1024 * 1024


def request_bytes(url: str) -> bytes:
    last_error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={
                "User-Agent": "GitHubProfileRandomWaifu/3.0", "Accept": "*/*"})
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Response exceeds 20 MB")
            return data
        except (OSError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Request failed: {url}: {last_error}")


def eligible(art: dict[str, Any], min_size: int) -> bool:
    try:
        width, height = int(art.get("width", 0)), int(art.get("height", 0))
    except (TypeError, ValueError):
        return False
    return (art.get("isNsfw") is False and not art.get("isAnimated", False)
            and height > width >= min_size
            and any(tag.get("slug") == "waifu" for tag in art.get("tags", [])))


def decode_portrait(data: bytes, min_size: int) -> tuple[bytes, int, int]:
    """Validate real pixels and encode a predictable, reasonably sized JPEG."""
    with Image.open(BytesIO(data)) as image:
        image.load()
        width, height = image.size
        if height <= width or width < min_size:
            raise ValueError(f"Image is not a sufficiently large portrait: {width}x{height}")
        image.thumbnail((1600, 2200))
        background = Image.new("RGB", image.size, "#0d1117")
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.getchannel("A"))
        output = BytesIO()
        background.save(output, "JPEG", quality=92, optimize=True)
    return output.getvalue(), width, height


def select_portrait(attempts: int, min_size: int) -> tuple[dict[str, Any], bytes]:
    seen = set()
    last_error = "No suitable portraits returned"
    for attempt in range(attempts):
        params = urllib.parse.urlencode({"IncludedTags": "waifu", "IsNsfw": "False", "PageSize": 10})
        try:
            items = json.loads(request_bytes(API_URL + "?" + params))["items"]
            candidates = [art for art in items if eligible(art, min_size)]
            random.shuffle(candidates)
            # Prefer popular work within the randomly sampled batch.
            candidates.sort(key=lambda art: int(art.get("favorites") or 0), reverse=True)
            for art in candidates:
                if art["url"] in seen:
                    continue
                seen.add(art["url"])
                try:
                    data, width, height = decode_portrait(request_bytes(art["url"]), min_size)
                    return art | {"width": width, "height": height}, data
                except (OSError, ValueError, RuntimeError) as exc:
                    last_error = str(exc)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            last_error = str(exc)
        print(f"Retry {attempt + 1}/{attempts}: {last_error}", file=sys.stderr)
    raise RuntimeError(f"Could not download a portrait: {last_error}")


QUOTE_API_URL = "https://zenquotes.io/api/random"


def fetch_quote(previous: str = "") -> dict[str, str]:
    """Fetch a new attributed quote; reject duplicate, malformed and oversized entries."""
    for _ in range(3):
        payload = json.loads(request_bytes(QUOTE_API_URL))
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise RuntimeError("Invalid quote API response")
        entry = payload[0]
        if not isinstance(entry.get("q"), str) or not isinstance(entry.get("a"), str):
            raise RuntimeError("Quote API returned no text or author")
        quote = " ".join(html.unescape(entry["q"]).split())
        author = " ".join(html.unescape(entry["a"]).split())
        if (quote and author and author.casefold() != "zenquotes.io"
                and quote != previous and len(quote) <= 240 and len(author) <= 64
                and len(textwrap.wrap(quote, width=33)) <= 8):
            return {"text": quote, "author": author, "provider": "ZenQuotes"}
    raise RuntimeError("Quote API did not return a new quote that fits the card; try again later")


def generate_svg(art: dict[str, Any], image_path: Path, quote: dict[str, str], day: date | None = None) -> str:
    """Pair the portrait with an API quote and its supplied author."""
    day = day or datetime.now(timezone(timedelta(hours=5))).date()
    note = quote["text"]
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif"}[image_path.suffix.lower()]
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    wrapped = textwrap.wrap(note, width=33)
    first_y = 300 - (len(wrapped) - 1) * 14
    quote_svg = "".join(
        f'<text x="464" y="{first_y + i * 28}" font-size="19" fill="#e6edf3">{html.escape(line)}</text>'
        for i, line in enumerate(wrapped))
    author_svg = "".join(
        f'<text x="464" y="{481 + i * 20}" font-size="14" fill="#91bfff">{html.escape(line)}</text>'
        for i, line in enumerate(textwrap.wrap("— " + quote["author"], width=40)))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
 width="900" height="620" viewBox="0 0 900 620" role="img" aria-labelledby="title description">
 <title id="title">A little pause — {day.isoformat()}</title>
 <desc id="description">{html.escape(note)}</desc>
 <defs><clipPath id="art"><rect x="20" y="64" width="404" height="536" rx="12"/></clipPath></defs>
 <rect x="1" y="1" width="898" height="618" rx="16" fill="#0d1117" stroke="#30363d" stroke-width="2"/>
 <path d="M17 1H883Q899 1 899 17V43H1V17Q1 1 17 1" fill="#161b22"/>
 <g font-family="'Courier New', monospace">
 <circle cx="23" cy="22" r="5" fill="#f85149"/>
 <circle cx="41" cy="22" r="5" fill="#d29922"/>
 <circle cx="59" cy="22" r="5" fill="#3fb950"/>
 <text x="80" y="27" font-size="12" fill="#8b949e">a little pause</text>
 <image x="20" y="64" width="404" height="536" preserveAspectRatio="xMidYMin slice"
 clip-path="url(#art)" xlink:href="data:{mime};base64,{image_data}"/>
 <rect x="464" y="100" width="32" height="3" rx="1.5" fill="#f2a7cd"/>
 <text x="464" y="130" font-size="12" fill="#f2a7cd" letter-spacing="2">A MOMENT OF INSPIRATION</text>
 <text x="457" y="218" font-family="Georgia, serif" font-size="76" fill="#303044">“</text>
 {quote_svg}
 <path d="M464 451H500" stroke="#91bfff" stroke-width="2"/>
 {author_svg}
 <path d="M464 550H864" stroke="#21262d"/>
 <text x="464" y="578" font-size="12" fill="#8b949e">{day.strftime('%d.%m.%Y')}</text>
 <a xlink:href="https://zenquotes.io/"><text x="864" y="578" text-anchor="end" font-size="11" fill="#8b949e">Quotes by ZenQuotes ↗</text></a>
 </g>
</svg>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-dir", type=Path, default=Path("assets"))
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--min-art-size", type=int, default=700)
    parser.add_argument("--max-pages", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.attempts < 1 or args.min_art_size < 1:
        parser.error("--attempts and --min-art-size must be positive")
    if args.max_pages is not None:
        print("--max-pages is no longer used: images are selected directly.")
    metadata_path = args.assets_dir / "random-waifu.json"
    previous = ""
    if metadata_path.exists():
        try:
            previous = json.loads(metadata_path.read_text(encoding="utf-8")).get("quote", {}).get("text", "")
        except (ValueError, AttributeError):
            pass
    quote = fetch_quote(previous)
    art, data = select_portrait(args.attempts, args.min_art_size)
    art["quote"] = quote
    args.assets_dir.mkdir(parents=True, exist_ok=True)
    image_path = args.assets_dir / "random-waifu.jpg"
    temporary = args.assets_dir / "random-waifu.tmp.jpg"
    temporary.write_bytes(data)
    try:
        svg = generate_svg(art, temporary, quote)
        temporary.replace(image_path)
    finally:
        temporary.unlink(missing_ok=True)
    svg_path = args.assets_dir / "random-waifu.svg"
    temporary_svg = svg_path.with_suffix(".tmp.svg")
    temporary_svg.write_text(svg, encoding="utf-8")
    temporary_svg.replace(svg_path)
    (args.assets_dir / "random-waifu.json").write_text(
        json.dumps(art, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for suffix in (".png", ".webp", ".gif", ".jpeg"):
        (args.assets_dir / ("random-waifu" + suffix)).unlink(missing_ok=True)
    print(f"Downloaded Waifu.im #{art['id']}: {art['width']}x{art['height']}")
    print(f"SVG: {svg_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
