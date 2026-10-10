"""Opt-in, six-call maximum synthetic comparison; no server, database or images."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from contextlib import aclosing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import llm  # noqa: E402

CASES = {
    "pantry": "A reusable pantry label kit for renters to notice duplicate groceries, without cameras, electronics or a subscription.",
    "repairable": "A repairable desk light for small rented rooms, with replaceable parts and no subscription.",
}


def fixture(idea: str) -> dict:
    """Same input for both efforts; not an output from an earlier comparison call."""
    return {
        "idea_raw": idea, "name": "Shelf Tags", "tagline": "See what is already in the cupboard.",
        "description": "Reusable writable tags show pantry contents to renters before shopping.",
        "category": "Kitchen organization", "accent": "#ddad55",
        "tiers": [
            {"label": label, "title": "Reusable labels", "description": description}
            for label, description in zip(
                ("The Lean Build", "The Real Product", "The Empire"),
                ("Test paper labels with five renters for about $20.",
                 "Offer washable tags after paid demand; estimate $500 for a small batch.",
                 "Expand to shared kitchens only after repeat orders."),
            )
        ],
        "roadmap": [{"step": 1, "action": "Test a paper-label mockup.", "where": "Interview five renters; revise if fewer than three find duplicates."}],
        "grounding": {"exists": "Writable pantry labels already exist; the duplicate-shopping cue is a hypothesis.",
                      "who_pays": "Renters with crowded cupboards; test $10 per kit.",
                      "first_hundred": "Offer ten kits at $10 through a local renter group, if demand is demonstrated."},
        "image_prompts": ["Reusable yellow pantry tags on a dark backdrop.", "A renter checking a cupboard with reusable tags."],
    }


async def compare(args):
    provider = llm.text_provider()
    if not args.live:
        raise SystemExit("No calls made. Pass --live to authorize up to six paid text-only calls.")
    if provider != "openai":
        raise SystemExit("Comparison requires the configured OpenAI provider; it will not switch providers.")
    model = os.environ.get("LLM_MODEL", "gpt-5-mini")
    if not model.startswith("gpt-5"):
        raise SystemExit("Reasoning comparison requires a configured gpt-5 model.")
    if args.case != "pantry" and any(op != "venture" for op in args.operations):
        raise SystemExit("The repairable case is venture-only; refinement/path use the fixed pantry fixture.")
    report = {"provider": provider, "model": model, "case": args.case,
              "timeout_seconds": args.timeout, "results": []}
    original = os.environ.get("LLM_REASONING_EFFORT")
    try:
        for operation in args.operations:
            for effort in args.efforts:
                os.environ["LLM_REASONING_EFFORT"] = effort
                started = time.perf_counter()
                first = None
                chunks = []
                outcome, error = "completed", None
                v = fixture(CASES[args.case])
                stream = {
                    "venture": lambda: llm.stream_venture_text(CASES[args.case], angle=None, avoid=[]),
                    "refinement": lambda: llm.stream_refinement_text(v, "The tags must be removable without marking rented shelves. Keep it offline and subscription-free."),
                    "path": lambda: llm.stream_path_text(v, 0),
                }[operation]()
                try:
                    async with asyncio.timeout(args.timeout), aclosing(stream):
                        async for chunk in stream:
                            if chunk and first is None:
                                first = time.perf_counter() - started
                            chunks.append(chunk)
                except TimeoutError:
                    outcome, error = "timeout", "120-second text bound reached" if args.timeout == 120 else "Text bound reached"
                except Exception as exc:
                    outcome, error = "failed", type(exc).__name__
                text = "".join(chunks)
                try:
                    parsed = json.loads(text)
                    valid_json = isinstance(parsed, dict)
                except ValueError:
                    parsed, valid_json = None, False
                result = {"operation": operation, "effort": effort, "outcome": outcome,
                          "first_text_seconds": round(first, 3) if first is not None else None,
                          "elapsed_seconds": round(time.perf_counter() - started, 3),
                          "output_chars": len(text), "valid_json": valid_json,
                          "error": error, "output": parsed if valid_json else text}
                report["results"].append(result)
                print(json.dumps({k: val for k, val in result.items() if k != "output"}), flush=True)
                # Preserve already finished measurements if a later call is interrupted.
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    finally:
        if original is None:
            os.environ.pop("LLM_REASONING_EFFORT", None)
        else:
            os.environ["LLM_REASONING_EFFORT"] = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--case", choices=CASES, default="pantry")
    parser.add_argument("--operations", nargs="+", choices=("venture", "refinement", "path"), default=["venture", "refinement", "path"])
    parser.add_argument("--efforts", nargs="+", choices=("medium", "low"), default=["medium", "low"])
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", default="generated-artifacts/latency-comparison.json")
    args = parser.parse_args()
    if not 0 < args.timeout <= 120 or len(args.operations) != len(set(args.operations)) or len(args.efforts) != len(set(args.efforts)):
        parser.error("Use unique operations/efforts and a timeout between 0 and 120 seconds.")
    if not Path(args.output).resolve().is_relative_to((Path(__file__).resolve().parents[1] / "generated-artifacts").resolve()):
        parser.error("Output must be inside generated-artifacts (excluded from Git and deployment).")
    asyncio.run(compare(args))


if __name__ == "__main__":
    main()
