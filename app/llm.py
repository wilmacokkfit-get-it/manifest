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
"image_prompts": array of exactly 3 strings, each a detailed prompt for a photorealistic product render. No text, logos, or words in the image. [1] the product alone on a dark studio backdrop with soft rim lighting; [2] the product in realistic use by a person in its real environment; [3] a close detail, material, or packaging shot. Describe form, material, color (use the accent color), scale, and lighting.

If the idea is weak or already exists, still build the best honest version of it and say the truth in grounding. Never flatter."""


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


async def stream_venture_text(idea: str) -> AsyncIterator[str]:
    provider = text_provider()
    if provider == "anthropic":
        async for chunk in _stream_anthropic(idea):
            yield chunk
    elif provider == "openai":
        async for chunk in _stream_openai(idea):
            yield chunk
    else:
        async for chunk in _stream_mock(idea):
            yield chunk


async def _stream_anthropic(idea: str) -> AsyncIterator[str]:
    import anthropic

    client = anthropic.AsyncAnthropic()
    model = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")
    async with client.messages.stream(
        model=model,
        max_tokens=2200,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"The sentence:\n\n{idea}\n\nReturn the JSON object now."},
            {"role": "assistant", "content": "{"},
        ],
    ) as stream:
        yield "{"
        async for text in stream.text_stream:
            yield text


async def _stream_openai(idea: str) -> AsyncIterator[str]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    model = os.environ.get("LLM_MODEL", "gpt-5-mini")
    stream = await client.responses.create(
        model=model,
        instructions=SYSTEM_PROMPT,
        input=f"The sentence:\n\n{idea}\n\nReturn the JSON object now.",
        stream=True,
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
        result = await client.images.generate(model=model, prompt=prompt, size="1024x1024", quality="medium", n=1)
        import base64

        return base64.b64decode(result.data[0].b64_json)
    if provider == "pplx":
        from .generate_image import generate_image  # sandbox-only helper

        return await generate_image(prompt, aspect_ratio="1:1", model=os.environ.get("IMAGE_MODEL", "gpt_image_2"))
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
