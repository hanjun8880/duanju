from src.domains.gateway.adapters.base import ProviderConfig

# MiniMax first-party id is "MiniMax-H3" (4-15s). Paths/fields are placeholders.
CONFIG = ProviderConfig(
    name="minimax",
    create_paths={"video.i2v": "/v1/video_generation", "video.t2v": "/v1/video_generation"},
    model_ids={"minimax_h3": "MiniMax-H3"},
    field_map={"omni_reference_task_type": None},
)
