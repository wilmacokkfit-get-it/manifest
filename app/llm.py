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
import os
import struct
import zlib
from typing import AsyncIterator

SYSTEM_PROMPT = """You are the engine inside MANIFEST, a product that turns one sentence into a fully formed venture.

You receive a single sentence from a real person describing a frustration, a want, or a half-formed product idea. You return ONE JSON object and nothing else. No prose, no code fences.

Write like a sharp founder talking to a friend, not like a pitch deck. Plain, specific, concrete. Forbidden words: revolutionary, seamless, cutting-edge, game-changing, empower, leverage, unlock, next-generation, innovative, disrupt, ecosystem, synergy, elevate.

Produce the keys IN THIS EXACT ORDER:

"name": 1-2 words. Brandable, easy to say, not a pun, not a real famous brand. No suffixes like -ify, -ly, AI, Hub, Labs, Pro.
"tagline": 4-12 words. One clean sentence fragment that makes the product obvious. No cliches.
"description": 2-3 sentences. What it is, who it is for, and why it beats the current workaround. Concrete nouns.
"category": 2-3 words, e.g. "Jobsite tools", "Home irrigation", "Consumer kitchen".
"accent": one hex color (like "#d9a441") that suits this product. Saturated enough to glow on a near-black background, never white, never gray.
"tiers": array of exactly 3 objects, in order, with "label" values exactly "The Lean Build", "The Real Product", "The Empire". Each has "title" (2-5 words) and "description" (2 sentences: what gets built at this level, and a rough cost or effort to make it real).
"roadmap": array of exactly 5 objects {"step": 1..5, "action": imperative sentence of 12 words or fewer, "where": 1-2 sentences naming the specific tools, suppliers, websites, marketplaces, or types of people to use}.
"grounding": object with three honest, specific sentences:
   "exists": the closest thing that already exists, named. If something very similar exists, say so plainly.
   "who_pays": the exact buyer and a realistic price point.
   "first_hundred": how the first one hundred dollars of revenue would actually happen, step by step in one or two sentences.
"image_prompts": array of exactly 2 strings, each a detailed prompt for a photorealistic product render. No text, logos, or words in the image. [1] the product alone on a dark studio backdrop with soft rim lighting; [2] the product in realistic use by a person in its real environment. Describe form, material, color (use the accent color), scale, and lighting.

If the idea is weak or already exists, still build the best honest version of it and say the truth in grounding. Never flatter.

VARIETY IS MANDATORY. Broad or vague sentences (an industry name, a category, "an app for X", "a money making app") have a hundred possible ventures inside them. Never take the first obvious interpretation. Pick one specific wedge: a specific customer, a specific painful moment in their day, a specific thing they already pay for badly. Where everyone expects software, consider a physical product, a service, a kit, a subscription box, a marketplace, a tool for pros, a franchise, a media brand. If the same sentence were sent twice, the two ventures must be clearly different in customer, form, and name. You will be given an ANGLE to build from and a list of names and concepts to AVOID. Obey both: the angle shapes the venture, and nothing in the avoid list may be reused or lightly reworded."""

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
        parts.append(f"ANGLE to build from: {angle}.")
    if avoid:
        lines = "\n".join(f"- {a.get('name')}: {a.get('tagline') or ''}".rstrip(": ") for a in avoid if a.get("name"))
        parts.append("AVOID. These ventures already exist for this creator. Do not reuse or lightly reword their names, taglines, or core concepts:\n" + lines)
    parts.append("Return the JSON object now. Start with { and end with }. No prose, no code fences.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Text streaming
# ---------------------------------------------------------------------------

def text_provider() -> str:
    p = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if p:
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


async def stream_venture_text(idea: str, angle: str | None = None, avoid: list[dict] | None = None,
                              preserve: bool = False) -> AsyncIterator[str]:
    provider = text_provider()
    msg = build_user_message(idea, angle, None if preserve else avoid)
    system = PRESERVE_SYSTEM_PROMPT if preserve else SYSTEM_PROMPT
    if provider == "anthropic":
        async for chunk in _stream_anthropic(msg, system=system):
            yield chunk
    elif provider == "openai":
        async for chunk in _stream_openai(msg, system=system):
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
        + json.dumps(existing, ensure_ascii=False)
        + "\n\nNew context from the creator:\n"
        + context
        + "\n\nReturn the complete refined venture JSON now. Start with { and end with }. No prose, no code fences."
    )


async def stream_refinement_text(venture: dict, context: str) -> AsyncIterator[str]:
    provider = text_provider()
    msg = _refinement_user_message(venture, context)
    if provider == "anthropic":
        import anthropic

        client = anthropic.AsyncAnthropic()
        model = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")
        async with client.messages.stream(
            model=model, max_tokens=2600, system=REFINEMENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": msg}],
        ) as stream:
            async for text in stream.text_stream:
                yield text
    elif provider == "openai":
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        model = os.environ.get("LLM_MODEL", "gpt-5-mini")
        kwargs = {"reasoning": {"effort": "low"}} if model.startswith("gpt-5") else {}
        stream = await client.responses.create(
            model=model, instructions=REFINEMENT_SYSTEM_PROMPT, input=msg, stream=True, **kwargs,
        )
        async for event in stream:
            event_type = getattr(event, "type", "")
            if event_type in ("response.failed", "response.incomplete"):
                response = getattr(event, "response", None)
                detail = getattr(response, "error", None) or getattr(response, "incomplete_details", None)
                raise RuntimeError(f"OpenAI refinement {event_type}: {detail or 'provider did not complete the response.'}")
            if event_type == "response.output_text.delta":
                yield event.delta
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


