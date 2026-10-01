"""Disposable UI fixtures; run with `python tests/refinement_preview.py`.

Only listens on localhost:5001. Never uses workspace or Supabase data.
"""
import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_refinement import OWNER, server, venture, refined_output
from app.store import SQLiteStore


async def prepare():
    data = TemporaryDirectory(prefix="manifest-refinement-preview-")
    server.store = SQLiteStore(data.name)
    server.TRUST_VISITOR_HEADER = False
    fixtures = [
        venture(slug="preview-available"),
        venture(slug="preview-waiting", images_status="pending"),
        venture(slug="preview-completed", grounding={"_refinement": {"state": "completed", "context": "Under $50."}}),
        venture(slug="preview-skipped", grounding={"_refinement": {"state": "skipped"}}),
    ]
    for row in fixtures:
        await server.store.create_venture(row)
    return data


async def fake_refinement(v, context):
    await asyncio.sleep(0.2)
    yield json.dumps(refined_output())


async def fixture_app(scope, receive, send):
    if scope["type"] == "http":
        scope = dict(scope)
        scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() != b"cookie"]
        scope["headers"].append((b"cookie", f"{server.COOKIE}={OWNER}".encode()))
    await server.app(scope, receive, send)


if __name__ == "__main__":
    import uvicorn

    data = asyncio.run(prepare())
    server.llm.stream_refinement_text = fake_refinement
    server.llm.image_provider = lambda: "none"
    try:
        uvicorn.run(fixture_app, host="127.0.0.1", port=5001)
    finally:
        server.store._db.close()
        data.cleanup()