import uuid

import pytest
from pydantic import ValidationError

from src.domains.gateway.schemas import JobCreateRequest, MultimodalRefs, preflight

P = uuid.uuid4()


def req(**kw):
    return JobCreateRequest(project_id=P, task_type=kw.pop("task_type", "video.i2v"),
                            model_key="seedance_2_5_spicy", prompt="p", **kw)


def test_last_frame_requires_first_frame():
    with pytest.raises(ValidationError, match="last_frame_url"):
        req(last_frame_url="http://x/l.png")


def test_first_frame_drops_multimodal_refs():
    r = req(first_frame_url="http://x/f.png", refs=MultimodalRefs(image_urls=["a"], audio_urls=["b"]))
    assert r.refs == MultimodalRefs()
    task, payload = preflight(r)
    assert task == "video.i2v" and payload["first_frame_url"] == "http://x/f.png"
    assert not any(k.startswith("reference_") for k in payload)
    assert "omni_reference_task_type" not in payload


def test_video_edit_forces_adaptive_and_minus_one():
    r = req(task_type="video.edit", duration=12, aspect_ratio="16:9", edit_source_seconds=8,
            refs=MultimodalRefs(video_urls=["src.mp4"]))
    assert (r.aspect_ratio, r.duration) == ("adaptive", -1)
    task, payload = preflight(r)
    assert task == "video.edit" and payload["duration"] == -1 and payload["aspect_ratio"] == "adaptive"


def test_video_edit_requires_source_seconds():
    with pytest.raises(ValidationError, match="edit_source_seconds"):
        req(task_type="video.edit", refs=MultimodalRefs(video_urls=["src.mp4"]))


@pytest.mark.parametrize("d", [3, 31, 0, -2])
def test_duration_bounds(d):
    with pytest.raises(ValidationError, match="between 4 and 30"):
        req(duration=d)


@pytest.mark.parametrize("d", [4, 30])
def test_duration_edges_ok(d):
    assert req(duration=d).duration == d


def test_ref_count_limits():
    with pytest.raises(ValidationError):
        MultimodalRefs(image_urls=["x"] * 31)
    with pytest.raises(ValidationError):
        MultimodalRefs(video_urls=["x"] * 11)
    with pytest.raises(ValidationError):
        MultimodalRefs(audio_urls=["x"] * 11)
    MultimodalRefs(image_urls=["x"] * 30, video_urls=["v"] * 10, audio_urls=["a"] * 10)


def test_no_refs_routes_i2v_to_t2v_and_strips_omni():
    task, payload = preflight(req())
    assert task == "video.t2v"
    assert "omni_reference_task_type" not in payload
    assert payload["prompt"] == "p" and payload["duration"] == 5


def test_multi_ref_sets_omni_and_arrays():
    task, payload = preflight(req(refs=MultimodalRefs(image_urls=["i1", "i2"], video_urls=["v1"])))
    assert task == "video.i2v"
    assert payload["omni_reference_task_type"]
    assert payload["reference_image_urls"] == ["i1", "i2"] and payload["reference_video_urls"] == ["v1"]
    assert "reference_audio_urls" not in payload and "first_frame_url" not in payload


def test_audio_only_ref_needs_visual_ref():
    with pytest.raises(ValidationError, match="audio"):
        req(refs=MultimodalRefs(audio_urls=["a"]))


def test_image_task_ignores_video_fields():
    task, payload = preflight(req(task_type="image.t2i"))
    assert task == "image.t2i"
    assert "duration" not in payload and "generate_audio" not in payload and "return_last_frame" not in payload
