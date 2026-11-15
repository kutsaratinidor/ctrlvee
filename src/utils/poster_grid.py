"""One JPEG of TMDB posters in a grid, for the /stats Recently Watched card."""
import io
import logging
import math
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger(__name__)

CELL_W, CELL_H, GAP = 200, 300, 6
BACKGROUND = (43, 45, 49)  # Discord's dark theme, so the gaps blend into the embed
PLACEHOLDER = (64, 66, 73)
DOWNLOAD_SIZE = "w342"  # Stored URLs are w500; a 200px cell doesn't need that much

# Poster bytes by URL. Pruned to the current grid on every render, so it never grows past it.
_posters: dict[str, bytes] = {}


def grid_shape(count: int) -> tuple[int, int]:
    """(columns, rows), as square as fits: 1 → 1x1, 2 → 2x1, 3-4 → 2x2, 5-6 → 3x2, 7-9 → 3x3."""
    cols = math.ceil(math.sqrt(count))
    return cols, math.ceil(count / cols)


def _fetch(url: str) -> bytes | None:
    if url not in _posters:
        try:
            response = requests.get(url.replace("/w500/", f"/{DOWNLOAD_SIZE}/"), timeout=5)
            response.raise_for_status()
            _posters[url] = response.content
        except requests.RequestException as e:
            logger.warning(f"Poster download failed for {url}: {e}")
            return None
    return _posters[url]


def _placeholder(title: str) -> Image.Image:
    tile = Image.new("RGB", (CELL_W, CELL_H), PLACEHOLDER)
    ImageDraw.Draw(tile).multiline_text(
        (CELL_W / 2, CELL_H / 2), textwrap.fill(title, 16), fill="white",
        font=ImageFont.load_default(20), anchor="mm", align="center",
    )
    return tile


def _badge(tile: Image.Image, number: int) -> None:
    """Number in the top-left corner, matching the list above the image."""
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle((6, 6, 40, 36), radius=8, fill=(0, 0, 0))
    draw.text((23, 21), str(number), fill="white", font=ImageFont.load_default(20), anchor="mm")


def render(tiles: list[tuple[str, str | None]]) -> tuple[bytes, bool]:
    """JPEG of (title, poster URL or None) tiles in order, and whether every poster loaded.

    A missing or broken poster becomes a grey tile with the title, so the numbering still lines up.
    """
    started = time.perf_counter()
    urls = [url for _, url in tiles if url]
    for stale in set(_posters) - set(urls):
        del _posters[stale]
    downloads = sum(url not in _posters for url in set(urls))
    with ThreadPoolExecutor(max_workers=max(1, len(urls))) as pool:
        fetched = dict(zip(urls, pool.map(_fetch, urls)))

    cols, rows = grid_shape(len(tiles))
    grid = Image.new("RGB", (cols * CELL_W + (cols + 1) * GAP, rows * CELL_H + (rows + 1) * GAP), BACKGROUND)
    complete = True
    for i, (title, url) in enumerate(tiles):
        tile = None
        if fetched.get(url):
            try:
                tile = ImageOps.fit(Image.open(io.BytesIO(fetched[url])).convert("RGB"), (CELL_W, CELL_H))
            except OSError as e:
                logger.warning(f"Unreadable poster {url}: {e}")
                _posters.pop(url, None)
        if tile is None:
            tile = _placeholder(title)
            complete = False
        _badge(tile, i + 1)
        grid.paste(tile, (GAP + (i % cols) * (CELL_W + GAP), GAP + (i // cols) * (CELL_H + GAP)))

    out = io.BytesIO()
    grid.save(out, "JPEG", quality=85)
    logger.info(
        f"Poster grid {cols}x{rows} rendered in {(time.perf_counter() - started) * 1000:.0f} ms "
        f"({downloads} downloaded, {out.tell() // 1024} KB)"
    )
    return out.getvalue(), complete
