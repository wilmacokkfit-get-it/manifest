"""The Architect: reads the whole Floor at once and surfaces what no single creator can see
from their own page.

Honesty contract
----------------
1. Facts are computed here, in Python, from real rows: public ventures, their paths, open asks,
   hands raised, signals. Nothing is invented.
2. The engine (LLM) only writes prose OVER those facts. Every reading must point at real
   ventures/asks by slug or id; anything it names that does not exist is dropped.
3. If the Floor is too thin to see anything, the Architect says so instead of pretending.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections import defaultdict
from typing import Any
from app.creation_standard import COMPARATIVE_STRESS_TEST

STOP = set("""a an the and or of for to in on at by with from into over under about your you my our their its it this
that these those is are be was were been being as if then than so but not no yes do does did done can could will would
should may might must one two three four five six seven eight nine ten first second third per each every any all some
more most less least very just only also even still already yet new old real full small large big little local near me
own use used using make makes making made get gets got need needs needed want wants like kind type types people person
product products tool tools system systems kit kits app apps line lines unit units set sets way ways thing things
built build builds building serious while who what when where why how time times current actually already without within
work works working day days week weeks month months year years hand hands one-person single better best good great""".split())

KIND_FAMILIES = [
    ("machine shop / CNC", r"machin|cnc|mill|lathe|turn"),
    ("injection molding", r"inject|mold|mould|tooling"),
    ("3D printing", r"3d|print|additive|sla|fdm"),
    ("sheet metal / fab / welding", r"weld|fab|sheet metal|laser cut|waterjet|bend"),
    ("sewing / textiles", r"sew|textile|fabric|cut and sew|upholster|canvas|nylon|leather"),
    ("electronics / PCB", r"pcb|electronic|circuit|firmware|microcontroller|sensor|battery|charger"),
    ("plastics / rubber / silicone", r"plastic|silicone|rubber|gasket|extru"),
    ("packaging / print", r"packag|label|box|print shop|sticker"),
    ("software / app dev", r"software|app dev|developer|no-code|bubble|flutter|react|website"),
    ("photo / video / marketing", r"photo|video|marketing|ads|content|influenc|tiktok"),
    ("legal / compliance / testing", r"legal|attorney|trademark|patent|compliance|certif|ul |etl|lab test|testing"),
    ("growers / horticulture", r"grow|greenhouse|nursery|horticult|hydro|irrigat"),
    ("contractors / trades", r"contractor|electrician|plumber|carpenter|roofer|painter|jobsite|trade"),
    ("retail / distribution", r"retail|distribut|wholesale|dealer|store|amazon|etsy|shopify"),
    ("money / funding", r"fund|invest|loan|grant|kickstarter|pre-order|preorder|crowdfund"),
]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z\-]{2,}", (text or "").lower()) if w not in STOP}


def _family(text: str) -> str | None:
    t = (text or "").lower()
    for name, pat in KIND_FAMILIES:
        if re.search(pat, t):
            return name
    return None


def compute_facts(ventures: list[dict], needs: list[dict]) -> dict[str, Any]:
    """Pure function. ventures = public, ready ventures (full rows incl. path). needs = open asks joined w/ venture."""
    vs = []
    for v in ventures:
        path = v.get("path") or {}
        steps = path.get("steps") or []
        sup_fams = set()
        sup_kinds = []
        for st in steps:
            for sp in st.get("suppliers") or []:
                sup_kinds.append(sp.get("kind") or "")
                f = _family(sp.get("kind", "") + " " + sp.get("search", ""))
                if f:
                    sup_fams.add(f)
        where_fams = set()
        for r in v.get("roadmap") or []:
            f = _family(r.get("where", ""))
            if f:
                where_fams.add(f)
        sig = {"buy": int(v.get("buy_count") or 0), "build": int(v.get("build_count") or 0), "fund": int(v.get("fund_count") or 0)}
        vs.append({
            "slug": v["slug"], "name": v.get("name"), "tagline": v.get("tagline"), "category": v.get("category"),
            "category_family": _family(v.get("category", "") + " " + (v.get("tagline") or "")),
            "words": _words(" ".join([v.get("name") or "", v.get("tagline") or "", v.get("description") or "", v.get("category") or ""])),
            "path_tier": path.get("tier_label"), "path_done": sum(1 for s in steps if s.get("status") == "done"), "path_total": len(steps),
            "current_step": next((s.get("title") for s in steps if s.get("status") != "done"), None),
            "supplier_families": sorted(sup_fams), "supplier_kinds": [k for k in sup_kinds if k][:12], "roadmap_families": sorted(where_fams),
            "signals": sig, "signals_total": sum(sig.values()), "released_at": v.get("released_at"),
        })

    by_slug = {x["slug"]: x for x in vs}

    # 1. Convergence: several ventures need the same family of supplier -> pooled buying power.
    fam_to = defaultdict(list)
    for x in vs:
        for f in set(x["supplier_families"]) | set(x["roadmap_families"]):
            fam_to[f].append(x["slug"])
    convergence = [{"family": f, "slugs": sorted(set(s))} for f, s in fam_to.items() if len(set(s)) >= 2]
    convergence.sort(key=lambda c: -len(c["slugs"]))

    # 2. Open asks that rhyme with each other (same family or shared words) -> one shop could fill both.
    ask_rows = []
    for n in needs:
        ask_rows.append({"id": n["id"], "slug": n["venture"]["slug"], "venture": n["venture"]["name"], "title": n["title"],
                         "detail": n.get("detail") or "", "step_n": n.get("step_n"), "hands": int(n.get("offer_count") or 0),
                         "family": _family(n["title"] + " " + (n.get("detail") or "")),
                         "words": _words(n["title"] + " " + (n.get("detail") or ""))})
    ask_pairs = []
    for i in range(len(ask_rows)):
        for j in range(i + 1, len(ask_rows)):
            a, b = ask_rows[i], ask_rows[j]
            if a["slug"] == b["slug"]:
                continue
            shared = a["words"] & b["words"]
            if (a["family"] and a["family"] == b["family"]) or len(shared) >= 2:
                ask_pairs.append({"ids": [a["id"], b["id"]], "slugs": [a["slug"], b["slug"]], "family": a["family"] or b["family"], "shared": sorted(shared)[:6]})

    # 3. Adjacency: venture A's supplier family is venture B's category family -> B's creator is A's supplier,
    #    or two ventures share enough vocabulary that they serve the same buyer.
    adjacency = []
    for a in vs:
        for b in vs:
            if a["slug"] >= b["slug"]:
                continue
            shared = a["words"] & b["words"]
            reason = []
            if b["category_family"] and b["category_family"] in a["supplier_families"]:
                reason.append(f"{b['name']} is in the '{b['category_family']}' world {a['name']}'s path needs")
            if a["category_family"] and a["category_family"] in b["supplier_families"]:
                reason.append(f"{a['name']} is in the '{a['category_family']}' world {b['name']}'s path needs")
            if a["category_family"] and a["category_family"] == b["category_family"]:
                reason.append(f"same buyer: {a['category_family']}")
            if len(shared) >= 3:
                reason.append("shared vocabulary: " + ", ".join(sorted(shared)[:5]))
            if reason:
                adjacency.append({"slugs": [a["slug"], b["slug"]], "reasons": reason})
    adjacency.sort(key=lambda r: -len(r["reasons"]))

    # 4. Echo: which buyer worlds are collecting real signals, and which are all build/fund (builders circling) vs buy.
    world = defaultdict(lambda: {"ventures": [], "buy": 0, "build": 0, "fund": 0})
    for x in vs:
        w = x["category_family"] or (x["category"] or "uncategorized").lower()
        world[w]["ventures"].append(x["slug"])
        for k in ("buy", "build", "fund"):
            world[w][k] += x["signals"][k]
    echo = [{"world": w, **d} for w, d in world.items()]
    echo.sort(key=lambda e: -(e["buy"] + e["build"] + e["fund"]) * 10 - len(e["ventures"]))

    # 5. Stalls: paths chosen but not moving; asks with zero hands for a while.
    stalled_paths = [x["slug"] for x in vs if x["path_total"] and x["path_done"] == 0]
    unanswered = [a["id"] for a in ask_rows if a["hands"] == 0]

    # 6. Gaps: supplier families the Floor keeps needing that no venture on the Floor provides.
    provided = {x["category_family"] for x in vs if x["category_family"]}
    gaps = [c for c in convergence if c["family"] not in provided]

    return {
        "counts": {"ventures": len(vs), "with_paths": sum(1 for x in vs if x["path_total"]), "open_asks": len(ask_rows),
                   "hands": sum(a["hands"] for a in ask_rows), "signals": sum(x["signals_total"] for x in vs)},
        "ventures": [{k: x[k] for k in ("slug", "name", "tagline", "category", "category_family", "path_tier", "path_done", "path_total",
                                        "current_step", "supplier_families", "supplier_kinds", "signals")} for x in vs],
        "asks": [{k: a[k] for k in ("id", "slug", "venture", "title", "detail", "step_n", "hands", "family")} for a in ask_rows],
        "convergence": convergence[:8], "ask_pairs": ask_pairs[:8], "adjacency": adjacency[:8], "echo": echo[:8],
        "stalled_paths": stalled_paths[:8], "unanswered_asks": unanswered[:8], "gaps": gaps[:6],
        "_by_slug": by_slug,
    }


def facts_hash(facts: dict) -> str:
    slim = {k: v for k, v in facts.items() if not k.startswith("_")}
    # Unchanged Floor data must not make an older generation standard "fresh".
    slim["_generation_contract"] = ARCHITECT_SYSTEM
    return hashlib.sha256(json.dumps(slim, sort_keys=True, default=str).encode()).hexdigest()[:16]


def thin(facts: dict) -> bool:
    c = facts["counts"]
    return c["ventures"] < 2 or (not facts["convergence"] and not facts["adjacency"] and not facts["ask_pairs"] and c["open_asks"] == 0)


ARCHITECT_SYSTEM = """You are the Architect inside MANIFEST. Every creator on the Floor sees only their own venture. You see all of them at once, and your job is to say what is only visible from up here.

