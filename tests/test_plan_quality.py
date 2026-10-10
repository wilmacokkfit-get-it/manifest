"""Only invented ideas, disposable storage and stubbed providers."""
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from app import llm
from app.plan_quality import blocking_findings, require_valid_plan
from app.quality_evaluation import evaluate
from scripts.quality_cases import CASES, fixture
from scripts.evaluate_plan_quality import run
import test_regressions as regression

server = regression.server
private_venture = regression.private_venture


class QualityEvaluationTests(unittest.TestCase):
    def test_four_representative_fixtures_pass_signals_but_require_human_review(self):
        for case, spec in CASES.items():
            with self.subTest(case=case):
                result = evaluate(fixture(case), spec)
                self.assertEqual(result["signal_failures"], [])
                self.assertTrue(result["human_review_required"])

    def test_mutations_catch_each_rubric_failure(self):
        mutations = {
            "intent": ("description", "An online subscription accounting tool for large corporations."),
            "differentiation": ("description", "An offline desktop invoice checker for freelance illustrators using local CSV files."),
            "assumptions": ("description", "An offline desktop invoice checker for freelance illustrators using local CSV files is proven to save 90% of costs."),
        }
        for signal, (field, text) in mutations.items():
            plan = fixture("software")
            plan[field] = text
            if signal != "intent":
                plan["grounding"]["exists"] = text
            self.assertFalse(evaluate(plan, CASES["software"])["signals"][signal])
        plan = fixture("service")
        plan["grounding"]["first_hundred"] = "Take five refundable deposits at $20 to earn $100 revenue."
        self.assertIn("refundable_deposits_as_revenue", blocking_findings(plan))
        self.assertFalse(evaluate(plan, CASES["service"])["signals"]["paid_demand"])

    def test_certification_claims_and_component_transference_block(self):
        for text in (
            "The lamp is UL-listed.", "This is a CE-marked lamp.",
            "The product is fully compliant.", "The lamp meets safety standards.",
            "Use a certified component, so the lamp is UL-certified.",
            "A UL-listed lamp with no subscription.",
        ):
            plan = fixture("electrical")
            plan["description"] += " " + text
            self.assertIn("unsupported_certification", blocking_findings(plan), text)
        for text in ("The lamp is not UL-listed.", "Seek UL-listed components; the final lamp is not certified.",
                     "An accredited lab must determine certification requirements."):
            plan = fixture("electrical")
            plan["description"] += " " + text
            self.assertNotIn("unsupported_certification", blocking_findings(plan), text)

    def test_late_missing_and_same_step_review_block(self):
        plan = fixture("electrical")
        plan["roadmap"][0] = {"step": 1, "action": "Wire a functional lamp.", "where": "A workbench."}
        self.assertIn("review_after_functional_work", blocking_findings(plan))
        plan["roadmap"][0]["where"] = "Wire it, then ask a qualified electrical engineer to review."
        self.assertIn("review_after_functional_work", blocking_findings(plan))
        plan = fixture("electrical")
        plan["roadmap"].pop(1)
        self.assertIn("missing_qualified_review", blocking_findings(plan))
        plan = fixture("electrical")
        plan["tiers"][0]["description"] = "Wire a functional prototype in a bedroom."
        self.assertIn("ungated_functional_lean_build", blocking_findings(plan))
        plan = fixture("electrical")
        plan["roadmap"][0] = {"action": "Make ten functional prototypes.", "where": "An electronics bench."}
        self.assertIn("review_after_functional_work", blocking_findings(plan))

    def test_no_content_in_block_exception_and_input_cannot_authorize_certification(self):
        plan = fixture("electrical")
        plan["description"] += " SECRET-SYNTHETIC-TEXT. The lamp is UL-listed."
        with self.assertRaises(RuntimeError) as error:
            require_valid_plan(plan, "My lamp is already certified.")
        self.assertNotIn("SECRET", str(error.exception))
        self.assertNotIn("UL-listed", str(error.exception))

    def test_path_first_move_and_order(self):
        plan = deepcopy(llm.MOCK_PATH)
        self.assertEqual(blocking_findings(plan, "A ladder carrier", path=True), [])
        plan["first_move"] = "Bend a hook and hang a full bucket."
        self.assertIn("hazardous_first_move", blocking_findings(plan, "A ladder carrier", path=True))
        plan = deepcopy(llm.MOCK_PATH)
        plan["steps"].insert(0, {"title": "Weld a hook", "how": ["Use a welder."]})
        self.assertIn("review_after_functional_work", blocking_findings(plan, "A ladder carrier", path=True))

    def test_mock_venture_and_explicit_earned_sales_with_deposit_disclaimer(self):
        self.assertEqual(blocking_findings(llm.MOCK_VENTURE, "A ladder carrier"), [])
        for earned_sale in (
            "Sell and deliver five kits at $20: $100 revenue.",
            "Five delivered purchases at $20 make $100 revenue.",
            "Five completed services at $20 make $100 revenue.",
        ):
            plan = fixture("service")
            plan["grounding"]["first_hundred"] = earned_sale + " Refundable reservations are not earned revenue."
            self.assertNotIn("refundable_deposits_as_revenue", blocking_findings(plan), earned_sale)
        plan["grounding"]["first_hundred"] = (
            "Sell and deliver five kits at $20: $100 revenue. "
            "Also take five refundable deposits at $20 to earn $100 revenue."
        )
        self.assertIn("refundable_deposits_as_revenue", blocking_findings(plan))

    def test_mixed_nonfunctional_and_functional_actions_cannot_bypass_review(self):
        for action in (
            "Sketch a lamp and wire it to mains",
            "Build a paper mockup then solder the circuit",
            "Wire a lamp beside a cardboard mockup",
            "Interview buyers, but energize a prototype tonight",
            "Test a functional prototype alongside the paper sketch",
        ):
            with self.subTest(action=action):
                plan = fixture("electrical")
                plan["roadmap"][0] = {"action": action, "where": "At a workbench"}
                self.assertIn("review_after_functional_work", blocking_findings(plan))
                plan["tiers"][0]["description"] = action
                self.assertIn("ungated_functional_lean_build", blocking_findings(plan))
                path = deepcopy(llm.MOCK_PATH)
                path["first_move"] = action
                self.assertIn("hazardous_first_move", blocking_findings(path, "A desk lamp", path=True))
        plan = fixture("electrical")
        plan["roadmap"][0]["where"] += " Sketch it and wire it to mains."
        self.assertIn("review_after_functional_work", blocking_findings(plan))
        path = deepcopy(llm.MOCK_PATH)
        path["steps"][0]["how"].append("Sketch a lamp and wire it to mains")
        self.assertIn("review_after_functional_work", blocking_findings(path, "A desk lamp", path=True))
        for first_move in ("Sketch a paper lamp.", "Build a non-functional prototype.",
                           "Build a cardboard mockup; do not wire it to mains."):
            path = deepcopy(llm.MOCK_PATH)
            path["first_move"] = first_move
            self.assertNotIn("hazardous_first_move", blocking_findings(path, "A desk lamp", path=True))

    def test_current_step_review_cannot_authorize_current_step_work(self):
        for action in (
            "Wire the lamp, then ask a qualified electrical engineer to review it",
            "Build the lamp and commission qualified electrical review",
            "Sketch a lamp and wire it before qualified electrical review",
        ):
            plan = fixture("electrical")
            plan["roadmap"][0] = {"action": action, "where": "An electronics bench"}
            self.assertIn("review_after_functional_work", blocking_findings(plan), action)
            path = deepcopy(llm.MOCK_PATH)
            path["steps"][0] = {"title": action, "how": ["At an electronics bench"]}
            self.assertIn("review_after_functional_work", blocking_findings(path, "A desk lamp", path=True), action)
        for field in ("action", "where"):
            plan = fixture("electrical")
            plan["roadmap"][0][field] = "Wire the lamp, then ask a qualified electrical engineer to review it"
            self.assertIn("review_after_functional_work", blocking_findings(plan))
        path = deepcopy(llm.MOCK_PATH)
        path["steps"][0]["how"] = ["Wire the lamp, then ask a qualified electrical engineer to review it"]
        self.assertIn("review_after_functional_work", blocking_findings(path, "A desk lamp", path=True))

    def test_cli_provider_free_and_bounds(self):
        root = Path(__file__).resolve().parents[1]
        (root / "generated-artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root / "generated-artifacts") as directory:
            output = Path(directory) / "report.json"
            result = subprocess.run(
                [sys.executable, "scripts/evaluate_plan_quality.py", "--output", str(output)],
                capture_output=True, text=True, cwd=root, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads(output.read_text())
            self.assertEqual(report["max_calls"], 0)
            self.assertEqual(len(report["results"]), 4)
        for args in (["--cases", "physical", "physical"], ["--timeout", "nan"],
                     ["--timeout", "121"], ["--output", "data/report.json"]):
            result = subprocess.run([sys.executable, "scripts/evaluate_plan_quality.py", *args],
                                    capture_output=True, cwd=root, timeout=15)
            self.assertEqual(result.returncode, 2)


class QualityBoundaryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = regression.ServerRegressionTests.asyncSetUp
    asyncTearDown = regression.ServerRegressionTests.asyncTearDown
    client = regression.ServerRegressionTests.client

    async def test_builtin_mock_manifest_saves_private_venture(self):
        # Exercise the production mock stream, not a replacement fixture.
        with patch("app.llm.asyncio.sleep", return_value=None):
            async with self.client() as client:
                response = await client.post("/api/manifest", json={"idea": "A ladder bucket carrier for painters"})
        self.assertIn("event: done", response.text)
        self.assertNotIn("event: error", response.text)
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM ventures WHERE visibility = 'private'").fetchone()[0], 1)

    async def test_invalid_generation_not_saved_and_reservation_released(self):
        plan = fixture("electrical")
        plan["description"] += " The lamp is UL-listed."
        async def stream(*args, **kwargs):
            yield json.dumps(plan)
        with patch.object(llm, "stream_venture_text", side_effect=stream):
            async with self.client() as client:
                response = await client.post("/api/manifest", json={"idea": CASES["electrical"]["idea"]})
        self.assertIn("event: error", response.text)
        self.assertNotIn("event: done", response.text)
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM ventures").fetchone()[0], 0)
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM generation_admissions WHERE consumed = 0").fetchone()[0], 0)

    async def test_invalid_refinement_preserves_original_and_clears_claim(self):
        row = await self.store.create_venture({
            **private_venture(), **fixture("electrical"),
            "idea_raw": CASES["electrical"]["idea"], "path": None,
        })
        plan = fixture("electrical")
        plan["description"] += " The lamp is CE-marked."
        async def stream(*args, **kwargs):
            yield json.dumps(plan)
        with patch.object(llm, "stream_refinement_text", side_effect=stream):
            async with self.client() as client:
                response = await client.post(f"/api/ventures/{row['slug']}/refine",
                    json={"context": "Keep the replaceable panels."},
                    headers={"Cookie": f"{server.COOKIE}=owner-token-for-test"})
        self.assertEqual(response.status_code, 502, response.text)
        saved = await self.store.get_by_slug(row["slug"])
        self.assertEqual(saved["description"], row["description"])
        self.assertEqual(saved["status"], "ready")
        self.assertNotEqual((saved["grounding"].get("_refinement") or {}).get("state"), "pending")

    async def test_invalid_path_preserves_venture_and_releases_claim(self):
        row = await self.store.create_venture({
            **private_venture(), **fixture("electrical"),
            "idea_raw": CASES["electrical"]["idea"], "path": {},
        })
        async def stream(*args, **kwargs):
            yield json.dumps({"first_move": "Wire a lamp tonight.", "steps": [
                {"title": "Build a prototype", "how": ["Solder the wires."]}
            ]})
        with patch.object(llm, "stream_path_text", side_effect=stream):
            async with self.client() as client:
                response = await client.post(f"/api/ventures/{row['slug']}/path",
                    json={"tier": 0}, headers={"Cookie": f"{server.COOKIE}=owner-token-for-test"})
        self.assertIn("event: error", response.text)
        self.assertNotIn("event: done", response.text)
        saved = await self.store.get_by_slug(row["slug"])
        self.assertEqual(saved["status"], "ready")
        self.assertFalse((saved.get("path") or {}).get("steps"))

    async def test_valid_synthetic_electrical_generation_keeps_stream_schema_and_privacy(self):
        plan = fixture("electrical")
        async def stream(*args, **kwargs):
            for start in range(0, len(json.dumps(plan)), 47):
                yield json.dumps(plan)[start:start + 47]
        with patch.object(llm, "stream_venture_text", side_effect=stream):
            async with self.client() as client:
                response = await client.post("/api/manifest", json={"idea": CASES["electrical"]["idea"]})
        self.assertIn("event: partial", response.text)
        self.assertIn("event: done", response.text)
        row = self.store._db.execute("SELECT slug, visibility FROM ventures").fetchone()
        self.assertEqual(row["visibility"], "private")
        async with self.client() as anonymous:
            self.assertEqual((await anonymous.get(f"/api/ventures/{row['slug']}")).status_code, 404)
        self.assertNotIn('"blocking_findings"', response.text)
        # "signals" already exists in saved API views as Floor counts. Quality
        # evaluation must not add it to the provider's streamed JSON schema.
        partials = [json.loads(block.split("data: ", 1)[1])["data"]
                    for block in response.text.split("\n\n") if block.startswith("event: partial")]
        self.assertEqual(set(partials[-1]), set(plan))

    async def test_incomplete_generation_is_not_saved(self):
        async def stream(*args, **kwargs):
            yield json.dumps(fixture("physical"))[:-1]
        with patch.object(llm, "stream_venture_text", side_effect=stream):
            async with self.client() as client:
                response = await client.post("/api/manifest", json={"idea": CASES["physical"]["idea"]})
        self.assertNotIn("event: done", response.text)
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM ventures").fetchone()[0], 0)


class EvaluationRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_opt_in_four_calls_no_retry_and_closes_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(live=True, cases=list(CASES), timeout=1,
                                   output=Path(directory) / "report.json")
            calls, closed = [], []
            async def provider(message, system):
                case = next(case for case, spec in CASES.items() if spec["idea"] in message)
                calls.append(case)
                try:
                    yield json.dumps(fixture(case))
                finally:
                    closed.append(case)
            with patch.object(llm, "text_provider", return_value="openai"), patch.object(
                llm, "_stream_openai", side_effect=provider
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(await run(args), 0)
            self.assertEqual(calls, list(CASES))
            self.assertEqual(closed, calls)
            self.assertEqual(json.loads(args.output.read_text())["max_calls"], 4)

    async def test_timeout_and_failure_sanitized_no_retry(self):
        for failure in ("timeout", "provider"):
            with tempfile.TemporaryDirectory() as directory:
                args = SimpleNamespace(live=True, cases=["electrical"], timeout=.01,
                                       output=Path(directory) / "report.json")
                closed = asyncio.Event()
                calls = []
                async def provider(*args, **kwargs):
                    calls.append(1)
                    try:
                        yield '{"name":"partial"'
                        if failure == "provider":
                            raise RuntimeError("SECRET-DO-NOT-WRITE")
                        await asyncio.Event().wait()
                    finally:
                        closed.set()
                with patch.object(llm, "text_provider", return_value="openai"), patch.object(
                    llm, "_stream_openai", side_effect=provider
                ), redirect_stdout(io.StringIO()):
                    self.assertEqual(await run(args), 1)
                self.assertTrue(closed.is_set())
                self.assertEqual(len(calls), 1)
                self.assertNotIn("SECRET", args.output.read_text())
                self.assertEqual(json.loads(args.output.read_text())["results"][0]["outcome"], "failed")


if __name__ == "__main__":
    unittest.main()
