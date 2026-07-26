"""Shared test fixtures that are not tests themselves.

FakeTokenServer lives here rather than in test_auth.py because two modules
need it, and importing across test modules only works by accident of whatever
happens to be on sys.path.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs


class FakeTokenServer:
    """A stand-in for a provider's /token endpoint.

    Binds an ephemeral port and records what it was sent, so a test can assert
    the code verifier really made it into the exchange.
    """

    def __init__(self, response: dict | None = None, status: int = 200):
        self.response = response or {
            "access_token": "at-11111111",
            "refresh_token": "rt-22222222",
            "id_token": "it-33333333",
            "expires_in": 3600,
            "scope": "user:profile user:inference",
            "account_id": "acct-1",
        }
        self.status = status
        self.requests: list[dict] = []
        self._server: asyncio.AbstractServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None, "use FakeTokenServer as an async context manager"
        return f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}/token"

    async def __aenter__(self) -> FakeTokenServer:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()  # request line
        length = 0
        content_type = ""
        while True:
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
            name, _, value = line.decode().partition(":")
            if name.strip().lower() == "content-length":
                length = int(value.strip())
            if name.strip().lower() == "content-type":
                content_type = value.strip()

        raw = await reader.readexactly(length) if length else b""
        if "json" in content_type:
            self.requests.append(json.loads(raw))
        else:
            self.requests.append({k: v[0] for k, v in parse_qs(raw.decode()).items()})

        body = json.dumps(self.response).encode()
        writer.write(
            f"HTTP/1.1 {self.status} OK\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