You receive FACTS computed from real database rows: the public ventures, their chosen paths and the supplier types those paths call for, the open asks (what creators need right now), how many real people raised a hand on each, and the real signals (I'd buy / I'd build / I'd fund) each venture has collected. You also receive pre-computed patterns: convergence (several ventures need the same kind of supplier), ask_pairs (open asks one shop could fill together), adjacency (ventures that are each other's customers or suppliers, or serve the same buyer), echo (which buyer worlds are collecting real signals), stalled paths, unanswered asks, gaps.

Write 3 to 6 READINGS. A reading is one thing a sharp operator would notice from above and act on. Each reading:
- "kind": one of "convergence" | "adjacency" | "pool" | "echo" | "gap" | "stall"
- "title": 3-8 words, plain, specific. No hype.
- "insight": 2-3 sentences. Name the ventures (by name). Say exactly what the pattern is and why it matters. Numbers must come from the facts; never invent counts, prices, or people.
- "action": one concrete sentence telling someone what to do about it tomorrow. If it's a pooled supplier, say who should call whom. If it's adjacency, say which creator should message which.
- "slugs": array of the venture slugs this reading is about (only slugs that appear in FACTS).
- "ask_ids": array of ask ids this reading is about (only ids that appear in FACTS), or [].
- "for": "creators" | "helpers" | "both" — who this is useful to.

