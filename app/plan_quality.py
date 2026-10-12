"""Conservative output checks, not a safety certification or semantic judge.

Only generated fields are examined. No content is logged, no provider is called,
and exceptions expose fixed rule names rather than a person's idea or output.
"""
from __future__ import annotations

import re


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def matches(pattern, text):
    return bool(re.search(pattern, text, re.I))


HAZARD = r"\b(electrical|electric|mains|voltage|wiring|battery|desk (?:light|lamp)|ladder|load.bearing|structural|food.contact|medical|gas burner|pressure vessel)\b"
REVIEW = (
    r"(?:\b(qualified|licensed|accredited)\b.{0,120}\b(review\w*|assess\w*|approv\w*)"
    r"|\b(review\w*|assess\w*|approv\w*)\b.{0,120}\b(?:by|with|from)\b"
    r".{0,50}\b(qualified|licensed|accredited)\b)"
)
FUNCTIONAL = r"\b(build\w*|assembl\w*|wir(?:e|ing)|weld\w*|solder\w*|bend\w*|energiz\w*|power\w*|load.test\w*|test\w*|prototyp\w*|fabricat\w*|construct\w*|install\w*|hang\w*|functional pilot)\b"
NONFUNCTIONAL = r"\b(non[- ]?functional|unpowered|paper|cardboard|mockup|mock-up|interview|survey|sketch|render|drawing)\b"
PHYSICAL_WORK = r"\b(wire|weld\w*|solder\w*|energiz\w*|power|load.test\w*|hang|install\w*|(?<!non-)(?<!non )functional (?:prototyp\w*|pilot|construct\w*|test\w*))\b"
DIRECT_WORK = r"\b(build\w*|assemble\w*|bend\w*|test|prototype|fabricate\w*|construct\w*)\b"
CERTIFICATION = r"\b(?:UL[- ](?:listed|certified|approved)|CE[- ](?:certified|marked|approved)|FCC[- ](?:certified|approved)|FDA[- ]approved|(?:is|are|already|fully)\s+(?:certified|compliant)|(?:meets|passes)\s+(?:all\s+)?(?:safety|regulatory|UL|CE|FCC)\s+(?:standards|requirements))\b"


def _affirmative_claim(text, pattern):
    # Clause-level exclusions: "not certified" and "seek certification" are not
    # assertions that the generated product already holds an approval.
    for clause in re.split(r"[.!?;\n]", text):
        for claim in re.finditer(pattern, clause, re.I):
            prefix = re.split(r",|\b(?:and|but)\b", clause[:claim.start()], flags=re.I)[-1][-90:]
            if not matches(
                r"\b(not|never|no|without|unverified|unknown|pending|must|requires?|seek|obtain|before|if|until|do not|don't|cannot|can't)\b",
                prefix,
            ) and not matches(r"^\s+(?:is |are )?(?:unverified|unknown|pending)\b", clause[claim.end():]):
                return True
    return False


def _functional(text):
    # Evaluate actions separately: a sketch elsewhere in the sentence cannot
    # authorize wiring, welding, powering or a functional prototype. Strong
    # physical-work verbs also cannot be hidden by "paper" in their own clause.
    for sentence in re.split(r"[.!?;\n]", text):
        planning = matches(r"\b(?:review\w*|assess\w*|defin\w*|document\w*|request\w*)\b", sentence)
        for clause in re.split(r",|\b(?:and|then|but)\b", sentence, flags=re.I):
            if matches(
                r"^\s*functional (?:construction|testing)\s+is\s+"
                r"(?:(?:explicitly|strictly)\s+)?(?:forbidden|prohibited|blocked)\s+"
                r"until\s+(?:the\s+)?(?:qualified\s+)?review"
                r"(?:\s+clears(?:\s+the)?\s+design)?\s*$",
                clause,
            ):
                continue
            # A description of review *before* future work is not conducting
            # that work. "Wire before review" retains the wire instruction.
            clause = re.split(r"\b(?:before|prior to)\b", clause, maxsplit=1, flags=re.I)[0]
            if matches(r"\b(?:do not|don't|never|must not|cannot|can't)\b", clause):
                continue
            if matches(PHYSICAL_WORK, clause):
                return True
            if matches(FUNCTIONAL, clause) and not matches(NONFUNCTIONAL, clause):
                # Nominal "fabrication and supervised tests" can be objects of
                # "define a plan"; direct build/test instructions cannot.
                direct_work = re.sub(
                    r"\b(?:define|document|review|assess|request)\w*\s+"
                    r"(?:(?:the|proposed|a)\s+)?test\s+"
                    r"(?:loads?|plans?|protocols?|requirements?|criteria|results|reports)\b",
                    "", clause, flags=re.I,
                )
                if not planning or matches(DIRECT_WORK, direct_work):
                    return True
    return False


