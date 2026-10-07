import subprocess
from pathlib import Path

import pytest


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def clips(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("clips")
    out = {
        "av": d / "testsrc_with_audio.mp4",
        "silent": d / "testsrc_silent.mp4",
        "cut": d / "red_then_blue.mp4",
        "static": d / "static_gray.mp4",
    }
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=3", "-f", "lavfi", "-i",
           "sine=frequency=440:duration=3", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           str(out["av"]))
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=3", "-c:v", "libx264", "-pix_fmt",
           "yuv420p", str(out["silent"]))
    ffmpeg("-f", "lavfi", "-i", "color=c=red:size=360x640:rate=30:duration=1.5", "-f", "lavfi", "-i",
           "color=c=blue:size=360x640:rate=30:duration=1.5", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["cut"]))
    ffmpeg("-f", "lavfi", "-i", "color=c=gray:size=360x640:rate=30:duration=3", "-c:v", "libx264", "-pix_fmt",
           "yuv420p", str(out["static"]))
    return out


CHARACTERS = Path(__file__).resolve().parents[3] / ".." / "AI Influencers Characters"
# a character's main image shows the face on the left half and the full body on the right half: take the body
# half and fit it into a vertical frame
PORTRAIT = "crop=iw/2:ih:iw/2:0,scale=270:480:force_original_aspect_ratio=decrease,pad=270:480:(ow-iw)/2:(oh-ih)/2:white"


def _character_image() -> Path | None:
    """A real full-body person for the pose tests: the main image (<Name>.png) of the first character folder."""
    for folder in sorted(CHARACTERS.glob("*/")) if CHARACTERS.is_dir() else []:
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            if (img := folder / f"{folder.name}{ext}").is_file():
                return img
    return None


@pytest.fixture(scope="session")
def person_clips(tmp_path_factory) -> dict[str, Path]:
    """Still-image videos built from a character image: one person, two side by side, and an empty frame."""
    person = _character_image()
    if person is None:
        pytest.skip("no character image to build person clips from")
    d = tmp_path_factory.mktemp("people")
    out = {"one": d / "one.mp4", "two": d / "two.mp4", "empty": d / "empty.mp4", "pan": d / "pan.mp4"}
    ffmpeg("-loop", "1", "-i", str(person), "-t", "3", "-r", "30", "-vf", PORTRAIT, "-c:v", "libx264",
           "-pix_fmt", "yuv420p", str(out["one"]))
    ffmpeg("-loop", "1", "-i", str(person), "-loop", "1", "-i", str(person), "-t", "3", "-r", "30",
           "-filter_complex", f"[0:v]{PORTRAIT}[a];[1:v]{PORTRAIT}[b];[a][b]hstack=inputs=2,scale=540:480",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["two"]))
    ffmpeg("-f", "lavfi", "-i", "color=c=0xfafafa:size=270x480:rate=30:duration=3", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", str(out["empty"]))
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1280x640:rate=30:duration=3", "-vf",
           "crop=360:640:x='t*250':y=0", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["pan"]))
    return out