async def _stream_anthropic(msg: str, system: str = SYSTEM_PROMPT) -> AsyncIterator[str]:
    import anthropic

    client = anthropic.AsyncAnthropic()
    model = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")
    async with client.messages.stream(
        model=model,
        max_tokens=2200,
        system=system,
        messages=[
            {"role": "user", "content": msg},
        ],
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def _stream_openai(msg: str, system: str = SYSTEM_PROMPT) -> AsyncIterator[str]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    model = os.environ.get("LLM_MODEL", "gpt-5-mini")
    kwargs = {"reasoning": {"effort": "low"}} if model.startswith("gpt-5") else {}
    stream = await client.responses.create(
        model=model,
        instructions=system,
        input=msg,
        stream=True,
        **kwargs,
    )
    async for event in stream:
        if getattr(event, "type", "") == "response.output_text.delta":
            yield event.delta


MOCK_VENTURE = {
    "name": "Ridgeline",
    "tagline": "The ladder hook that carries the bucket for you.",
    "description": "Ridgeline is a forged steel hook that clamps onto any extension ladder rung and holds a five-gallon bucket, a drill, and a caulk gun at working height. It is for painters, gutter crews, and homeowners who are tired of climbing down for the one tool they left on the ground. It replaces the bent coat hanger every crew already uses.",
    "category": "Jobsite tools",
    "accent": "#e0a458",
    "tiers": [
        {"label": "The Lean Build", "title": "Bent and welded", "description": "One hook, hand-bent from 3/8 inch rod and powder coated in a friend's shop. Twenty units for about four hundred dollars in material, sold at the paint counter."},
        {"label": "The Real Product", "title": "Cast, coated, carded", "description": "An investment-cast hook with a rubber rung pad and a locking gate, blister packed for retail. Tooling and a first run of two thousand comes in around eighteen thousand dollars."},
        {"label": "The Empire", "title": "The rung system", "description": "A family of clip-on rung accessories: tray, cordless charger dock, paint cup, light. Licensed to a ladder manufacturer so it ships in the box."},
    ],
    "roadmap": [
        {"step": 1, "action": "Weld ten prototypes and give them to painters.", "where": "Any local fab shop or a friend with a MIG welder; hand them out at a Sherwin-Williams counter on a Monday morning."},
        {"step": 2, "action": "Film the bucket-drop test and post it.", "where": "A thirty second clip on TikTok and Facebook contractor groups showing the hook holding sixty pounds while the ladder shakes."},
        {"step": 3, "action": "Take two hundred preorders at thirty-four dollars.", "where": "A Shopify store with a single product page and the video above the fold."},
        {"step": 4, "action": "Get a quote for a cast version.", "where": "Three investment casting shops through Xometry or Thomasnet; ask for a 2,000 unit run with rubber overmold."},
        {"step": 5, "action": "Pitch a distributor once you clear a thousand units.", "where": "Regional paint-supply distributors and the buyer at a hardware co-op like Do it Best or Ace."},
    ],
    "grounding": {
        "exists": "Werner sells a plastic ladder bucket hook and there are dozens of cheap S-hooks on Amazon, but none lock to the rung or carry tools and a bucket together.",
        "who_pays": "Residential painters and gutter crews, about thirty-four dollars each, with foremen buying four to six per crew.",
        "first_hundred": "Three hooks sold in person at a paint counter to the first crew that watches the drop test, paid on the spot with a card reader.",
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


async def stream_path_text(venture: dict, tier_index: int) -> AsyncIterator[str]:
    provider = text_provider()
    msg = _path_user_message(venture, tier_index)
    if provider == "anthropic":
        import anthropic
        client = anthropic.AsyncAnthropic()
        model = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")
        async with client.messages.stream(model=model, max_tokens=3500, system=PATH_SYSTEM_PROMPT,
                                          messages=[{"role": "user", "content": msg}]) as stream:
            async for text in stream.text_stream:
                yield text
    elif provider == "openai":
        from openai import AsyncOpenAI
        client = AsyncOpenAI()
        model = os.environ.get("LLM_MODEL", "gpt-5-mini")
        kwargs = {"reasoning": {"effort": "low"}} if model.startswith("gpt-5") else {}
        stream = await client.responses.create(model=model, instructions=PATH_SYSTEM_PROMPT, input=msg, stream=True, **kwargs)
        async for event in stream:
            if getattr(event, "type", "") == "response.output_text.delta":
                yield event.delta
    else:
        text = json.dumps(MOCK_PATH)
        for i in range(0, len(text), 24):
            await asyncio.sleep(0.02)
            yield text[i:i + 24]


MOCK_PATH = {
    "first_move": "Bend one hook tonight from a piece of 3/8 inch rod and hang a full bucket on it.",
    "steps": [
        {"title": "Make ten by hand", "why": "You need something real in painters' hands before you spend a dollar on tooling.",
         "how": ["Buy 3/8 inch mild steel rod at a farm store or Menards.", "Bend around a 2 inch pipe in a vise; weld the gate tab.", "Rattle-can or ask a local powder coater for a batch price."],
         "suppliers": [{"kind": "Local weld or fab shop", "examples": [], "search": "welding shop near me"}],
         "cost": "$60-150", "time": "One weekend", "done_when": "Ten hooks, each holding 60 lb for a full minute."},
        {"title": "Put them on ladders", "why": "Feedback from the first ten users decides the shape of everything after.",
         "how": ["Stand at a paint counter Monday 6:30am and hand hooks to crews.", "Ask one question: what would make you pay $30 for this?", "Write every answer in your phone."],
         "suppliers": [], "cost": "Free", "time": "One morning", "done_when": "Ten crews have a hook and you have ten answers."},
    ],
    "watch_out": "Ordering tooling before a stranger has paid you money.",
}
