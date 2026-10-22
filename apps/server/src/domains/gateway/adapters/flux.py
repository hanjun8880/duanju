from src.domains.gateway.adapters.base import ProviderConfig

# Look-dev T2I/I2I. Vendor unknown: placeholder contract, callback returns the image under video_url key
# unless overridden.
CONFIG = ProviderConfig(
    name="flux",
    create_paths={"image.t2i": "/v1/images/generations", "image.i2i": "/v1/images/edits"},
    field_map={"omni_reference_task_type": None},
    cb_video_url="image_url",
)
