from sqlalchemy import inspect, text

from src.models import Base

TABLES = {"users", "model_catalog", "templates", "projects", "lookdev_assets",
          "shots", "jobs", "assets", "credit_ledger"}


async def test_nine_tables_exist_and_match_orm(engine):
    async with engine.connect() as conn:
        def cols(sync_conn):
            insp = inspect(sync_conn)
            return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}
        db_cols = await conn.run_sync(cols)
    assert TABLES <= set(db_cols)
    for name, table in Base.metadata.tables.items():
        assert {c.name for c in table.columns} == db_cols[name], name
    assert set(Base.metadata.tables) == TABLES


async def test_seed_models_and_fallbacks(engine):
    async with engine.connect() as conn:
        rows = dict((await conn.execute(text(
            "SELECT model_key, fallback_model_key FROM model_catalog"))).all())
    for k in ["seedance_2_5_spicy", "wan3_spicy", "minimax_h3", "flux_uncensored", "realesrgan_x4plus"]:
        assert k in rows
    assert rows["seedance_2_5_spicy"] == "wan3_spicy"
    assert rows["wan3_spicy"] == "seedance_2_5_spicy"


async def test_seed_ten_templates_mixed_sfw_nsfw(engine):
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT is_nsfw FROM templates"))).scalars().all()
    assert len(rows) == 10
    assert any(rows) and not all(rows)


async def test_shot_duration_check_constraint(engine, user_and_project):
    _, pid = user_and_project
    async with engine.connect() as conn:
        for bad in (3, 31):
            tx = await conn.begin()
            try:
                await conn.execute(text(
                    "INSERT INTO shots (project_id, shot_order, duration, shot_prompt) VALUES (:p,1,:d,'x')"),
                    {"p": pid, "d": bad})
                raise AssertionError(f"duration {bad} accepted")
            except Exception as e:  # noqa: BLE001
                assert "check" in str(e).lower()
            finally:
                await tx.rollback()
