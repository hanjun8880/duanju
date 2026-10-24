from src.domains.gateway.adapters.base import ProviderConfig

# 可灵 (Kuaishou Keling) 4.0: 专家选型推荐用于打戏/动作镜头，但系统里还没有接口，且没有核实过的 API 文档/密钥。
# 占位：model_catalog 行默认 is_active=false，HttpAdapter.submit 在没有 KELING_API_KEY 时会直接
# ProviderNotConfigured（和 wan/minimax 的占位一致）。等拿到官方文档和密钥后，把这里的 create_paths /
# field_map 换成真实值，再把 model_catalog.is_active 打开。
CONFIG = ProviderConfig(
    name="keling",
    create_paths={"video.i2v": "/v1/videos/image2video", "video.t2v": "/v1/videos/text2video"},
    model_ids={"keling_4_0": "kling-v4-0"},
    field_map={"omni_reference_task_type": None},
)
