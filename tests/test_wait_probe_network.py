"""Real loopback diagnostics; no production server, providers, auth, or storage.

These short real-clock checks are NOT hosting or browser compatibility evidence.
"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import threading
import time
import unittest
from urllib.parse import parse_qs
from unittest.mock import patch
import uuid

from fastapi import FastAPI
import uvicorn
from app import wait_probe
from app.wait_probe import router

ROOT = Path(__file__).resolve().parent.parent
REQUEST_OBSERVER = ContextVar("wait_probe_request_observer", default=None)


class ProbeClient:
    """Disposable HTTP connection that can close before headers or mid-SSE."""

    def __init__(self, port, scenario, delay=2):
        self.probe_id = str(uuid.uuid4())
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.received = b""
        self.socket.sendall((
            f"GET /api/diagnostics/wait-probe?scenario={scenario}&delay={delay}"
            f"&probe_id={self.probe_id} HTTP/1.1\r\n"
            "Host: localhost\r\nConnection: close\r\n\r\n"
        ).encode())

    def read_until(self, marker):
        deadline = time.monotonic() + 4
        while marker not in self.received:
            self.socket.settimeout(max(0.01, deadline - time.monotonic()))
            chunk = self.socket.recv(65536)
            if not chunk:
                raise AssertionError(f"EOF before {marker!r}: {self.received!r}")
            self.received += chunk
        return self.received

    def read_all(self):
        self.socket.settimeout(4)
        while chunk := self.socket.recv(65536):
            self.received += chunk
        return self.received

    def close(self):
        self.socket.close()


class ObservedApp:
    """Observe outer ASGI request completion, not just the admission counter."""

    def __init__(self, app):
        self.app = app
        self.requests = {}
        self.tasks = []
        self.request_tasks = {}
        self.loop = None
        self.errors = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/diagnostics/wait-probe":
            return await self.app(scope, receive, send)
        probe_id = parse_qs(scope.get("query_string", b"").decode()).get(
            "probe_id", [f"unlabelled-{uuid.uuid4()}"])[0]
        record = {"finished": False, "status": None, "partial": False, "done": False,
                  "disconnect_before_headers": False, "exception": None}
        self.requests[probe_id] = record
        self.tasks.append(asyncio.current_task())
        self.request_tasks[probe_id] = [asyncio.current_task()]
        token = REQUEST_OBSERVER.set((self, probe_id))

        async def observe_receive():
            message = await receive()
            if message["type"] == "http.disconnect" and record["status"] is None:
                record["disconnect_before_headers"] = True
            return message

        async def observe_send(message):
            if message["type"] == "http.response.start":
                record["status"] = message["status"]
            body = message.get("body", b"")
            record["partial"] |= b"event: partial" in body
            record["done"] |= b"event: done" in body
            await send(message)

        try:
            await self.app(scope, observe_receive, observe_send)
        except Exception as exc:
            record["exception"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["finished"] = True
            REQUEST_OBSERVER.reset(token)

    async def snapshot(self):
        return {
            "active": wait_probe._active,
            "requests": {key: {**value, "pending_tasks": sum(
                not task.done() for task in self.request_tasks[key])}
                for key, value in self.requests.items()},
            "pending": [getattr(task.get_coro(), "__qualname__", type(task.get_coro()).__name__)
                        for task in self.tasks if not task.done()],
            "errors": list(self.errors),
        }

    def inspect(self):
        return asyncio.run_coroutine_threadsafe(self.snapshot(), self.loop).result(timeout=2)


@contextmanager
def isolated_server():
    app = FastAPI()

    @app.middleware("http")
    async def response_wrapper(request, call_next):
        # Match server.py's @app.middleware("http") / call_next / cookie shape
        # (BaseHTTPMiddleware cancellation scopes), without real identity/auth.
        request.state.owner = "synthetic-diagnostic-owner"
        response = await call_next(request)
        response.set_cookie("__Host-manifest_owner", request.state.owner,
                            httponly=True, samesite="lax", secure=True, path="/")
        return response

    app.include_router(router)
    observed = ObservedApp(app)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(observed, log_level="warning", lifespan="off"))

    async def serve():
        observed.loop = asyncio.get_running_loop()

        def task_factory(loop, coro, **kwargs):
            task = asyncio.Task(coro, loop=loop, **kwargs)
            # Track ALL descendants of each ASGI request, including AnyIO's
            # coroutine wrappers and async-generator finalizers. Coroutine-name
            # filtering misses middleware tasks wrapped by newer AnyIO versions.
            context = REQUEST_OBSERVER.get()
            if context is not None and context[0] is observed:
                observed.tasks.append(task)
                observed.request_tasks[context[1]].append(task)
            return task

        observed.loop.set_task_factory(task_factory)
        observed.loop.set_exception_handler(
            lambda loop, context: observed.errors.append(context["message"]))
        await server.serve(sockets=[sock])

    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            raise AssertionError("Isolated diagnostic server did not start")
        yield port, observed
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        if thread.is_alive():
            raise AssertionError("Isolated diagnostic server did not stop")


class WaitProbeNetworkTests(unittest.TestCase):
    def wait_for(self, observed, predicate, timeout=1):
        deadline = time.monotonic() + timeout
        while True:
            state = observed.inspect()
            if predicate(state):
                return state
            if time.monotonic() >= deadline:
                self.fail(f"Diagnostic tasks did not reach expected state: {state}")
            time.sleep(0.005)

    def assert_idle(self, observed):
        state = self.wait_for(observed, lambda s: (
            s["active"] == 0 and not s["pending"]
            and all(r["finished"] for r in s["requests"].values())))
        self.assertEqual(state["errors"], [])
        for record in state["requests"].values():
            if record["exception"] is not None:
                # BaseHTTPMiddleware raises when a response returns without
                # headers on a disconnected socket. Keep this real shape (and
                # its exception) visible rather than swallowing it in the app.
                self.assertTrue(record["disconnect_before_headers"], record)
                self.assertEqual(record["exception"], "RuntimeError: No response returned.")
        # Ensure task tracking really saw both endpoint child task types.
        names = [getattr(task.get_coro(), "__qualname__", "") for task in observed.tasks]
        self.assertTrue(any(name.endswith(".respond") for name in names))
        self.assertTrue(any(name.endswith(".disconnect") for name in names))
        self.assertTrue(any("BaseHTTPMiddleware" in task.get_name() for task in observed.tasks))

    def assert_rejected(self, port, observed, status, active):
        started = time.monotonic()
        client = ProbeClient(port, "json")
        try:
            response = client.read_all()
            self.assertTrue(response.startswith(f"HTTP/1.1 {status} ".encode()), response)
            # Far shorter than the requested two-second wait.
            self.assertLess(time.monotonic() - started, 0.75)
            state = self.wait_for(observed, lambda s: (
                s["requests"].get(client.probe_id, {}).get("finished")))
            self.assertEqual(state["active"], active)
        finally:
            client.close()

    def exercise_disconnect(self, scenario):
        self.assertEqual(wait_probe._active, 0)
        with patch.dict(os.environ, {"MANIFEST_WAIT_PROBE_UNTIL": str(time.time() + 60)}):
            with isolated_server() as (port, observed):
                clients = []
                try:
                    survivor = ProbeClient(port, "partial-gap")
                    cancelled = ProbeClient(port, scenario)
                    clients.extend((survivor, cancelled))
                    self.wait_for(observed, lambda s: s["active"] == 2)
                    survivor.read_until(b"event: partial")
                    if scenario == "partial-gap":
                        cancelled.read_until(b"event: partial")
                    else:
                        state = observed.inspect()
                        self.assertIsNone(state["requests"][cancelled.probe_id]["status"])
                        self.assertEqual(select.select([cancelled.socket], [], [], 0)[0], [])

                    self.assert_rejected(port, observed, 429, active=2)
                    cancelled.close()
                    state = self.wait_for(observed, lambda s: (
                        s["active"] == 1 and s["requests"][cancelled.probe_id]["finished"]
                        and s["requests"][cancelled.probe_id]["pending_tasks"] == 0))
                    self.assertFalse(state["requests"][cancelled.probe_id]["done"])
                    self.assertFalse(state["requests"][survivor.probe_id]["finished"])

                    # Refill the released slot while the original survivor is
                    # still waiting; a third request must STILL be rejected.
                    replacement = ProbeClient(port, "partial-gap", delay=3)
                    clients.append(replacement)
                    replacement.read_until(b"event: partial")
                    self.wait_for(observed, lambda s: s["active"] == 2)
                    self.assert_rejected(port, observed, 429, active=2)
                    self.assertIn(b"event: done", survivor.read_all())
                    self.wait_for(observed, lambda s: (
                        s["active"] == 1 and s["requests"][survivor.probe_id]["finished"]))

                    # The surviving request's cleanup must release exactly one
                    # slot too; a short fourth request can finish alongside it.
                    later = ProbeClient(port, "early-eof", delay=1)
                    clients.append(later)
                    self.assertTrue(later.read_all().startswith(b"HTTP/1.1 200 "))
                    state = self.wait_for(observed, lambda s: (
                        s["requests"][later.probe_id]["finished"]))
                    self.assertEqual(state["active"], 1)
                    response = replacement.read_all()
                    self.assertTrue(response.startswith(b"HTTP/1.1 200 "))
                    self.assertIn(b"event: done", response)
                    self.assert_idle(observed)
                finally:
                    for client in clients:
                        client.close()

    def test_overlapping_disconnect_before_headers_restores_exact_capacity(self):
        self.exercise_disconnect("first-byte")

    def test_overlapping_disconnect_after_partial_sse_restores_exact_capacity(self):
        self.exercise_disconnect("partial-gap")

    def test_gate_expiry_rejects_new_requests_without_cancelling_running_probe(self):
        self.assertEqual(wait_probe._active, 0)
        with patch.dict(os.environ, {"MANIFEST_WAIT_PROBE_UNTIL": str(time.time() + 60)}):
            with isolated_server() as (port, observed):
                running = ProbeClient(port, "partial-gap", delay=1)
                try:
                    running.read_until(b"event: partial")
                    self.wait_for(observed, lambda s: s["active"] == 1)
                    # Let a short real gate expire AFTER admission. Expiry is
                    # not a disconnect and must not tear down an admitted wait.
                    with patch.dict(os.environ, {
                        "MANIFEST_WAIT_PROBE_UNTIL": str(time.time() + 0.1),
                    }):
                        deadline = time.monotonic() + 1
                        while wait_probe.enabled() and time.monotonic() < deadline:
                            time.sleep(0.005)
                        self.assertFalse(wait_probe.enabled())
                        self.assert_rejected(port, observed, 404, active=1)
                        self.assertIn(b"event: done", running.read_all())
                        self.assert_idle(observed)
                        self.assert_rejected(port, observed, 404, active=0)
                        self.assert_idle(observed)
                finally:
                    running.close()

    def test_isolated_runner_raw_and_creator_feedback(self):
        directory = ROOT / "generated-artifacts/published-waits"
        before = set(directory.glob("*.json"))
        with patch.dict(os.environ, {"MANIFEST_WAIT_PROBE_UNTIL": str(time.time() + 300)}):
            with isolated_server() as (port, observed):
                result = subprocess.run([
                    "node", "scripts/probe-published-waits.cjs",
                    "--url", f"http://127.0.0.1:{port}", "--delay", "2",
                    "--phase", "all", "--local-test", "true",
                ], cwd=ROOT, text=True, capture_output=True, timeout=110)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                files = set(directory.glob("*.json")) - before
                self.assertEqual(len(files), 1)
                report = json.loads(next(iter(files)).read_text())
                self.assertTrue(report["localTest"])
                self.assertEqual(report["blocked"], [])
                raw = [r for r in report["results"] if r["kind"] == "raw"]
                self.assertEqual(len(raw), 8)
                self.assertEqual([r["outcome"] for r in raw],
                                 ["completed"] * 4 + ["early-eof"] + ["client-abort"] * 3)
                ui = [r for r in report["results"] if r["kind"] == "creator-ui"]
                self.assertEqual(len(ui), 4)
                self.assertTrue(all(r["complete"] and r["final"]["submissions"] == 1 for r in ui))
                self.assert_idle(observed)


if __name__ == "__main__":
    unittest.main()
