import pytest

from tf_agent.pipeline.feasibility import clean_segments, feasibility
from tf_agent.pipeline.frames import sample_frames
from tf_agent.pipeline.motion import camera_motion
from tf_agent.pipeline.pose import PoseAnalyzer, PoseStats


def stats(spr=1.0, bv=0.8, hv=0.5, fs=1.0, people=None):
    people = people if people is not None else [1] * 6
    return PoseStats(per_frame_people=people, per_frame_single=[p == 1 for p in people], single_person_ratio=spr,
                     body_visibility=bv, hands_visibility=hv, face_size_ok=fs)


def test_feasibility_formula():
    score, reason = feasibility(stats(), camera_motion=0.2, cut_rate=1.5, best_segment=(0.0, 4.0))
    assert score == pytest.approx(82.0) and reason is None


@pytest.mark.parametrize("st,segment,reason", [
    (stats(spr=0.0, people=[0] * 6), (0.0, 4.0), "no_person"),
    (stats(spr=0.3, people=[2] * 6), (0.0, 4.0), "multiple_people"),
    (stats(bv=0.3), (0.0, 4.0), "body_not_visible"),
    (stats(), None, "no_clean_segment"),
])
def test_hard_filters(st, segment, reason):
    assert feasibility(st, camera_motion=0.0, cut_rate=0.0, best_segment=segment)[1] == reason


def test_clean_segments_break_on_cuts_and_unclean_frames():
    times = [i * 0.5 for i in range(12)]
    single = [t != 3.0 for t in times]
    motion = [0.0] * 12
    assert clean_segments(times, single, cuts=[1.2], motion=motion, min_len=3.0) == []
    assert clean_segments(times, single, cuts=[1.2], motion=motion, min_len=2.0) == [(3.5, 6.0)]
    shaky = [0.9 if t == 4.5 else 0.0 for t in times]
    assert clean_segments(times, [True] * 12, cuts=[], motion=shaky, min_len=2.0) == [(0.0, 4.5)]


@pytest.fixture(scope="module")
def analyzer():
    with PoseAnalyzer() as pa:
        yield pa


async def frames_of(path, tmp_path, name):
    return await sample_frames(path, tmp_path / name, fps=2, width=360)


async def test_pose_one_person(person_clips, tmp_path, analyzer):
    st = analyzer.analyze([p for _, p in await frames_of(person_clips["one"], tmp_path, "one")])
    assert st.single_person_ratio >= 0.8 and st.body_visibility >= 0.6 and st.face_size_ok >= 0.9


async def test_pose_two_people(person_clips, tmp_path, analyzer):
    st = analyzer.analyze([p for _, p in await frames_of(person_clips["two"], tmp_path, "two")])
    assert st.single_person_ratio <= 0.2 and max(st.per_frame_people) >= 2


async def test_pose_empty_frame(person_clips, tmp_path, analyzer):
    st = analyzer.analyze([p for _, p in await frames_of(person_clips["empty"], tmp_path, "empty")])
    assert st.single_person_ratio == 0.0 and max(st.per_frame_people) == 0


async def test_camera_motion_static_vs_pan(person_clips, tmp_path):
    still, _ = camera_motion([p for _, p in await frames_of(person_clips["one"], tmp_path, "s")])
    pan, per_frame = camera_motion([p for _, p in await frames_of(person_clips["pan"], tmp_path, "p")])
    assert still < 0.05 and pan > still + 0.1
    assert len(per_frame) == len(list((tmp_path / "p").glob("*.jpg")))


async def test_pose_analyzer_closes_explicitly(person_clips, tmp_path):
    """MediaPipe deadlocks if its landmarker is only closed by the GC at shutdown: owners must close explicitly."""
    frames = [p for _, p in await frames_of(person_clips["one"], tmp_path, "c")]
    with PoseAnalyzer() as pa:
        assert pa.analyze(frames).single_person_ratio >= 0.8
        assert pa._landmarker is not None
    assert pa._landmarker is None
    pa.close()  # idempotent
