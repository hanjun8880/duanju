from src.domains.gateway.adapters.base import ProviderConfig

# Alibaba Model Studio names the model "wan3.0-video" (async task API); reseller paths differ. Placeholder.
CONFIG = ProviderConfig(
    name="wan",
    create_paths={"video.i2v": "/v1/video/generations", "video.t2v": "/v1/video/generations"},
    model_ids={"wan3_spicy": "wan3.0-video",
              # 专家选型 2026-10-23：空镜/过场用 2.2-i2v-flash 降成本，id 为占位（未核实）
              "wan2_2_i2v_flash": "wan2.2-i2v-flash"},
    field_map={"omni_reference_task_type": None},
)
