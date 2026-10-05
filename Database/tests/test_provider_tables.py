from sqlalchemy import select

from tf_db.models import ModelCallRow, ModelRow, ProviderStateRow


async def test_model_call_roundtrip(db_sessionmaker):
    async with db_sessionmaker() as s:
        s.add(ModelCallRow(role="scout", provider="claude", model="opus", latency_ms=1200,
                           status="ok", input_tokens=10, output_tokens=5))
        await s.commit()
        got = (await s.execute(select(ModelCallRow))).scalar_one()
    assert got.id.version == 7
    assert got.images == 0
    assert got.created_at is not None
    assert got.run_id is None


async def test_model_row_defaults(db_sessionmaker):
    async with db_sessionmaker() as s:
        s.add(ModelRow(provider="chatgpt", model_id="gpt-6-luna"))
        await s.commit()
        got = (await s.execute(select(ModelRow))).scalar_one()
    assert got.capabilities == {}
    assert got.available is True


async def test_provider_state_defaults(db_sessionmaker):
    async with db_sessionmaker() as s:
        s.add(ProviderStateRow(provider="claude"))
        await s.commit()
        got = (await s.execute(select(ProviderStateRow))).scalar_one()
    assert got.status == "ok"
    assert got.cooling_until is None
