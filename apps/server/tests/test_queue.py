import pytest
from celery.contrib.testing.worker import start_worker

from src.queue import QUEUE_HIGH, QUEUE_LOCAL, celery_app, queue_for


@pytest.mark.parametrize("task,model,provider,queue", [
    ("image.t2i", "flux_uncensored", "flux", QUEUE_HIGH),
    ("video.i2v", "seedance_2_5_spicy", "seedance", "video.seedance_2_5_spicy"),
    ("video.t2v", "wan3_spicy", "wan", "video.wan3_spicy"),
    ("face.swap", "insightface_swap_pro", "local", QUEUE_LOCAL),
    ("post.upscale", "realesrgan_x4plus", "local", QUEUE_LOCAL),
])
def test_queue_isolation(task, model, provider, queue):
    assert queue_for(task, model, provider) == queue


def test_real_redis_worker_roundtrip():
    """A real worker consumes the isolated local queue over the Redis broker."""
    with start_worker(celery_app, queues=[QUEUE_LOCAL], perform_ping_check=False, pool="solo"):
        r = celery_app.send_task("system.ping", queue=QUEUE_LOCAL)
        assert r.get(timeout=20) == "pong"
