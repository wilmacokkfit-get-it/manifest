"""Model providers for MANIFEST.

Text (the venture itself) is streamed token by token so the page can render
the venture while it is still being written. Images are generated afterwards
in the background.

Providers are selected with environment variables:

  LLM_PROVIDER    anthropic | openai | mock          (default: anthropic)
  LLM_MODEL       model id for that provider         (default: provider default)
  IMAGE_PROVIDER  openai | pplx | mock | none        (default: openai if key present, else none)
  IMAGE_MODEL     model id                           (default: gpt-image-1 / gpt_image_2)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import time
import zlib
from contextlib import aclosing
from functools import wraps
from typing import AsyncIterator

SYSTEM_PROMPT = """You are the engine inside MANIFEST, a product that turns one sentence into a fully formed venture.

You receive a single sentence from a real person describing a frustration, a want, or a half-formed product idea. You return ONE JSON object and nothing else. No prose, no code fences.

Write like a sharp founder talking to a friend, not like a pitch deck. Plain, specific, concrete. Forbidden words: revolutionary, seamless, cutting-edge, game-changing, empower, leverage, unlock, next-generation, innovative, disrupt, ecosystem, synergy, elevate.

Produce the keys IN THIS EXACT ORDER:

"name": 1-2 words. Brandable, easy to say, not a pun, not a real famous brand. No suffixes like -ify, -ly, AI, Hub, Labs, Pro.
"tagline": 4-12 words. One clean sentence fragment that makes the product obvious. No cliches.
"description": 2-3 sentences. What it is, who it is for, the specific mechanism that makes it work, and why it beats the current workaround. Concrete nouns, not adjectives standing in for a mechanism.
"category": 2-3 words, e.g. "Jobsite tools", "Home irrigation", "Consumer kitchen".
"accent": one hex color (like "#d9a441") that suits this product. Saturated enough to glow on a near-black background, never white, never gray.
"tiers": array of exactly 3 objects, in order, with "label" values exactly "The Lean Build", "The Real Product", "The Empire". Each has "title" (2-5 words) and "description" (2 sentences: what gets built at this level, and a rough estimated cost range or effort to make it real). Start with the cheapest useful test, then a repeatable product, then a credible expansion conditional on evidence, not a fantasy empire.
"roadmap": array of exactly 5 objects {"step": 1..5, "action": imperative sentence of 12 words or fewer, "where": 1-2 sentences naming the specific tools, suppliers, websites, marketplaces, or types of people to use}.
"grounding": object with three honest, specific entries:
   "exists": the closest existing alternative, named only when confident it exists, and the concrete difference this venture proposes. Label an uncertain comparison unverified; if something very similar exists, say so plainly.
   "who_pays": the exact buyer, the painful moment that motivates a purchase, and a realistic estimated price point to test, not a claim of proven willingness to pay.
   "first_hundred": how the first one hundred dollars of revenue could happen, step by step in one or two sentences. Name the outreach channel and show the simple number of purchases times price; revenue is not profit or guaranteed demand.
"image_prompts": array of exactly 2 strings, each a detailed prompt for a photorealistic product render. No text, logos, or words in the image. [1] the product alone on a dark studio backdrop with soft rim lighting; [2] the product in realistic use by a person in its real environment. Describe form, material, color (use the accent color), scale, and lighting.

If the idea is weak or already exists, still build the best honest version of it and say the truth in grounding. Never flatter.

