"""The controller's HTTPS API (implementation.md §6.3). Every route needs the admin token."""

import hmac
from collections.abc import Callable, MutableMapping
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .controller import Cloud, Controller, InvalidT3Version
from .sizes import UnknownSize
from .store import StateStore


class Unauthorized(Exception):
    pass


class CreateMachine(BaseModel):
    name: str
    size: str
    t3Version: str


def machine_view(m) -> dict[str, Any]:
    doc = m.to_json()
    return {k: doc[k] for k in ("id", "name", "size", "power", "sandboxId", "endpoints", "stoppedAt", "error")}


def create_api(
    *,
    admin_token: str,
    store: StateStore,
    ops: MutableMapping[str, dict[str, Any]],
    cloud: Cloud,
    run_op: Callable[[str], None] | None,
    revision: str,
    modal_version: str,
    clock_ms: Callable[[], int],
) -> FastAPI:
    """run_op starts an operation in the background. None runs it inline before the request returns."""
    if not admin_token:
        raise ValueError("admin_token is empty, which would let any request through")
    controller = Controller(store=store, ops=ops, cloud=cloud, clock_ms=clock_ms)
    start = run_op or controller.perform
    expected = f"Bearer {admin_token}".encode()

    def require_admin(request: Request) -> None:
        given = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(given, expected):
            raise Unauthorized()

    api = FastAPI(dependencies=[Depends(require_admin)], docs_url=None, redoc_url=None, openapi_url=None)

    @api.exception_handler(Unauthorized)
    async def unauthorized(_request: Request, _exc: Unauthorized):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    @api.get("/version")
    def version():
        return {"revision": revision, "modal": modal_version}

    @api.post("/machines", status_code=202)
    def create_machine(body: CreateMachine):
        try:
            machine_id, op_id = controller.begin_create(name=body.name, size=body.size, t3_version=body.t3Version)
        except UnknownSize as e:
            return JSONResponse({"error": "unknown_size", "message": str(e)}, status_code=400)
        except InvalidT3Version as e:
            return JSONResponse({"error": "invalid_t3_version", "message": str(e)}, status_code=400)
        try:
            start(op_id)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            controller.fail(op_id, error)
            return JSONResponse({"error": "start_failed", "message": error, "id": machine_id, "opId": op_id}, status_code=503)
        return {"id": machine_id, "opId": op_id}

    @api.get("/machines/{machine_id}")
    def get_machine(machine_id: str):
        m = controller.machine(machine_id)
        if m is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return machine_view(m)

    @api.get("/ops/{op_id}")
    def get_op(op_id: str):
        op = controller.op(op_id)
        if op is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return op

    return api
