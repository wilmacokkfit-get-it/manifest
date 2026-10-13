"""Private, approval-driven preparation. No external side effects or fake completion."""
from copy import deepcopy
from datetime import datetime, timezone
import json

from fastapi import HTTPException
from app.plan_quality import blocking_findings


def running(automation: dict) -> bool:
    if automation.get("state") != "running":
        return False
    try:
        until = datetime.fromisoformat(automation["lease_until"].replace("Z", "+00:00"))
        return until > datetime.now(timezone.utc)
    except (KeyError, ValueError, TypeError):
        return False


async def edit_step(store, venture_id, n, change):
    """CAS the entire path, retrying unrelated note/step edits, never stale writes."""
    for _ in range(4):
        venture = await store.get_by_id(venture_id)
        if not venture:
            raise HTTPException(404, "The venture no longer exists.")
        original = venture.get("path")
        path = deepcopy(original)
        if not isinstance(path, dict):
            raise HTTPException(409, "Choose a build path first.")
        step = next((s for s in path.get("steps", []) if s.get("n") == n), None)
        if not step:
            raise HTTPException(404, "No such step.")
        change(step)
        saved = await store.compare_path(venture_id, original, path)
        if saved:
            return saved
    raise HTTPException(409, "Another change arrived. Refresh before trying again.")


def packet(text: str) -> dict:
    """Accept complete bounded work, not claims that external work was executed."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as e:
        raise ValueError("The engine returned incomplete preparation. Please try again.") from e
    if not isinstance(data, dict):
        raise ValueError("The engine returned no preparation.")
    summary = data.get("summary")
    items = data.get("deliverables")
    if not isinstance(summary, str) or not 15 <= len(summary.strip()) <= 1800:
        raise ValueError("The preparation summary is incomplete.")
    if not isinstance(items, list) or not 1 <= len(items) <= 6:
        raise ValueError("The engine returned no usable deliverables.")
    out = {"summary": summary.strip(), "deliverables": []}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("The deliverable is incomplete.")
        title, content = item.get("title"), item.get("content")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
            raise ValueError("The deliverable needs a title.")
        if not isinstance(content, str) or not 40 <= len(content.strip()) <= 14000:
            raise ValueError("The deliverable needs complete content.")
        out["deliverables"].append({"title": title.strip(), "content": content.strip()})
    for field in ("human_handoff", "limitations"):
        values = data.get(field)
        if (not isinstance(values, list) or not 1 <= len(values) <= 8
                or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 1400 for v in values)):
            raise ValueError("The preparation must explain its handoff and limitations.")
        out[field] = [v.strip() for v in values]
    if len(json.dumps(out)) > 42000:
        raise ValueError("The preparation was too large to save safely.")
    findings = blocking_findings({"description": json.dumps(out, ensure_ascii=False)})
    if {"unsupported_certification", "unsupported_guarantee"} & set(findings):
        raise ValueError("The preparation made an unsupported certification or guarantee claim.")
    # These capabilities do not exist in this worker. Never trust model-provided
    # statuses, recipients, links, purchases or completion markers.
    out["limitations"].insert(0, "AI-prepared drafts, not verified research. No messages were sent, money spent, testing performed, or company bids obtained.")
    return out
