from src.domains.gateway.adapters.base import ProviderConfig

# Field names follow the ApiPass/Segmind-style contract (first_frame_url, reference_image_urls,
# return_last_frame). Model id and paths are placeholders: override per reseller.
CONFIG = ProviderConfig(
    name="seedance",
    create_paths={"video.i2v": "/v1/video/generations", "video.t2v": "/v1/video/generations",
                  "video.edit": "/v1/video/edits", "video.extend": "/v1/video/extensions"},
    model_ids={"seedance_2_5_spicy": "bytedance/seedance-2.5"},
)
