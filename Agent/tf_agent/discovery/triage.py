"""The look check: does a candidate look like the owner's references? One model call compares a sheet of the
references' thumbnails with a numbered sheet of up to 20 candidates' thumbnails, the way a person glances at a
grid of covers. Captions often aren't available (Instagram profile grids), but a cover always is."""
from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from tf_agent.tools.types import VideoItem

log = logging.getLogger(__name__)
BATCH = 20
CELL = (180, 320)  # 9:16, enough to read the scene, small enough for 20 to a sheet
COLS = 5
MAX_REFERENCES = 15
Fetch = Callable[[str], Awaitable[bytes | None]]


@dataclass(frozen=True)
class Look:
    score: int  # 0-10: 10 = the same kind of video as the references
    why: str


async def fetch_cover(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url, headers={"user-agent": "Mozilla/5.0"})
        return r.content if r.status_code == 200 and r.content else None
    except httpx.HTTPError:
        return None


def cover_url(i: VideoItem) -> str | None:
    if i.thumbnail_url:
        return i.thumbnail_url
    if i.platform == "youtube":
        return f"https://i.ytimg.com/vi/{i.canonical_id.split(':', 1)[1]}/hqdefault.jpg"
    return None


def make_sheet(images: Sequence[bytes], labels: Sequence[str], out: Path) -> Path:
    """A grid of covers, each with its label in a box at the top left."""
    rows = max(1, -(-len(images) // COLS))
    sheet = Image.new("RGB", (COLS * CELL[0], rows * CELL[1]), "white")
    font = ImageFont.load_default(size=30)
    for n, (data, label) in enumerate(zip(images, labels, strict=True)):
        try:
            with Image.open(io.BytesIO(data)) as im:
                cover = im.convert("RGB")
        except Exception:
            cover = Image.new("RGB", CELL, "grey")
        cover.thumbnail(CELL)
        x, y = (n % COLS) * CELL[0], (n // COLS) * CELL[1]
        sheet.paste(cover, (x + (CELL[0] - cover.width) // 2, y + (CELL[1] - cover.height) // 2))
        draw = ImageDraw.Draw(sheet)
        box = draw.textbbox((x + 6, y + 6), label, font=font)
        draw.rectangle((box[0] - 4, box[1] - 4, box[2] + 4, box[3] + 4), fill="black")
        draw.text((x + 6, y + 6), label, fill="yellow", font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=82)
    return out


class Triage:
    def __init__(self, judge: Callable[[str, str, int], Awaitable[dict[int, Look]]], work_dir: Path,
                 fetch: Fetch = fetch_cover) -> None:
        """`judge(reference_sheet, candidate_sheet, count)` -> {candidate number (1-based): Look}."""
        self.judge, self.work_dir, self.fetch = judge, work_dir, fetch

    async def looks(self, references: Sequence[Path], candidates: Sequence[VideoItem],
                    on_batch: Callable[[int, int, int], Awaitable[None]] | None = None) -> dict[str, Look] | None:
        """{canonical id: Look} for the candidates with a cover; None when no batch could be judged at all (then
        the run falls back to ranking without it rather than dropping everything)."""
        refs = [p.read_bytes() for p in references[:MAX_REFERENCES] if p.is_file()]
        if not refs:
            return None
        ref_sheet = make_sheet(refs, [f"R{n}" for n in range(1, len(refs) + 1)], self.work_dir / "references.jpg")
        covers = await asyncio.gather(*(self.fetch(u) if (u := cover_url(i)) else _none() for i in candidates))
        ready = [(i, c) for i, c in zip(candidates, covers, strict=True) if c]
        out: dict[str, Look] = {}
        judged = 0
        for b, start in enumerate(range(0, len(ready), BATCH), 1):
            batch = ready[start:start + BATCH]
            sheet = make_sheet([c for _, c in batch], [str(n) for n in range(1, len(batch) + 1)],
                               self.work_dir / f"candidates-{b}.jpg")
            try:
                verdicts = await self.judge(str(ref_sheet), str(sheet), len(batch))
                judged += 1
            except Exception as e:  # one failed batch only costs its candidates
                log.warning("look check batch %d failed: %s", b, e)
                verdicts = {}
            for n, (item, _) in enumerate(batch, 1):
                if n in verdicts:
                    out[item.canonical_id] = verdicts[n]
            if on_batch is not None:
                await on_batch(b, len(batch), sum(1 for v in verdicts.values() if v.score >= 6))
        return out if judged else None


async def _none() -> None:
    return None
