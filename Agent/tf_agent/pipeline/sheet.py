"""Contact sheet: one grid image of key frames with timestamps (D-16: one image per video for the AI judge)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from tf_agent.pipeline.frames import Frame


def make_contact_sheet(frames: list[Frame], out_path: Path, cols: int = 3, rows: int = 4, width: int = 1024) -> Path:
    if not frames:
        raise ValueError("no frames for the contact sheet")
    cell_w = width // cols
    with Image.open(frames[0][1]) as first:
        cell_h = round(cell_w * first.height / first.width)
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=max(14, cell_w // 14))
    for idx, (t, path) in enumerate(frames[: cols * rows]):
        x, y = (idx % cols) * cell_w, (idx // cols) * cell_h
        with Image.open(path) as im:
            sheet.paste(im.convert("RGB").resize((cell_w, cell_h)), (x, y))
        label = f"{t:.1f}s"
        box = draw.textbbox((x + 6, y + 6), label, font=font)
        draw.rectangle((box[0] - 4, box[1] - 3, box[2] + 4, box[3] + 3), fill=(0, 0, 0))
        draw.text((x + 6, y + 6), label, fill=(255, 255, 255), font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "JPEG", quality=85)
    return out_path
