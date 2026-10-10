"""Short loopback smoke test of the real-clock runner; NOT hosting evidence."""
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
import uvicorn
from app.wait_probe import router

ROOT = Path(__file__).resolve().parent.parent


class WaitProbeNetworkTests(unittest.TestCase):
    def test_isolated_runner_raw_and_creator_feedback(self):
        app = FastAPI()
        app.include_router(router)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        directory = ROOT / "generated-artifacts/published-waits"
        before = set(directory.glob("*.json"))
        with patch.dict(os.environ, {"MANIFEST_WAIT_PROBE_UNTIL": str(time.time() + 300)}):
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(0.02)
                self.assertTrue(server.started)
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
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                sock.close()


if __name__ == "__main__":
    unittest.main()
