"""The controller's HTTPS API (implementation.md §6.3, §6.7).

The laptop sends the admin token. A daemon signs each request with its machine secret, and may use
only the routes in MACHINE_ROUTES, and only for its own machine. Every other route refuses it.
"""

import hmac
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .controller import Cloud, Controller, InvalidT3Version
from .machines import StaleSandbox
from .signing import Nonces, Refused, check
from .sizes import UnknownSize
from .store import StateStore

# (method, route path) pairs a machine secret may call. A route added later stays closed to machines
# until it is listed here.
MACHINE_ROUTES = {("POST", "/machines/{machine_id}/heartbeat"), ("GET", "/ops/{op_id}")}


class Unauthorized(Exception):
    def __init__(self, reason: str | None = None):
        self.reason = reason


class Forbidden(Exception):
    pass


@dataclass(frozen=True)
class Caller:
    machine_id: str | None  # None for the admin


class Heartbeat(BaseModel):
    sandboxId: str
    nextDueAt: int | None
    keepAwakeUntil: int


class CreateMachine(BaseModel):
    name: str
    size: str
    t3Version: str


def signed_path(scope: dict[str, Any]) -> str:
    """The path and query string as the client sent them, which is what the daemon signs. An ASGI
    server may leave raw_path out or set it to None, so the decoded path is the fallback."""
    path = scope.get("raw_path") or scope["path"].encode()
    query = scope.get("query_string", b"")
    return (path + (b"?" + query if query else b"")).decode("latin-1")


def machine_view(m) -> dict[str, Any]:
    doc = m.to_json()
    keys = ("id", "name", "size", "power", "sandboxId", "endpoints", "stoppedAt", "error", "lastHeartbeatAt", "nextDueAt", "keepAwakeUntil")
    return {k: doc[k] for k in keys}


def create_api(
    *,
    admin_token: str,
    hmac_key: str,
    controller_url: str,
    store: StateStore,
    ops: MutableMapping[str, dict[str, Any]],
    nonces: Nonces,
    cloud: Cloud,
    run_op: Callable[[str], None] | None,
    revision: str,
    modal_version: str,
    clock_ms: Callable[[], int],
) -> FastAPI:
    """run_op starts an operation in the background. None runs it inline before the request returns."""
    if not admin_token:
        raise ValueError("admin_token is empty, which would let any request through")
    if not hmac_key:
        raise ValueError("hmac_key is empty, which would make every machine secret guessable")
    controller = Controller(
        store=store, ops=ops, cloud=cloud, clock_ms=clock_ms, hmac_key=hmac_key, controller_url=controller_url
    )
    start = run_op or controller.perform
    expected = f"Bearer {admin_token}".encode()

    async def identify(request: Request) -> Caller:
        if "authorization" in request.headers:
            if not hmac.compare_digest(request.headers["authorization"].encode(), expected):
                raise Unauthorized()
            return Caller(machine_id=None)
        h = request.headers
        names = ("x-t3modal-machine", "x-t3modal-timestamp", "x-t3modal-nonce", "x-t3modal-signature")
        if not all(n in h for n in names):
            raise Unauthorized()
        try:
            check(
                hmac_key,
                machine_id=h["x-t3modal-machine"],
                timestamp=h["x-t3modal-timestamp"],
                nonce=h["x-t3modal-nonce"],
                signature=h["x-t3modal-signature"],
                method=request.method,
                path=signed_path(request.scope),
                body=await request.body(),
                now_s=clock_ms() // 1000,
                nonces=nonces,
            )
        except Refused as e:
            raise Unauthorized(e.reason) from None
        return Caller(machine_id=h["x-t3modal-machine"])

    async def authenticate(request: Request) -> None:
        caller = await identify(request)
        if caller.machine_id is not None:
            route = request.scope.get("route")
            if route is None or (request.method, route.path) not in MACHINE_ROUTES:
                raise Forbidden()
        request.state.caller = caller

    api = FastAPI(dependencies=[Depends(authenticate)], docs_url=None, redoc_url=None, openapi_url=None)

    @api.exception_handler(Unauthorized)
    async def unauthorized(_request: Request, exc: Unauthorized):
        body = {"error": "unauthorized"} if exc.reason is None else {"error": "unauthorized", "reason": exc.reason}
        return JSONResponse(body, status_code=401)

    @api.exception_handler(Forbidden)
    async def forbidden(_request: Request, _exc: Forbidden):
        return JSONResponse({"error": "forbidden"}, status_code=403)

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
        # The only answer that carries the secret. The controller derives it and never stores it.
        return {"id": machine_id, "opId": op_id, "machineSecret": controller.machine_secret(machine_id)}

    @api.get("/machines/{machine_id}")
    def get_machine(machine_id: str):
        m = controller.machine(machine_id)
        if m is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return machine_view(m)

    @api.post("/machines/{machine_id}/heartbeat")
    def heartbeat(machine_id: str, body: Heartbeat, request: Request):
        if request.state.caller.machine_id != machine_id:
            raise Forbidden()
        try:
            found = controller.heartbeat(
                machine_id, sandbox_id=body.sandboxId, next_due_at=body.nextDueAt, keep_awake_until=body.keepAwakeUntil
            )
        except StaleSandbox as e:
            return JSONResponse({"error": "stale_sandbox", "message": str(e)}, status_code=409)
        if not found:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return {}

    @api.get("/ops/{op_id}")
    def get_op(op_id: str, request: Request):
        op = controller.op(op_id)
        caller = request.state.caller
        if caller.machine_id is not None and (op is None or op.get("machineId") != caller.machine_id):
            raise Forbidden()
        if op is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return op

    return api
