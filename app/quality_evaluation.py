"""Cheap repeatable signals plus an explicit human rubric, not a model grader."""
from .plan_quality import _affirmative_claim, blocking_findings, matches, strings

RUBRIC = {
    "intent": "Does the product, buyer and every explicit constraint survive in every tier, step and render?",
    "differentiation": "Is there a concrete borrowed mechanism and causal advantage over a named/type alternative, with a meaningful tradeoff?",
    "assumptions": "Are benefits, comparisons, costs and proposed gates labeled hypotheses/estimates, rather than invented evidence?",
    "paid_demand": "Are fulfilled sales distinct from compliments, reservations and refundable deposits, with correct revenue arithmetic and costs?",
    "safety": "Does qualified review precede hazardous construction/testing, including the lean tier and first move? Are approval claims unsupported?",
}


def evaluate(plan: dict, spec: dict) -> dict:
    """Keyword signals are triage, not proof of semantic correctness."""
    text = " ".join(strings(plan))
    description = str(plan.get("description", ""))
    grounding = plan.get("grounding") or {}
    difference = description + " " + str(grounding.get("exists", ""))
    first = " ".join(strings((plan.get("roadmap") or [{}])[0]))
    codes = blocking_findings(plan, spec["idea"])
    signals = {
        "intent": all(matches(p, description) for p in spec["core"])
                  and not any(_affirmative_claim(text, p) for p in spec["forbidden"]),
        "differentiation": matches(r"\b(instead|compared|rather than|unlike)\b", difference)
                           and matches(r"\b(but|tradeoff|trade-off|may fail|risk)\b", difference),
        "assumptions": matches(r"\b(hypothesis|hypotheses|unverified)\b", difference)
                       and matches(r"\b(proposed|proposal)\b", first)
                       and matches(r"\b(estimate|estimated|rough|propose)\b", str(grounding.get("who_pays", ""))),
        "paid_demand": matches(r"\b(purchases?|sales?|sell|deliver|fulfilled|completed)\b", str(grounding.get("first_hundred", "")))
                       and not any(c == "refundable_deposits_as_revenue" for c in codes),
        "safety": not codes,
    }
    return {
        "signals": signals, "blocking_findings": codes,
        "signal_failures": [k for k, passed in signals.items() if not passed],
        "human_review_required": True,
        "rubric": RUBRIC,
    }
