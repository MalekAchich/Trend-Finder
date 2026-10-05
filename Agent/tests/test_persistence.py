from sqlalchemy import select

from tf_agent.models.client import CallRecord
from tf_agent.models.governor import ProviderStatus
from tf_agent.models.persistence import DbCallLedger, DbProviderStateStore, save_models
from tf_agent.models.types import ModelInfo
from tf_db.models import ModelCallRow, ModelRow


async def test_ledger_writes_rows(db_sessionmaker):
    await DbCallLedger(db_sessionmaker).record(CallRecord(
        run_id=None, task_id=None, role="scout", provider="claude", model="opus", input_tokens=5,
        output_tokens=3, images=1, latency_ms=900, status="ok", error_class=None))
    async with db_sessionmaker() as s:
        row = (await s.execute(select(ModelCallRow))).scalar_one()
    assert (row.provider, row.images, row.latency_ms) == ("claude", 1, 900)


async def test_state_store_upserts_and_loads(db_sessionmaker):
    store = DbProviderStateStore(db_sessionmaker)
    await store.save("claude", ProviderStatus("cooling", 1_800_000_000.0, 98.5, "limit"))
    await store.save("claude", ProviderStatus("ok", None, 12.0, None))
    loaded = await store.load_all()
    assert loaded["claude"] == ProviderStatus("ok", None, 12.0, None)


async def test_save_models_marks_missing_unavailable(db_sessionmaker):
    await save_models(db_sessionmaker, "chatgpt", [ModelInfo("chatgpt", "a"), ModelInfo("chatgpt", "b")])
    await save_models(db_sessionmaker, "chatgpt", [ModelInfo("chatgpt", "b", display_name="B", vision=False)])
    async with db_sessionmaker() as s:
        rows = {r.model_id: r for r in (await s.execute(select(ModelRow))).scalars()}
    assert rows["a"].available is False and rows["b"].available is True
    assert rows["b"].display_name == "B" and rows["b"].capabilities["vision"] is False
