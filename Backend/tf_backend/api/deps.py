from fastapi import HTTPException, Request

from tf_backend.app_context import AppContext

CLIENT_HEADER = "x-trendfinder-client"


def require_client_header(request: Request) -> None:
    """Mutating calls need a custom header (forces a CORS preflight; blocks drive-by requests from other sites)."""
    if not request.headers.get(CLIENT_HEADER):
        raise HTTPException(403, f"missing {CLIENT_HEADER} header")


def ctx(request: Request) -> AppContext:
    return request.app.state.context