def _deposit_revenue(text):
    if not matches(r"\b(refundable|returnable)\b", text) or not matches(r"\b(deposits?|reservations?)\b", text):
        return False
    # A separate, explicit fulfilled sale is different from a reservation.
    fulfilled_sale = matches(
        r"\b(?:sell\w*.{0,80}(?:deliver\w*|fulfil\w*)|"
        r"(?:deliver\w*|fulfil\w*).{0,80}(?:purchases?|orders?|units?|hooks?)|"
        r"completed services?|non[- ]?refundable purchases?)\b", text,
    )
    for clause in re.split(r"[.!?;\n]", text):
        if not (matches(r"\b(refundable|returnable)\b", clause)
                and matches(r"\b(deposits?|reservations?)\b", clause)):
            continue
        disclaimer = matches(
            r"\b(?:not|never|no)\b.{0,25}\b(?:earned |recognized |proven )?(?:revenue|paid demand|sales?)\b",
            clause,
        )
        if not disclaimer and matches(r"\b(earn\w*|revenue|paid demand)\b", clause):
            return True
    # Even an honest deposit disclaimer is not itself a route to first revenue.
    return not fulfilled_sale


def blocking_findings(plan: dict, idea: str = "", *, path: bool = False) -> list[str]:
    """Return stable codes for recognizable invalid output; passing proves little.

    Incoming ideas are used only to detect hazard context, never as evidence of
    certification. User assertions cannot bypass review or the claims check.
    """
    fields = ("first_move", "steps", "watch_out") if path else (
        "name", "tagline", "description", "category", "tiers", "roadmap",
        "grounding", "image_prompts",
    )
    text = " ".join(strings({key: plan.get(key) for key in fields}))
    codes = []
    if _affirmative_claim(text, CERTIFICATION):
        codes.append("unsupported_certification")
    if _affirmative_claim(text, r"\b(guaranteed (?:safe|success|novelty)|patent[- ]?proof|no competitors|nothing (?:similar|like this) exists)\b"):
        codes.append("unsupported_guarantee")
    if not path:
        revenue = str((plan.get("grounding") or {}).get("first_hundred", ""))
        if _deposit_revenue(revenue):
            codes.append("refundable_deposits_as_revenue")
    if matches(HAZARD, idea + " " + text):
        sequence = plan.get("steps" if path else "roadmap") or []
        reviewed = False
        if path and _functional(str(plan.get("first_move", ""))):
            codes.append("hazardous_first_move")
        for step in sequence:
            step_text = " ".join(strings(step))
            action = str(step.get("title" if path else "action", ""))
            instructions = step.get("how", []) if path else [step.get("where", "")]
            functional = _functional(action) or any(_functional(s) for s in strings(instructions))
            # Review must be an earlier step, not a caveat in the same build step.
            if functional and not reviewed:
                codes.append("review_after_functional_work")
                break
            if matches(REVIEW, step_text):
                reviewed = True
        if not reviewed:
            codes.append("missing_qualified_review")
        if not path:
            lean = (plan.get("tiers") or [{}])[0]
            lean_text = str(lean.get("description", ""))
            if _functional(lean_text) and not matches(REVIEW, lean_text):
                codes.append("ungated_functional_lean_build")
    return list(dict.fromkeys(codes))


class PlanValidationError(RuntimeError):
    """A completed draft rejection, distinct from a provider/connection failure."""

    def __init__(self, findings: list[str]):
        self.findings = tuple(findings)
        reasons = []
        if any(code in findings for code in (
            "review_after_functional_work", "missing_qualified_review",
            "ungated_functional_lean_build", "hazardous_first_move",
        )):
            reasons.append(
                "The draft must put qualified safety review before functional "
                "construction or testing, including in the lean build."
            )
        if "unsupported_certification" in findings or "unsupported_guarantee" in findings:
            reasons.append("The draft made a certification or guarantee claim without evidence.")
        if "refundable_deposits_as_revenue" in findings:
            reasons.append("Refundable reservations cannot be counted as earned revenue.")
        super().__init__(
            "Draft needs correction—not saved. " + " ".join(reasons)
            + " The factual/safety checks remain in place; this is not a connection stall."
        )


def require_valid_plan(plan: dict, idea: str = "", *, path: bool = False) -> None:
    findings = blocking_findings(plan, idea, path=path)
    if findings:
        raise PlanValidationError(findings)
