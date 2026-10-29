"""A local stand-in for a Databricks workspace's OAuth and REST endpoints.

The real SDK authenticates a service principal against it end to end: OIDC
discovery when a client is built, then the client-credentials token exchange on
its first API call. The token endpoint refuses any secret but the one the stub
accepts with ``invalid_client``, as Databricks does, which the SDK raises as a
plain ``ValueError`` rather than a ``DatabricksError``. It answers the Unity
Catalog catalog list and a volume directory listing; anything else is a 404.
"""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class WorkspaceStub:
    host: str
    accepted_secret: str
    #: Clients built against it: each does OIDC discovery once.
    discoveries: int = 0
    token_requests: list[str] = field(default_factory=list)


@contextmanager
def workspace_stub(accepted_secret: str) -> Iterator[WorkspaceStub]:
    """Serve a workspace whose token endpoint takes only *accepted_secret*."""
    stub = WorkspaceStub(host="", accepted_secret=accepted_secret)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return None

        def _json(self, status: int, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/oidc/.well-known/oauth-authorization-server":
                stub.discoveries += 1
                self._json(
                    200,
                    {
                        "authorization_endpoint": f"{stub.host}/oidc/v1/authorize",
                        "token_endpoint": f"{stub.host}/oidc/v1/token",
                    },
                )
            elif self.path.startswith("/api/2.1/unity-catalog/catalogs"):
                self._json(200, {"catalogs": [{"name": "main"}]})
            elif self.path.startswith("/api/2.0/fs/directories/"):
                self._json(200, {"contents": []})
            else:
                self._json(404, {"error_code": "NOT_FOUND", "message": self.path})

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path != "/oidc/v1/token":
                self._json(404, {"error_code": "NOT_FOUND", "message": self.path})
                return
            basic = self.headers.get("Authorization", "").removeprefix("Basic ")
            secret = base64.b64decode(basic).decode().partition(":")[2]
            stub.token_requests.append(secret)
            if secret == stub.accepted_secret:
                self._json(
                    200, {"access_token": "token", "token_type": "Bearer", "expires_in": 3600}
                )
            else:
                self._json(
                    401,
                    {
                        "error": "invalid_client",
                        "error_description": "Client authentication failed",
                    },
                )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    stub.host = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
