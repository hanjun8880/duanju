from decimal import Decimal

from httpx import ASGITransport, AsyncClient

from src.main import app


def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_project_lifecycle_and_18plus_confirmation():
    async with client() as c:
        r = await c.post("/api/v1/projects", json={"title": "雾港来信", "content_mode": "uncensored_nsfw"})
        assert r.status_code == 422 and "18" in r.text
        r = await c.post("/api/v1/projects", json={"title": "雾港来信", "content_mode": "uncensored_nsfw",
                                                   "confirm_18plus": True})
        assert r.status_code == 201
        pid = r.json()["id"]
        sfw = (await c.post("/api/v1/projects", json={"title": "晨曦", "source_novel_text": "第一章"})).json()
        assert sfw["content_mode"] == "standard_sfw"
        ids = [p["id"] for p in (await c.get("/api/v1/projects")).json()]
        assert pid in ids and sfw["id"] in ids
        assert (await c.get("/api/v1/me")).json()["credits"]


async def test_lookdev_assets_and_likeness_gate():
    async with client() as c:
        pid = (await c.post("/api/v1/projects", json={"title": "定妆"})).json()["id"]
        body = {"asset_type": "character", "name": "林晚", "visual_prompt": "黑色风衣", "ref_urls": ["https://cdn/a.png"]}
        r = await c.post(f"/api/v1/projects/{pid}/lookdev", json=body)
        assert r.status_code == 422 and "likeness" in r.text
        r = await c.post(f"/api/v1/projects/{pid}/lookdev", json={**body, "likeness_consent": True})
        assert r.status_code == 201
        prop = await c.post(f"/api/v1/projects/{pid}/lookdev", json={"asset_type": "prop", "name": "怀表",
                                                                     "visual_prompt": "黄铜", "ref_urls": []})
        assert prop.status_code == 201
        assets = (await c.get(f"/api/v1/projects/{pid}/lookdev")).json()
        assert {a["name"] for a in assets} == {"林晚", "怀表"}


async def test_templates_hide_nsfw_for_sfw_projects():
    async with client() as c:
        sfw = (await c.post("/api/v1/projects", json={"title": "a"})).json()["id"]
        nsfw = (await c.post("/api/v1/projects", json={"title": "b", "content_mode": "uncensored_nsfw",
                                                       "confirm_18plus": True})).json()["id"]
        t_sfw = (await c.get(f"/api/v1/projects/{sfw}/templates")).json()
        t_nsfw = (await c.get(f"/api/v1/projects/{nsfw}/templates")).json()
    assert len(t_sfw) == 8 and not any(t["is_nsfw"] for t in t_sfw)
    assert len(t_nsfw) == 10


async def test_storyboard_import_endpoint_and_shot_listing():
    from tests.test_orchestrator import BOARD
    async with client() as c:
        pid = (await c.post("/api/v1/projects", json={"title": "c"})).json()["id"]
        r = await c.post(f"/api/v1/projects/{pid}/storyboard", json={"episode_number": 1,
                                                                    "storyboard": BOARD.model_dump()})
        assert r.status_code == 201
        shots = (await c.get(f"/api/v1/projects/{pid}/shots")).json()
    assert [s["shot_order"] for s in shots] == [1, 2] and shots[0]["render_tier"] == "L0_animatic"


async def test_admin_model_switch_price_and_manual_refund(sessionmaker, user_and_project):
    from src.domains.gateway import credit_ledger as ledger
    uid, pid = user_and_project
    async with client() as c:
        r = await c.patch("/api/v1/admin/models/minimax_h3", json={"credits_per_unit": "0.25", "is_active": True})
        assert r.status_code == 200 and r.json()["credits_per_unit"] == "0.2500"
        async with sessionmaker() as s:
            job = await ledger.create_job_with_prededuct(
                s, user_id=uid, project_id=pid, model_key="minimax_h3", task_type="video.t2v",
                request_payload={"x": 1}, billable_units=4, resolution="480p")
        assert job.credit_estimate == Decimal("1.0000")
        jobs = (await c.get("/api/v1/admin/jobs")).json()
        assert any(j["id"] == str(job.id) for j in jobs)
        r = await c.post(f"/api/v1/admin/jobs/{job.id}/refund")
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        assert (await c.post(f"/api/v1/admin/jobs/{job.id}/refund")).status_code == 409
        r = await c.patch("/api/v1/admin/models/minimax_h3", json={"credits_per_unit": "0.3"})
        ledger_rows = (await c.get("/api/v1/ledger", params={"user_id": str(uid)})).json()
    assert [x["reason"] for x in ledger_rows][:2] == ["cancel_refund", "pre_deduct"]


async def test_lookdev_patch_consent_only_for_newly_added_character_refs(engine, user_and_project):
    from sqlalchemy import text
    _, pid = user_and_project
    async with engine.begin() as conn:
        aid = (await conn.execute(text(
            "INSERT INTO lookdev_assets (project_id, asset_type, name, visual_prompt, ref_urls) VALUES "
            "(:p, 'character', '林', 'v', '[\"/media/renders/a.png\", \"/media/renders/b.png\"]') RETURNING id"),
            {"p": pid})).scalar_one()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.patch(f"/api/v1/lookdev/{aid}", json={"ref_urls": ["/media/renders/b.png"]})  # generated, reorder
        assert r.status_code == 200 and r.json()["ref_urls"] == ["/media/renders/b.png"]
        r = await c.patch(f"/api/v1/lookdev/{aid}", json={"ref_urls": ["/media/renders/b.png", "/media/uploads/me.jpg"]})
        assert r.status_code == 422 and "likeness_consent" in r.text
        r = await c.patch(f"/api/v1/lookdev/{aid}", json={"ref_urls": ["/media/uploads/me.jpg"], "likeness_consent": True,
                                                          "visual_prompt": "短发"})
        assert r.status_code == 200 and r.json()["visual_prompt"] == "短发" and r.json()["meta"]["likeness_consent"]
