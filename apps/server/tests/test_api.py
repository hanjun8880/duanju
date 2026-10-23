from httpx import ASGITransport, AsyncClient

from src.main import app


async def test_models_endpoint_hides_provider_details():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/healthz")).json() == {"status": "ok"}
        models = (await c.get("/api/v1/models")).json()
    keys = {m["model_key"] for m in models}
    assert {"seedance_2_5_spicy", "wan3_spicy", "realesrgan_x4plus"} <= keys
    assert all("api_base" not in m and "provider" not in m for m in models)
