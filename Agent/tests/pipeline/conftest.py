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
