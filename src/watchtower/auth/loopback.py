"""The little HTTP server that catches the OAuth redirect.

Notes on the things that matter:

* It binds 127.0.0.1, never 0.0.0.0. The redirect only ever comes from the
  browser on this machine and there is no reason to be reachable from the LAN.
* The port is fixed per provider because it is baked into the redirect URI
  registered with them. If it is busy we say so rather than silently picking
  another one that the provider will reject.
* ``state`` is compared with hmac.compare_digest.
* Request reading is capped, so a local process cannot hold the flow open by
  dribbling headers at us forever.
* The success page is self-contained. No fonts, no analytics, no requests back
  out - the browser has an auth code in its address bar at this point.
"""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from ..errors import AuthCancelled, AuthError, AuthTimeout
from ..logging_setup import get_logger

log = get_logger("auth.loopback")

MAX_REQUEST_BYTES = 16 * 1024
READ_TIMEOUT = 10.0
HOST = "127.0.0.1"


@dataclass(slots=True, repr=False)
class CallbackResult:
    code: str
    state: str

    def __repr__(self) -> str:
        return "<CallbackResult>"


def _page(title: str, message: str, accent: str) -> bytes:
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         font:16px/1.5 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif;
         background:#0f1115; color:#e6e8ec; }}
  .card {{ max-width:26rem; padding:2.5rem; text-align:center; }}
  .dot {{ width:.6rem; height:.6rem; border-radius:50%; background:{accent};
          display:inline-block; margin-right:.5rem; vertical-align:middle; }}
  h1 {{ font-size:1.1rem; font-weight:600; margin:0 0 .5rem; }}
  p {{ margin:0; color:#9aa2ae; font-size:.9rem; }}
  @media (prefers-color-scheme: light) {{
    body {{ background:#fafafa; color:#16181d; }} p {{ color:#5c6470; }}
  }}
</style></head>
<body><div class="card">
  <h1><span class="dot"></span>{title}</h1>
  <p>{message}</p>
</div></body></html>"""
    return html.encode("utf-8")


OK_PAGE = _page("Signed in", "You can close this tab and go back to the terminal.", "#3fb950")
DENIED_PAGE = _page("Sign-in cancelled", "Nothing was changed. You can close this tab.", "#d29922")
ERROR_PAGE = _page("Something went wrong", "Go back to the terminal for details.", "#f85149")


class LoopbackReceiver:
    """One-shot listener. Use it as an async context manager."""

    def __init__(self, port: int, path: str = "/callback", *, timeout: float = 300.0):
        self.port = port
        self.path = path
        self.timeout = timeout
        self._server: asyncio.AbstractServer | None = None
        self._result: asyncio.Future[CallbackResult] | None = None
        self._expected_state: str = ""

    @property
    def redirect_uri(self) -> str:
        # localhost rather than 127.0.0.1 because that is what the providers
        # have registered, and the two are not interchangeable to them.
        return f"http://localhost:{self.port}{self.path}"

    async def __aenter__(self) -> LoopbackReceiver:
        loop = asyncio.get_running_loop()
        self._result = loop.create_future()
        try:
            self._server = await asyncio.start_server(self._handle, HOST, self.port)
        except OSError as exc:
            raise AuthError(
                f"cannot bind {HOST}:{self.port}",
                friendly=(
                    f"Port {self.port} is already in use. Close the other sign-in "
                    "window (or the CLI holding it) and try again."
                ),
            ) from exc
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # pragma: no cover
                pass
            self._server = None
        if self._result is not None and not self._result.done():
            self._result.cancel()

    async def wait(self, expected_state: str) -> CallbackResult:
        self._expected_state = expected_state
        assert self._result is not None, "use LoopbackReceiver as a context manager"
        try:
            return await asyncio.wait_for(self._result, timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise AuthTimeout() from exc

    # -- connection handling --------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await self._read_request_line(reader)
            if request_line is None:
                await self._respond(writer, 400, ERROR_PAGE)
                return

            method, target = request_line
            if method != "GET":
                await self._respond(writer, 405, ERROR_PAGE)
                return

            split = urlsplit(target)
            if split.path != self.path:
                # Browsers ask for /favicon.ico. Do not treat that as the callback.
                await self._respond(writer, 404, ERROR_PAGE)
                return

            params = parse_qs(split.query, keep_blank_values=True)
            await self._complete(params, writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass
        except Exception:
            log.exception("unexpected failure in the callback handler")
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _read_request_line(self, reader: asyncio.StreamReader) -> tuple[str, str] | None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=READ_TIMEOUT)
        except (asyncio.TimeoutError, ValueError):
            return None
        if not line or len(line) > MAX_REQUEST_BYTES:
            return None

        parts = line.decode("latin-1", errors="replace").split()
        if len(parts) < 2:
            return None

        # Drain the headers so the browser is not left mid-write, but never
        # read more than MAX_REQUEST_BYTES in total.
        remaining = MAX_REQUEST_BYTES - len(line)
        while remaining > 0:
            try:
                header = await asyncio.wait_for(reader.readline(), timeout=READ_TIMEOUT)
            except (asyncio.TimeoutError, ValueError):
                break
            if not header or header in (b"\r\n", b"\n"):
                break
            remaining -= len(header)

        return parts[0].upper(), parts[1]

    async def _complete(self, params: dict[str, list[str]], writer: asyncio.StreamWriter) -> None:
        if self._result is None or self._result.done():
            await self._respond(writer, 200, OK_PAGE)
            return

        error = (params.get("error") or [""])[0]
        if error:
            description = (params.get("error_description") or [""])[0]
            page = DENIED_PAGE if error == "access_denied" else ERROR_PAGE
            await self._respond(writer, 200, page)
            if error == "access_denied":
                self._result.set_exception(AuthCancelled())
            else:
                # error_description comes from the provider; it is safe to show
                # but we cap it so a hostile one cannot spam the UI.
                detail = unquote(description)[:200] or error
                self._result.set_exception(
                    AuthError(f"provider returned {error}", friendly=f"Sign-in failed: {detail}")
                )
            return

        code = (params.get("code") or [""])[0]
        state = (params.get("state") or [""])[0]

        if not code:
            await self._respond(writer, 400, ERROR_PAGE)
            self._result.set_exception(
                AuthError(
                    "callback had no code",
                    friendly="The provider did not send an authorisation code.",
                )
            )
            return

        if not hmac.compare_digest(state, self._expected_state):
            await self._respond(writer, 400, ERROR_PAGE)
            log.warning("rejected a callback whose state did not match")
            self._result.set_exception(
                AuthError(
                    "state mismatch",
                    friendly="The sign-in response did not match the request and was rejected.",
                )
            )
            return

        await self._respond(writer, 200, OK_PAGE)
        self._result.set_result(CallbackResult(code=code, state=state))

    async def _respond(self, writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}.get(
            status, "OK"
        )
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Referrer-Policy: no-referrer\r\n"
            "X-Content-Type-Options: nosniff\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        writer.write(head + body)
        try:
            await writer.drain()
        except (ConnectionError, RuntimeError):
            pass
