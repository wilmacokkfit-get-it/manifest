"""Prompt contract checks, without provider calls or application database access."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import architect, llm


class CreationStandardTests(unittest.IsolatedAsyncioTestCase):
    def test_shared_standard_reaches_generation_refinement_and_selected_mix(self):
        for prompt in (
            llm.SYSTEM_PROMPT,
            llm.REFINEMENT_SYSTEM_PROMPT,
            llm.PRESERVE_SYSTEM_PROMPT,
        ):
            with self.subTest(prompt=prompt[-70:]):
                self.assertIn("CREATION STANDARD:", prompt)
                self.assertIn("hardest unproven assumption", prompt)
                self.assertIn("pass/fail", prompt)
                self.assertIn("has not performed live market research", prompt)
                self.assertIn("qualified review", prompt)
                self.assertIn("do not add scores, extra keys", prompt)
                self.assertIn('"image_prompts"', prompt)
                self.assertIn("revenue is not profit", prompt)
                self.assertIn("Keep the twists, merges and lateral invention", prompt)
                self.assertIn("better than OR equal to the normal way", prompt)
                self.assertIn("tradeoff or failure condition", prompt)
                self.assertIn("explicitly call the explanation a hypothesis", prompt)

    def test_chosen_concept_and_refinement_do_not_inherit_random_variety(self):
        self.assertNotIn("VARIETY IS MANDATORY.", llm.PRESERVE_SYSTEM_PROMPT)
        self.assertNotIn("VARIETY IS MANDATORY.", llm.REFINEMENT_SYSTEM_PROMPT)
        self.assertIn("Develop THAT concept faithfully", llm.PRESERVE_SYSTEM_PROMPT)
        self.assertIn("THAT SAME venture", llm.REFINEMENT_SYSTEM_PROMPT)
        self.assertIn("An ANGLE is secondary", llm.SYSTEM_PROMPT)
        self.assertIn("The FIRST roadmap step", llm.SYSTEM_PROMPT)
        self.assertIn("BEFORE functional construction", llm.SYSTEM_PROMPT)
        self.assertIn("unnecessary monthly parts", llm.SYSTEM_PROMPT)

    def test_path_has_evidence_gates_and_respects_resources(self):
        self.assertIn("selected tier's scope and resources", llm.PATH_SYSTEM_PROMPT)
        self.assertIn('relevant "done_when"', llm.PATH_SYSTEM_PROMPT)
        self.assertIn("qualified review", llm.PATH_SYSTEM_PROMPT)

    def test_architect_and_splices_preserve_unproven_twists_with_comparisons(self):
        for prompt in (architect.ARCHITECT_SYSTEM, architect.CROSSBREED_SYSTEM):
            with self.subTest(prompt=prompt[:60]):
                self.assertIn("hypothes", prompt)
                self.assertIn("normal way", prompt)
                self.assertIn("tradeoff", prompt)
                self.assertIn("side-by-side test", prompt)
                self.assertIn("qualified review", prompt)
                self.assertIn("invent measured gains" if prompt == architect.ARCHITECT_SYSTEM
                              else "fabricate measured gains", prompt)

    def test_architect_cache_tracks_generation_contract_not_just_floor_data(self):
        facts = {"counts": {"ventures": 3}, "_private": "excluded"}
        current = architect.facts_hash(facts)
        self.assertEqual(current, architect.facts_hash({**facts, "_private": "different"}))
        with patch.object(architect, "ARCHITECT_SYSTEM", "different comparison standard"):
            self.assertNotEqual(current, architect.facts_hash(facts))

    async def test_streaming_sends_standard_and_preserves_selected_concept(self):
        calls = []

        async def capture(message, system):
            calls.append((message, system))
            yield '{"name":"Test"}'

        with patch.object(llm, "text_provider", return_value="openai"), patch.object(
            llm, "_stream_openai", side_effect=capture
        ):
            ordinary = [part async for part in llm.stream_venture_text(
                "A repairable garden tool", angle="a physical product",
                avoid=[{"name": "Previous", "tagline": "Old concept"}]
            )]
            selected = [part async for part in llm.stream_venture_text(
                "A refillable repair kit", preserve=True,
                avoid=[{"name": "Previous", "tagline": "Old concept"}]
            )]
        self.assertEqual(ordinary, ['{"name":"Test"}'])
        self.assertEqual(selected, ordinary)
        self.assertEqual(calls[0][1], llm.SYSTEM_PROMPT)
        self.assertIn("Previous", calls[0][0])
        self.assertIn("Optional ANGLE", calls[0][0])
        self.assertIn("creator's intent takes priority", calls[0][0])
        self.assertEqual(calls[1][1], llm.PRESERVE_SYSTEM_PROMPT)
        self.assertNotIn("Previous", calls[1][0])

    async def test_reasoning_budget_applies_to_generation_refinement_and_path(self):
        async def events():
            yield SimpleNamespace(type="response.output_text.delta", delta='{"name":"Test"}')
            yield SimpleNamespace(type="response.completed")

        client = SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(side_effect=lambda **kwargs: events())
            )
        )
        with patch.dict("os.environ", {"LLM_MODEL": "gpt-5-mini", "LLM_REASONING_EFFORT": "medium"}), patch.object(
            llm, "text_provider", return_value="openai"
        ), patch("openai.AsyncOpenAI", return_value=client):
            self.assertEqual(
                [part async for part in llm.stream_venture_text("A repairable garden tool")],
                ['{"name":"Test"}']
            )
            await self._collect(llm.stream_refinement_text(llm.MOCK_VENTURE, "For renters"))
            await self._collect(llm.stream_path_text(llm.MOCK_VENTURE, 0))
        self.assertEqual(client.responses.create.await_count, 3)
        for call in client.responses.create.await_args_list:
            self.assertEqual(call.kwargs["reasoning"], {"effort": "medium"})
            self.assertTrue(call.kwargs["stream"])

    async def test_other_models_do_not_receive_unsupported_reasoning_parameter(self):
        async def events():
            yield SimpleNamespace(type="response.output_text.delta", delta="{}")
            yield SimpleNamespace(type="response.completed")

        client = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock(return_value=events()))
        )
        with patch.dict("os.environ", {"LLM_MODEL": "gpt-4.1"}), patch(
            "openai.AsyncOpenAI", return_value=client
        ):
            await self._collect(llm._stream_openai("test"))
        self.assertNotIn("reasoning", client.responses.create.await_args.kwargs)

    async def _collect(self, stream):
        return [part async for part in stream]


if __name__ == "__main__":
    unittest.main()
