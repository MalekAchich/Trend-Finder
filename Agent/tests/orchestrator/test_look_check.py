"""The look check on made-up covers: sheets, batching, a failed batch, and picks that only look like the references."""
import io
from datetime import UTC, datetime, timedelta

from PIL import Image

from tf_agent.discovery.lookalike import Gate, Pool, gate
from tf_agent.discovery.triage import BATCH, Look, Triage, cover_url, make_sheet
from tf_agent.tools.types import Creator, Metrics, VideoItem

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def jpeg(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (90, 160), color).save(buf, "JPEG")
    return buf.getvalue()


def item(n, plays=1_000_000, handle=None, thumb=True):
    return VideoItem(canonical_id=f"instagram:TESTREEL{n:03d}", platform="instagram",
                     url=f"https://www.instagram.com/reel/TESTREEL{n:03d}/", creator=Creator(handle=handle or f"c{n}"),
                     metrics=Metrics(views=plays), posted_at=NOW - timedelta(days=2),
                     thumbnail_url=f"https://cdn.invalid/{n}.jpg" if thumb else None)


def test_sheet_is_a_labeled_grid(tmp_path):
    out = make_sheet([jpeg(), jpeg("blue"), b"broken"], ["1", "2", "3"], tmp_path / "s.jpg")
    with Image.open(out) as im:
        assert im.size == (5 * 180, 320)
    assert cover_url(item(1, thumb=False)) is None
    yt = VideoItem(canonical_id="youtube:TestShort01", platform="youtube", url="https://www.youtube.com/shorts/TestShort01")
    assert cover_url(yt) == "https://i.ytimg.com/vi/TestShort01/hqdefault.jpg"


async def test_covers_are_judged_in_batches_and_a_failed_batch_costs_only_itself(tmp_path):
    ref = tmp_path / "ref.jpg"
    ref.write_bytes(jpeg())
    calls = []

    async def judge(ref_sheet, sheet, count):
        calls.append(count)
        if len(calls) == 2:
            raise RuntimeError("model hiccup")
        return {n: Look(9 if n % 2 else 2, "AI skit like R1" if n % 2 else "football") for n in range(1, count + 1)}

    async def fetch(url):
        return jpeg()

    items = [item(n) for n in range(1, BATCH + 6)] + [item(99, thumb=False)]
    looks = await Triage(judge, tmp_path, fetch).looks([ref], items)
    assert calls == [BATCH, 5]  # the cover-less one isn't sent
    assert len(looks) == BATCH and looks["instagram:TESTREEL001"] == Look(9, "AI skit like R1")
    assert await Triage(judge, tmp_path, fetch).looks([], items) is None  # no reference covers: can't run


async def test_nothing_judged_means_no_look_check_at_all(tmp_path):
    ref = tmp_path / "ref.jpg"
    ref.write_bytes(jpeg())

    async def judge(*_):
        raise RuntimeError("every provider is at its limit")

    async def fetch(url):
        return jpeg()

    assert await Triage(judge, tmp_path, fetch).looks([ref], [item(1)]) is None


def test_with_the_look_check_only_lookalikes_are_judged_closest_first():
    pool = Pool()
    pool.add([item(1, 50_000_000), item(2, 2_000_000), item(3, 900_000), item(4, 20_000), item(5, 20_000),
              item(6, 3_000_000)], "found searching")
    looks = {"instagram:TESTREEL001": Look(2, "real football broadcast"), "instagram:TESTREEL002": Look(7, "close"),
             "instagram:TESTREEL003": Look(9, "AI suit-man skit like R3"), "instagram:TESTREEL004": Look(8, "AI skit"),
             "instagram:TESTREEL005": Look(6, "loosely")}
    picks, dropped = gate(pool, set(), Gate(min_plays=300_000), NOW, looks=looks)
    assert [(p.item.canonical_id[-3:], p.lane) for p in picks] == [("003", "viral"), ("002", "viral"), ("004", "gem")]
    assert "like R3" in picks[0].why and picks[0].look == 9
    assert dropped == {"doesn't look like your references": 2, "not among the covers checked": 1}