Then write 2 or 3 CROSSBREEDS: take two real ventures from FACTS and splice them. Steal one specific mechanism, business model, ritual, or material from the donor and graft it onto the host so the host still solves its original problem for its original buyer, but in a way nobody on either page would have seen. The pairing should look wrong at first. Each crossbreed:
- "host_slug", "donor_slug": real slugs from FACTS, different ventures.
- "name": 1-3 plain words for the hybrid. No puns.
- "stole": the exact thing taken from the donor, one sentence.
- "hybrid": 2 sentences: what the host becomes with the stolen part inside it.
- "why_it_does": 2-4 concise sentences: compare the normal way with the borrowed mechanism and explain why the hybrid could be better or equal. Label an untested benefit a hypothesis, name the key assumption and a meaningful tradeoff, and propose a cheap side-by-side test with the original approach.
- "sentence": ONE sentence under 25 words a person could type into MANIFEST to manifest this hybrid as its own venture.

Rules: Only use ventures, asks, counts, and signals present in FACTS as evidence. New mechanisms and combinations may be creative hypotheses: do not suppress a plausible twist just because it is untested. Keep observed patterns distinct from proposed benefits; interest is not sales, and never invent measured gains. Equal performance can justify a hybrid if cost, repairability, accessibility or another constraint improves. Require a credible causal explanation; never invent a rationale for impossible physics. Safety-critical prototypes require qualified review before functional testing. If a pattern in FACTS is weak, skip it; fewer honest readings beat more padded ones. Never flatter. Never say "exciting", "synergy", "ecosystem", "leverage", "unlock". Write like a foreman reading a jobsite, not a consultant. Return ONE JSON object: {"readings": [...], "crossbreeds": [...]} and nothing else."""


ARCHITECT_SYSTEM += "\n\n" + COMPARATIVE_STRESS_TEST


async def write_readings(facts: dict) -> tuple[list[dict], list[dict]]:
    """Ask the engine to write readings (and crossbreeds) over the facts, then validate every reference against real rows."""
    from . import llm

    slim = {k: v for k, v in facts.items() if not k.startswith("_")}
    msg = "FACTS:\n" + json.dumps(slim, indent=1, default=str) + "\n\nReturn the JSON object now. Start with { and end with }."
    provider = llm.text_provider()
    raw = ""
    if provider == "anthropic":
        import anthropic
        client = anthropic.AsyncAnthropic()
        model = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")
        resp = await client.messages.create(model=model, max_tokens=3200, system=ARCHITECT_SYSTEM,
                                            messages=[{"role": "user", "content": msg}])
        raw = "".join(getattr(b, "text", "") for b in resp.content)
    elif provider == "openai":
        from openai import AsyncOpenAI
        client = AsyncOpenAI()
        model = os.environ.get("LLM_MODEL", "gpt-5-mini")
        kwargs = {"reasoning": {"effort": "low"}} if model.startswith("gpt-5") else {}
        resp = await client.responses.create(model=model, instructions=ARCHITECT_SYSTEM, input=msg, **kwargs)
        raw = resp.output_text
    else:
        await asyncio.sleep(0.5)
        raw = json.dumps(MOCK_READINGS)

    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0) if m else raw)
    except Exception:
        return [], []
    valid_slugs = {v["slug"] for v in facts["ventures"]}
    valid_asks = {a["id"] for a in facts["asks"]}
    out = []
    for r in (data.get("readings") or [])[:6]:
        if not isinstance(r, dict):
            continue
        slugs = [s for s in (r.get("slugs") or []) if s in valid_slugs]
        asks = [a for a in (r.get("ask_ids") or []) if a in valid_asks]
        if not slugs and not asks:
            continue   # a reading about nothing real is not a reading
        kind = r.get("kind") if r.get("kind") in ("convergence", "adjacency", "pool", "echo", "gap", "stall") else "adjacency"
        out.append({
            "kind": kind, "title": str(r.get("title") or "")[:80], "insight": str(r.get("insight") or "")[:600],
            "action": str(r.get("action") or "")[:300], "slugs": slugs[:6], "ask_ids": asks[:6],
            "for": r.get("for") if r.get("for") in ("creators", "helpers", "both") else "both",
        })
    crosses = []
    for c in (data.get("crossbreeds") or [])[:3]:
        if not isinstance(c, dict) or c.get("host_slug") not in valid_slugs or c.get("donor_slug") not in valid_slugs or c.get("host_slug") == c.get("donor_slug"):
            continue
        if not c.get("name") or not c.get("hybrid") or not c.get("sentence"):
            continue
        crosses.append({"host_slug": c["host_slug"], "donor_slug": c["donor_slug"],
                        **{k: str(c.get(k) or "")[:lim] for k, lim in (("name", 60), ("stole", 240), ("hybrid", 600), ("why_it_does", 1000), ("sentence", 220))}})
    return out, crosses


MOCK_READINGS = {"readings": [], "crossbreeds": []}


# ---------------------------------------------------------------------------
# Crossbreed: splice a venture with another venture, or with a mechanism from a field that has
# nothing to do with it, and make the hybrid real enough to manifest.
# ---------------------------------------------------------------------------

LATERAL_FIELDS = [
    "vending machines", "beehives", "airport baggage systems", "fishing tackle", "surgical instruments",
    "espresso machines", "sailing rigging", "library card catalogs", "bicycle drivetrains", "chicken coops",
    "fire sprinkler systems", "theater stage rigging", "fly-fishing reels", "slot machines", "grain elevators",
    "mountaineering harnesses", "subscription razor clubs", "lego bricks", "trading card packs", "car washes",
    "food trucks", "lottery scratch-offs", "church collection plates", "pizza delivery", "escape rooms",
    "tattoo parlors", "ant farms", "zip lines", "pinball machines", "irrigation pivots", "blood banks",
    "bowling alley pinsetters", "parking meters", "hotel key cards", "funeral homes", "ski lifts",
    "crossword puzzles", "septic tanks", "laundromats", "military MREs", "coin-operated binoculars",
]

CROSSBREED_SYSTEM = """You are the Architect inside MANIFEST, in crossbreeding mode. You take ONE venture (the host) and splice it with a DONOR: either another venture on the Floor, or a mechanism from a field that has nothing to do with it. Your job is the kind of thinking a good inventor does at 2am: steal a mechanism, a business model, a ritual, a material, or a pricing trick from the donor, graft it onto the host, and find the version that should not work but does.

