"""Test helpers importable from any test directory."""
import base64
import json
import time


def make_jwt(exp: int | None = None, account_id: str = "acc_1", email: str = "nico@example.com",
             plan: str = "plus", fedramp: bool = False) -> str:
    def enc(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    payload = {
        "exp": exp if exp is not None else int(time.time()) + 3600,
        "email": email,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan,
            "chatgpt_account_is_fedramp": fedramp,
        },
    }
    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"


# ---- model-client helpers (Task 8) ----
from tf_agent.models.client import CallRecord, ModelClient  # noqa: E402
from tf_agent.models.fake import FakeAdapter  # noqa: E402
from tf_agent.models.governor import ProviderStatus, UsageGovernor  # noqa: E402
from tf_agent.models.registry import ModelRegistry  # noqa: E402
from tf_agent.models.router import RoleRouter  # noqa: E402


class ListLedger:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    async def record(self, rec: CallRecord) -> None:
        self.records.append(rec)


class MemoryStateStore:
    def __init__(self, initial: dict[str, ProviderStatus] | None = None) -> None:
        self.saved: dict[str, ProviderStatus] = dict(initial or {})

    async def save(self, provider: str, status: ProviderStatus) -> None:
        self.saved[provider] = status

    async def load_all(self) -> dict[str, ProviderStatus]:
        return dict(self.saved)


async def _no_sleep(_: float) -> None:
    return None


def make_client(adapters: dict[str, FakeAdapter], roles: dict | None = None, concurrency: int = 4,
                clock=None) -> tuple[ModelClient, UsageGovernor, ListLedger]:
    """ModelClient over fake adapters; default role order = dict order of `adapters`."""
    registry = ModelRegistry(adapters, {})
    roles = roles or {"default": [{"provider": name, "model": f"{name}-model"} for name in adapters]}
    governor = UsageGovernor(adapters.keys(), concurrency, **({"clock": clock} if clock else {}))
    ledger = ListLedger()
    client = ModelClient(adapters, RoleRouter(roles, registry), governor, ledger, sleep=_no_sleep)
    return client, governor, ledger


# ---- run fixtures (Plan 3) ----
async def create_test_run(sessionmaker, n_tasks: int = 1, slug: str = "nicolaiz"):
    """Character + version + run (+ scout tasks) for DB tests. Returns (character_id, run_id, task_ids)."""
    from tf_db.models import Character, CharacterVersion, Run, Task

    async with sessionmaker() as s:
        c = Character(slug=slug, name=slug.title(), folder_path="/x")
        s.add(c)
        await s.flush()
        v = CharacterVersion(character_id=c.id, version=1, profile_md="p", front_matter={},
                             canonical_image_path="/i.png", content_hash="h")
        s.add(v)
        await s.flush()
        r = Run(character_id=c.id, character_version_id=v.id, state="running")
        s.add(r)
        await s.flush()
        tasks = [Task(run_id=r.id, task_type="scout", platform="tiktok") for _ in range(n_tasks)]
        s.add_all(tasks)
        await s.commit()
        return c.id, r.id, [t.id for t in tasks]
