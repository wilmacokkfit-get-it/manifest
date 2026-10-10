"""Synthetic MANIFEST evaluation: provider-free by default; --live opts into at
most four sequential paid text calls, each bounded to 120 seconds. No app store,
real user ideas, images, automatic retries, or provider switching.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import aclosing
import json
import math
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import llm
from app.quality_evaluation import evaluate
from scripts.quality_cases import CASES, fixture


async def run(args):
    provider = llm.text_provider() if args.live else "fixture"
    if args.live and provider not in {"openai", "anthropic"}:
        raise SystemExit("Live evaluation requires an explicitly configured real text provider; no fallback.")
    report = {
        "mode": "live" if args.live else "provider-free",
        "provider": provider,
        "model": os.environ.get("LLM_MODEL") if args.live else None,
        "max_calls": len(args.cases) if args.live else 0,
        "timeout_seconds_per_call": args.timeout,
        "note": "Signals are not a safety verdict. Apply the human rubric to every output.",
        "results": [],
    }
    async def collect(case):
        chunks = []
        chars = 0
        async with asyncio.timeout(args.timeout), aclosing(
            llm.stream_venture_text(CASES[case]["idea"], angle=None, avoid=[])
        ) as stream:
            async for chunk in stream:
                chunks.append(chunk)
                chars += len(chunk)
                if chars > 128_000:
                    raise ValueError("output_limit")
        result = json.loads("".join(chunks))
        if not isinstance(result, dict):
            raise ValueError("invalid_object")
        return result

    for case in args.cases:
        result = {"case": case}
        try:
            if args.live:
                plan = await collect(case)
            else:
                async def synthetic_stream(_idea):
                    text = json.dumps(fixture(case))
                    for start in range(0, len(text), 37):
                        yield text[start:start + 37]
                with patch.object(llm, "text_provider", return_value="mock"), patch.object(
                    llm, "_stream_mock", side_effect=synthetic_stream
                ):
                    plan = await collect(case)
            result.update(output=plan, evaluation=evaluate(plan, CASES[case]), outcome="completed")
        except Exception as exc:
            # Never put provider exception bodies or request headers in reports.
            result.update(outcome="failed", error_type=type(exc).__name__)
        report["results"].append(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps({k: v for k, v in result.items() if k != "output"}), flush=True)
    return int(any(
        result["outcome"] != "completed"
        or result.get("evaluation", {}).get("signal_failures")
        for result in report["results"]
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Authorize at most four paid synthetic text calls.")
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path, default=Path("generated-artifacts/plan-quality.json"))
    args = parser.parse_args()
    if len(args.cases) != len(set(args.cases)) or not math.isfinite(args.timeout) or not 0 < args.timeout <= 120:
        parser.error("Use unique cases and a finite timeout between 0 and 120 seconds.")
    root = Path(__file__).resolve().parents[1] / "generated-artifacts"
    if not args.output.resolve().is_relative_to(root.resolve()):
        parser.error("Output must be inside generated-artifacts (excluded from Git and deployment).")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
