from src.domains.gateway.adapters.base import ProviderConfig

# Alibaba Model Studio names the model "wan3.0-video" (async task API); reseller paths differ. Placeholder.
CONFIG = ProviderConfig(
    name="wan",
    create_paths={"video.i2v": "/v1/video/generations", "video.t2v": "/v1/video/generations"},
    model_ids={"wan3_spicy": "wan3.0-video"},
    field_map={"omni_reference_task_type": None},
)