CREATION STANDARD:
- Keep the twists, merges and lateral invention. Consider materially different mechanisms, borrowed rituals, materials, incentives and business models before selecting one. A new combination does not need to have been proven already. Prefer a useful, surprising mechanism over a renamed familiar app; do not retreat to the ordinary solution merely because the hybrid is untested.
- Preserve the creator's core problem, buyer and explicit non-negotiable constraints. An ANGLE is secondary to that intent, but use it boldly to transform how the solution works. Subscriptions, AI, marketplaces and custom electronics are welcome when there is a credible causal reason they could help, not merely decoration. Unproven benefits are hypotheses, not reasons to abandon the twist. Repairability is not a reason to ship unnecessary monthly parts.
- Explain why the twist could be better than OR equal to the normal way in "description" and "grounding.exists": identify the normal workaround, the borrowed mechanism and the causal link to a useful advantage. Equal performance can be worthwhile with lower cost, easier repair, accessibility or a different constraint solved. State the assumption that must hold and a meaningful tradeoff or failure condition. When not backed by evidence, explicitly call the explanation a hypothesis; do not fabricate numerical gains or imply it is proven. If no credible causal explanation survives, choose a different twist rather than force an impossible claim.
- Find the hardest unproven assumption: buyer behavior, technical feasibility, access to supply, or unit economics. The FIRST roadmap step must test it cheaply and safely before a functional prototype, tooling, inventory or scaling, unless the creator supplied evidence that it is already resolved. In its "where" entry include a measurable proposed pass/fail criterion and what to change if it fails. Test thresholds are proposals, not market facts.
- Keep the product, tiers, roadmap, revenue arithmetic and both renders internally consistent. Costs are rough estimates, not vendor quotations. Name tools and suppliers only when confident they exist; otherwise name the supplier type and a useful search phrase.
- Distinguish a plausible hypothesis from verified evidence. This generation has not performed live market research: do not invent studies, citations, current launch dates, adoption figures or customer traction. Never guarantee novelty, patentability, exclusivity, success or legal ownership.
- Check physical, operational and regulatory feasibility. For electrical, structural, food-contact, medical or other safety-critical work, name qualified review and start with a nonfunctional mockup or customer test; never prescribe hazardous DIY testing. Put qualified review BEFORE functional construction or testing in the roadmap, not only at the end. Never claim legal compliance or safety certification without verified evidence. Renders illustrate the actual concept, not impossible technology.
- Approval of one component does not certify the assembled product. Do not call a proposed product UL-listed, CE-marked, compliant or certified. Say that a qualified professional or accredited lab must determine the applicable requirements. Gate functional work in the lean tier as well as the roadmap on prior qualified review.
- Refundable deposits and reservations show preliminary interest, not earned revenue or proven paid demand. In first_hundred use fulfilled purchases or completed services times an estimated test price; explicitly label prices, benefits and pass/fail thresholds as hypotheses or proposals.
- Be concise and useful. Embed these judgments in the existing fields; do not add scores, extra keys, a reasoning transcript or marketing filler.

VARIETY IS MANDATORY. Broad or vague sentences (an industry name, a category, "an app for X", "a money making app") have a hundred possible ventures inside them. Never take the first obvious interpretation. Pick one specific wedge: a specific customer, a specific painful moment in their day, a specific thing they already pay for badly. Where everyone expects software, consider a physical product, a service, a kit, a subscription box, a marketplace, a tool for pros, a franchise, a media brand. For broad ideas, vary customer, form and name across generations. For explicit product ideas, preserve the requested product and vary only unconstrained details. You will be given an ANGLE to build from and a list of names and concepts to AVOID. Use the angle where compatible with the creator's explicit intent, and avoid duplicating prior names or lightly rewording their concepts."""

ANGLES = [
    "a physical product you can hold, not software",
    "a tool or kit for professionals who do this for a living",
    "a service business a two-person crew could run in one town",
    "a subscription that shows up every month",
    "a marketplace connecting two groups who cannot find each other",
    "something for the beginner who is doing this for the very first time",
    "something for the person doing this at scale, who has hired staff",
    "a data or recordkeeping product for a boring but mandatory task",
    "a consumer brand with a strong look, sold direct",
    "a piece of hardware that plugs into what people already own",
    "an unglamorous back-of-house fix for a mess everyone tolerates",
    "a training, certification, or education product for a skilled trade",
    "a rental or shared-equipment model instead of ownership",
    "a safety or compliance product that removes a fine or a risk",
    "a product for the buyer nobody builds for: rural, small-town, older, or offline",
]


