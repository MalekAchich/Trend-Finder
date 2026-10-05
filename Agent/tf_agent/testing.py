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