Rules for a good splice:
- Steal something SPECIFIC from the donor: a mechanism (how it physically works), a model (how money moves), a ritual (how people use it), a constraint (what it refuses to do), or a material. Name what you stole.
- The hybrid must still solve the host's original problem for the host's original buyer. If it drifts into a different product entirely, it is not a splice, it is a new idea.
- Say honestly why it might not work, then give a mechanism-based case for why it could work better than OR equal to the normal way. Identify that baseline, the causal link, the key assumption and a meaningful tradeoff. Equal performance can be worthwhile with lower cost, easier repair, accessibility or another constraint solved.
- Keep the unexpected twist. An untested combination is allowed: label its proposed benefit a hypothesis rather than presenting it as an established fact or flattening it into a normal product. If you cannot find a credible causal case, pick a different thing to steal; do not rationalize impossible physics or fabricate measured gains.
- No puns in names. Plain words. Forbidden: revolutionary, seamless, game-changing, disrupt, synergy, ecosystem, unlock, elevate, reimagine.
- Make the first experiment cheap and accessible. For safety-critical products, begin with a nonfunctional mockup or customer test and require qualified review before functional construction or testing, not hazardous garage experiments.

Return ONE JSON object, nothing else:
{"splices": [ exactly 3 of:
  {"name": "1-3 words, the hybrid's working name",
   "stole": "the exact thing taken from the donor, one short sentence",
   "hybrid": "2-3 sentences: what the product becomes, concretely, and how the stolen part works inside it",
   "why_not": "one sentence: the obvious reason this should not work",
   "why_it_does": "2-4 concise sentences: normal way, causal case for better or equal performance, hypothesis label if unproven, key assumption and tradeoff",
   "first_test": "1-2 sentences: cheapest safe side-by-side test against the normal way on the same task, proposed measurable pass/fail threshold and what to revise if it fails",
   "sentence": "ONE sentence, under 25 words, a person could type into MANIFEST to manifest this hybrid as its own venture"}
]}"""


CROSSBREED_SYSTEM += "\n\n" + COMPARATIVE_STRESS_TEST


def crossbreed_message(host: dict, donor_venture: dict | None, lateral_field: str | None) -> str:
    def brief(v: dict) -> str:
        tiers = v.get("tiers") or []
        return (f"Name: {v.get('name')}\nTagline: {v.get('tagline')}\nWhat it is: {v.get('description')}\nCategory: {v.get('category')}\n"
                f"Lean build: {(tiers[0].get('title') + ' — ' + tiers[0].get('description')) if tiers else ''}\n"
                f"Who pays: {(v.get('grounding') or {}).get('who_pays', '')}")
    parts = ["HOST VENTURE:\n" + brief(host)]
    if donor_venture:
        parts.append("DONOR (another real venture on the Floor):\n" + brief(donor_venture))
    else:
        parts.append(f"DONOR FIELD (nothing to do with the host, on purpose): {lateral_field}\nFirst list, to yourself, the mechanisms, rituals, pricing, and materials that make {lateral_field} work. Then steal.")
    parts.append("Return the JSON object now. Start with { and end with }.")
    return "\n\n".join(parts)


async def write_crossbreed(host: dict, donor_venture: dict | None, lateral_field: str | None) -> list[dict]:
    from . import llm
    msg = crossbreed_message(host, donor_venture, lateral_field)
    provider = llm.text_provider()
    raw = ""
    if provider == "anthropic":
        import anthropic
        client = anthropic.AsyncAnthropic()
        model = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")
        resp = await client.messages.create(model=model, max_tokens=1800, system=CROSSBREED_SYSTEM,
                                            messages=[{"role": "user", "content": msg}])
        raw = "".join(getattr(b, "text", "") for b in resp.content)
    elif provider == "openai":
        from openai import AsyncOpenAI
        client = AsyncOpenAI()
        model = os.environ.get("LLM_MODEL", "gpt-5-mini")
        kwargs = {"reasoning": {"effort": "medium"}} if model.startswith("gpt-5") else {}
        resp = await client.responses.create(model=model, instructions=CROSSBREED_SYSTEM, input=msg, **kwargs)
        raw = resp.output_text
    else:
        await asyncio.sleep(0.5)
        raw = json.dumps({"splices": []})
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0) if m else raw)
    except Exception:
        return []
    out = []
    for sp in (data.get("splices") or [])[:3]:
        if not isinstance(sp, dict) or not sp.get("name") or not sp.get("hybrid"):
            continue
        out.append({k: str(sp.get(k) or "")[:lim] for k, lim in
                    (("name", 60), ("stole", 260), ("hybrid", 900), ("why_not", 360), ("why_it_does", 1000), ("first_test", 500), ("sentence", 220))})
    return out