def build_user_message(idea: str, angle: str | None, avoid: list[dict] | None) -> str:
    parts = [f"The sentence:\n\n{idea}"]
    if angle:
        parts.append(
            f"Optional ANGLE for exploring broad ideas: {angle}.\n"
            "Ignore this angle wherever it conflicts with the sentence's explicit product, "
            "buyer or constraints, or would add an unnecessary business model. "
            "The creator's intent takes priority."
        )
    if avoid:
        lines = "\n".join(f"- {a.get('name')}: {a.get('tagline') or ''}".rstrip(": ") for a in avoid if a.get("name"))
        parts.append("AVOID. These ventures already exist for this creator. Do not reuse or lightly reword their names, taglines, or core concepts:\n" + lines)
    parts.append("Return the JSON object now. Start with { and end with }. No prose, no code fences.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Text streaming
# ---------------------------------------------------------------------------

log = logging.getLogger("uvicorn.error.manifest.llm")


def reasoning_options(model: str) -> dict:
    """Keep the quality default; alternatives are explicit, never provider fallbacks."""
    if not model.startswith("gpt-5"):
        return {}
    effort = os.environ.get("LLM_REASONING_EFFORT", "medium").strip().lower()
    if effort not in {"low", "medium", "high"}:
        raise ValueError("LLM_REASONING_EFFORT must be low, medium or high.")
    return {"reasoning": {"effort": effort}}


def measured_stream(operation: str):
    """Log timings only: never idea text, output, creator identifiers or credentials."""
    def decorate(fn):
        @wraps(fn)
        async def run(*args, **kwargs):
            started = time.perf_counter()
            first = None
            chars = 0
            outcome = "cancelled"
            provider = text_provider()
            model = os.environ.get("LLM_MODEL") or {
                "openai": "gpt-5-mini", "anthropic": "claude-sonnet-4-5",
            }.get(provider, "mock")
            try:
                async with aclosing(fn(*args, **kwargs)) as stream:
                    async for chunk in stream:
                        if chunk and first is None:
                            first = time.perf_counter() - started
                        chars += len(chunk)
                        yield chunk
                outcome = "completed"
            except Exception:
                outcome = "failed"
                raise
            finally:
                log.info("llm_timing %s", json.dumps({
                    "operation": operation, "provider": provider, "model": model,
                    "reasoning_effort": os.environ.get("LLM_REASONING_EFFORT", "medium")
                    if provider == "openai" and model.startswith("gpt-5") else None,
                    "first_text_seconds": round(first, 3) if first is not None else None,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "output_chars": chars, "outcome": outcome,
                }))
        return run
    return decorate

def text_provider() -> str:
    p = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if p:
        if p not in {"anthropic", "openai", "mock"}:
            raise ValueError("LLM_PROVIDER must be anthropic, openai or mock; no provider fallback was attempted.")
        return p
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_BASE_URL"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "mock"


PRESERVE_SYSTEM_PROMPT = SYSTEM_PROMPT.split("\n\nVARIETY IS MANDATORY.", 1)[0] + """

SELECTED HYBRID: The user has deliberately chosen this specific product, buyer,
and combination of mechanisms. Develop THAT concept faithfully, not a different
random wedge. Keep the central product, intended buyer and borrowed mechanisms
recognizable. Explain technical constraints honestly rather than promising an
unsafe or impossible build. Any proposed correction must retain the core intent.
"""


@measured_stream("venture")
async def stream_venture_text(idea: str, angle: str | None = None, avoid: list[dict] | None = None,
                              preserve: bool = False) -> AsyncIterator[str]:
    provider = text_provider()
    msg = build_user_message(idea, angle, None if preserve else avoid)
    system = PRESERVE_SYSTEM_PROMPT if preserve else SYSTEM_PROMPT
    if provider == "anthropic":
        async with aclosing(_stream_anthropic(msg, system=system)) as stream:
            async for chunk in stream:
                yield chunk
    elif provider == "openai":
        async with aclosing(_stream_openai(msg, system=system)) as stream:
            async for chunk in stream:
                yield chunk
    else:
        async for chunk in _stream_mock(idea):
            yield chunk


REFINEMENT_SYSTEM_PROMPT = SYSTEM_PROMPT.split("\n\nVARIETY IS MANDATORY.", 1)[0] + """

REFINEMENT REQUEST: The user already has a generated venture. Improve and adapt THAT SAME venture using the original idea, its existing generated content, and the user's new context. Do not replace it with an unrelated randomized concept. Keep its central product, customer, and intent recognizable unless the user's context specifically calls for a correction. Return the complete venture JSON in the exact schema above, including all tiers, roadmap, grounding, and image_prompts.
"""


def _refinement_user_message(venture: dict, context: str) -> str:
    existing = {
        key: venture.get(key)
        for key in (
            "name", "tagline", "description", "category", "accent", "tiers",
            "roadmap", "grounding", "image_prompts",
        )
    }
    grounding = dict(existing.get("grounding") or {})
    grounding.pop("_refinement", None)
    existing["grounding"] = grounding
    return (
        "Original idea:\n" + str(venture.get("idea_raw") or "") + "\n\n"
        "Existing generated venture (refine this, do not start over):\n"
        + json.dumps(existing, ensure_ascii=False, separators=(",", ":"))
        + "\n\nNew context from the creator:\n"
        + context
        + "\n\nReturn the complete refined venture JSON now. Start with { and end with }. No prose, no code fences."
    )


@measured_stream("refinement")
async def stream_refinement_text(venture: dict, context: str) -> AsyncIterator[str]:
    provider = text_provider()
    msg = _refinement_user_message(venture, context)
    if provider == "anthropic":
        async with aclosing(_stream_anthropic(msg, REFINEMENT_SYSTEM_PROMPT, max_tokens=2600)) as stream:
            async for text in stream:
                yield text
    elif provider == "openai":
        async with aclosing(_stream_openai(msg, REFINEMENT_SYSTEM_PROMPT)) as stream:
            async for text in stream:
                yield text
    else:
        # The mock provider keeps the same venture identity/content shape while
        # allowing API and persistence behavior to be exercised without a key.
        text = json.dumps(
            {key: venture.get(key) for key in (
                "name", "tagline", "description", "category", "accent", "tiers",
                "roadmap", "grounding", "image_prompts",
            )},
            ensure_ascii=False,
        )
        for i in range(0, len(text), 24):
            yield text[i:i + 24]


async def _stream_anthropic(msg: str, system: str = SYSTEM_PROMPT, max_tokens: int = 2200) -> AsyncIterator[str]:
    import anthropic

    client = anthropic.AsyncAnthropic()
    model = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")
    try:
        async with client.messages.stream(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": msg}],
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            if final.stop_reason != "end_turn":
                raise RuntimeError("The text provider could not finish the response. Please try again.")
    finally:
        await client.close()


async def _stream_openai(msg: str, system: str = SYSTEM_PROMPT) -> AsyncIterator[str]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    model = os.environ.get("LLM_MODEL", "gpt-5-mini")
    stream = None
    completed = False
    try:
        stream = await client.responses.create(
            model=model, instructions=system, input=msg, stream=True,
            **reasoning_options(model),
        )
        async for event in stream:
            event_type = getattr(event, "type", "")
            if event_type in ("response.failed", "response.incomplete", "error"):
                raise RuntimeError(f"The text provider could not finish the response ({event_type}). Please try again.")
            if event_type == "response.completed":
                completed = True
            if event_type == "response.output_text.delta":
                yield event.delta
        if not completed:
            raise RuntimeError("The text provider connection ended before completion. Please try again.")
    finally:
        if stream is not None and hasattr(stream, "close"):
            await stream.close()
        if hasattr(client, "close"):
            await client.close()


MOCK_VENTURE = {
    "name": "Ridgeline",
    "tagline": "The ladder hook that carries the bucket for you.",
    "description": "Ridgeline is a forged steel hook that clamps onto any extension ladder rung and holds a five-gallon bucket, a drill, and a caulk gun at working height. It is for painters, gutter crews, and homeowners who are tired of climbing down for the one tool they left on the ground. It replaces the bent coat hanger every crew already uses.",
    "category": "Jobsite tools",
    "accent": "#e0a458",
    "tiers": [
        {"label": "The Lean Build", "title": "Paper fit study", "description": "Interview painters with a nonfunctional cardboard hook; estimate $20 for mockups. A qualified structural engineer must review load and ladder compatibility before functional construction."},
        {"label": "The Real Product", "title": "Cast, coated, carded", "description": "An investment-cast hook with a rubber rung pad and a locking gate, blister packed for retail. Tooling and a first run of two thousand comes in around eighteen thousand dollars."},
        {"label": "The Empire", "title": "The rung system", "description": "A family of clip-on rung accessories: tray, cordless charger dock, paint cup, light. Licensed to a ladder manufacturer so it ships in the box."},
    ],
    "roadmap": [
        {"step": 1, "action": "Interview painters using a cardboard mockup.", "where": "Proposed gate: three of five painters identify a useful improvement over their current hook. Revise the fit if fewer do."},
        {"step": 2, "action": "Commission qualified structural review.", "where": "A qualified structural engineer must assess ladder compatibility and define safe loads and testing before functional construction."},
        {"step": 3, "action": "Commission professional prototypes under the reviewed test plan.", "where": "A fabrication shop follows the engineer's design and supervised testing requirements; no unreviewed ladder trials."},
        {"step": 4, "action": "Get a quote for a cast version.", "where": "Three investment casting shops through Xometry or Thomasnet; ask for a 2,000 unit run with rubber overmold."},
        {"step": 5, "action": "Pitch a distributor once you clear a thousand units.", "where": "Regional paint-supply distributors and the buyer at a hardware co-op like Do it Best or Ace."},
    ],
    "grounding": {
        "exists": "Ordinary bucket hooks already exist. The locking tool carrier is a hypothesis for fewer retrieval trips, not verified novelty; extra weight may defeat the benefit.",
        "who_pays": "Residential painters and gutter crews; propose testing thirty-four dollars each rather than claiming proven demand.",
        "first_hundred": "After qualified review and supervised testing, sell and deliver three hooks at an estimated $34 each through a paint counter: $102 revenue, not profit. Refundable reservations are not earned revenue.",
    },
    "image_prompts": [
        "Photorealistic product render of a forged steel ladder hook with a warm amber powder coat, clamped to an aluminum ladder rung, alone on a near-black studio backdrop, soft rim lighting, shallow depth of field, no text.",
        "A painter on an extension ladder against a white farmhouse, an amber steel hook on the rung holding a five-gallon bucket and a cordless drill, late afternoon light, photorealistic, no text.",
        "Extreme close-up of the amber powder-coated steel hook's locking gate and black rubber rung pad, brushed metal texture, dramatic side lighting, dark background, no text.",
    ],
}


async def _stream_mock(idea: str) -> AsyncIterator[str]:
    text = json.dumps(MOCK_VENTURE, ensure_ascii=False)
    step = 6
    for i in range(0, len(text), step):
        yield text[i:i + step]
        await asyncio.sleep(0.012)


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

def image_provider() -> str:
    p = os.environ.get("IMAGE_PROVIDER", "").strip().lower()
    if p:
        return p
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "none"


async def generate_render(prompt: str, accent: str = "#e8eeff") -> bytes | None:
    provider = image_provider()
    if provider == "none":
        return None
    if provider == "openai":
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        model = os.environ.get("IMAGE_MODEL", "gpt-image-1")
        quality = os.environ.get("IMAGE_QUALITY", "medium")   # low ($0.011) | medium ($0.042) | high
        result = await client.images.generate(model=model, prompt=prompt, size="1024x1024", quality=quality, n=1)
        import base64

        return base64.b64decode(result.data[0].b64_json)
    if provider == "pplx":
        from .generate_image import generate_image  # sandbox-only helper

        return await generate_image(prompt, aspect_ratio="1:1", model=os.environ.get("IMAGE_MODEL", "nano_banana_2"))
    if provider == "mock":
        await asyncio.sleep(1.2)
        return _mock_png(accent, seed=hash(prompt) & 0xFFFF)
    raise RuntimeError(f"Unknown IMAGE_PROVIDER {provider}")


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = (h or "#e8eeff").lstrip("#")
    if len(h) != 6:
        h = "e8eeff"
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mock_png(accent: str, seed: int = 0, size: int = 512) -> bytes:
    """A soft radial glow in the accent color. Pure Python, no PIL."""
    r, g, b = _hex_to_rgb(accent)
    cx, cy = size * (0.35 + (seed % 7) / 20), size * (0.4 + (seed % 5) / 20)
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / size
            k = max(0.0, 1 - d * 1.6) ** 2
            raw += bytes((int(8 + (r - 8) * k), int(8 + (g - 8) * k), int(8 + (b - 8) * k)))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )

# ---------------------------------------------------------------------------
# The Path: a chosen tier turned into a step-by-step build guide
# ---------------------------------------------------------------------------

PATH_SYSTEM_PROMPT = """You are the engine inside MANIFEST. A real person has picked ONE way to build their venture and now needs to be walked through it, step by step, like a seasoned operator sitting next to them.

You return ONE JSON object and nothing else. No prose, no code fences. Plain, specific, concrete. Forbidden words: revolutionary, seamless, cutting-edge, game-changing, empower, leverage, unlock, next-generation, innovative, disrupt, ecosystem, synergy, elevate.

Rules for honesty: name real, well-known companies, marketplaces, and tools only when you are confident they exist and do this kind of work. When unsure, describe the TYPE of supplier and give a search phrase instead of inventing a name. Never invent prices for specific vendors; give rough ranges and say they are rough.

BUILD STANDARD: Respect the selected tier's scope and resources. Test the hardest
unproven assumption before expensive tooling, inventory or scale. Include a
small, measurable proposed pass/fail criterion in the relevant "done_when" and
explain what to revise if it fails in "how". Customer compliments are not paid
demand. Separate revenue from profit and include ongoing costs where relevant.
Do not treat earlier generated assumptions as verified facts. For safety-critical
products require qualified review before functional tests; the first move can
be a nonfunctional mockup or buyer interview, never hazardous DIY work.
Component approvals do not certify the final product. Never assert unsupported
certification or compliance. Refundable deposits are not earned revenue.

Produce the keys IN THIS EXACT ORDER:

"first_move": one sentence. The single thing to do tonight, in under an hour, with what they already have.
"steps": array of 5 to 7 objects, in the order they should be done. Each object has, in this order:
  "title": 3-7 words, imperative.
  "why": one sentence on why this step comes now.
  "how": array of 3-4 strings. Each is one concrete action a beginner can do, with the exact tool, site, or person type named.
  "suppliers": array of 0-3 objects {"kind": type of supplier or resource (e.g. "Investment casting shop", "Powder coater", "Shopify theme"), "examples": array of 0-3 real, well-known names you are confident about (may be empty), "search": a short phrase to type into Google or a marketplace to find local or online options}.
  "cost": rough dollar range as a short string, or "Free".
  "time": rough duration as a short string.
  "done_when": one sentence describing the concrete evidence this step is finished.
"watch_out": one sentence on the most common way people blow this specific plan.
"""


def _path_user_message(venture: dict, tier_index: int) -> str:
    tier = (venture.get("tiers") or [{}] * 3)[tier_index]
    roadmap = "\n".join(f"{r.get('step')}. {r.get('action')} ({r.get('where')})" for r in venture.get("roadmap") or [])
    g = venture.get("grounding") or {}
    return (
        f"Venture: {venture.get('name')} — {venture.get('tagline')}\n"
        f"What it is: {venture.get('description')}\n"
        f"Category: {venture.get('category')}\n\n"
        f"The person chose this path: {tier.get('label')} — {tier.get('title')}\n{tier.get('description')}\n\n"
        f"Original five-step sketch:\n{roadmap}\n\n"
        f"What already exists: {g.get('exists')}\nWho pays: {g.get('who_pays')}\nFirst hundred dollars: {g.get('first_hundred')}\n\n"
        "Return the JSON object now. Start with { and end with }. No prose, no code fences."
    )


@measured_stream("path")
async def stream_path_text(venture: dict, tier_index: int) -> AsyncIterator[str]:
    provider = text_provider()
    msg = _path_user_message(venture, tier_index)
    if provider == "anthropic":
        async with aclosing(_stream_anthropic(msg, PATH_SYSTEM_PROMPT, max_tokens=3500)) as stream:
            async for text in stream:
                yield text
    elif provider == "openai":
        async with aclosing(_stream_openai(msg, PATH_SYSTEM_PROMPT)) as stream:
            async for text in stream:
                yield text
    else:
        text = json.dumps(MOCK_PATH)
        for i in range(0, len(text), 24):
            await asyncio.sleep(0.02)
            yield text[i:i + 24]


MOCK_PATH = {
    "first_move": "Sketch a nonfunctional paper hook and ask a painter about their current bucket carrier.",
    "steps": [
        {"title": "Interview five painters", "why": "Test whether the fit solves a real inconvenience before spending on tooling.",
         "how": ["Sketch a paper mockup.", "Ask five painters about their current hook.", "Revise the fit if fewer than three identify a useful improvement; this is a proposed threshold."],
         "suppliers": [], "cost": "Estimated $0-20", "time": "One morning", "done_when": "Three of five painters identify a useful improvement in the paper mockup."},
        {"title": "Commission qualified structural review", "why": "A ladder accessory can create a fall or load hazard.",
         "how": ["Contact a qualified structural engineer.", "Request review of compatibility and load assumptions.", "Have the engineer define professional fabrication and supervised tests before any functional construction."],
         "suppliers": [{"kind": "Structural engineer", "examples": [], "search": "qualified product structural engineer"}],
         "cost": "Rough estimate $500-2000", "time": "One to three weeks", "done_when": "The engineer has documented the design review and supervised test requirements."},
    ],
    "watch_out": "Ordering tooling before a stranger has paid you money.",
}
