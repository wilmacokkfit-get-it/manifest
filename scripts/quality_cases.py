"""Invented inputs and hand-written outputs; never copied from user ventures."""
from copy import deepcopy

CASES = {
    "physical": {
        "idea": "An offline reusable drawer divider kit for sewing clubs, with sliding tabs, no electronics or subscription.",
        "core": [r"drawer|divider", r"sewing", r"sliding|tabs"],
        "forbidden": [r"\bsubscription\b(?![- ]free)", r"\b(camera|electronics|AI)\b"],
        "description": "A drawer divider kit for sewing clubs uses sliding tabs instead of fixed bins. The hypothesis is that moving tabs accommodates changing spool sizes, but loose tabs may drift.",
    },
    "electrical": {
        "idea": "A repairable desk lamp for dorm residents, with replaceable shade panels and no subscription.",
        "core": [r"lamp|light", r"dorm", r"repair|replaceable", r"shade"],
        "forbidden": [r"\bmonthly\b"],
        "description": "A repairable desk lamp for dorm residents uses replaceable shade panels instead of a sealed lamp. The hypothesis is that replacing only damaged panels reduces waste, but extra seams may leak light.",
    },
    "service": {
        "idea": "A one-town bicycle cleaning service for delivery riders, booked by text, without an app or repairs.",
        "core": [r"bicycle|bike", r"clean", r"delivery riders", r"text"],
        "forbidden": [r"\b(?:build|launch) an app\b", r"\brepair service\b"],
        "description": "A bicycle cleaning service for delivery riders groups text bookings by pickup corner instead of individual trips. The hypothesis is that shared pickup reduces travel cost, but delays may offset savings.",
    },
    "software": {
        "idea": "An offline desktop invoice checker for freelance illustrators, using local CSV files, no cloud uploads or subscription.",
        "core": [r"invoice", r"illustrator", r"offline|local", r"CSV"],
        "forbidden": [r"\b(?:upload|sync).{0,25}cloud\b", r"\bmonthly (?:fee|plan|subscription)\b"],
        "description": "An offline desktop invoice checker for freelance illustrators flags duplicates in local CSV files instead of cloud bookkeeping. The hypothesis is that local comparison avoids uploads, but varied CSV formats may cause false matches.",
    },
}


def fixture(case):
    spec = CASES[case]
    safety = case == "electrical"
    plan = {
        "name": "Trial Kit", "tagline": "A small test of a specific mechanism.",
        "description": spec["description"], "category": "Synthetic evaluation",
        "accent": "#dab35f",
        "tiers": [
            {"label": label, "title": title, "description": detail}
            for label, title, detail in [
                ("The Lean Build", "Paper comparison", "Use a nonfunctional paper mockup for interviews; rough estimate $20."),
                ("The Real Product", "Small paid pilot", "After the proposed gates pass, deliver a small paid pilot; rough estimate $500."),
                ("The Empire", "Repeat after evidence", "Expand only after repeat fulfilled purchases; rough estimate $2000."),
            ]
        ],
        "roadmap": [
            {"step": 1, "action": "Compare a paper mockup with the usual workaround.",
             "where": "Interview five intended buyers. Proposed pass/fail gate: three choose the new mechanism; revise it if fewer do."},
            {"step": 2, "action": "Commission qualified electrical review." if safety else "Revise the mechanism from buyer interviews.",
             "where": "A qualified electrical engineer reviews the design before functional construction." if safety else "Revise based on the five buyer interviews."},
            {"step": 3, "action": "Commission a functional pilot after passing the earlier gates.",
             "where": "Use a professional supplier under the reviewed test plan." if safety else "Offer a limited pilot to the interview group."},
            {"step": 4, "action": "Deliver the paid pilot.",
             "where": "Estimate five fulfilled purchases at $20 each, after testing; revise price if fewer than three buy."},
            {"step": 5, "action": "Expand only after repeat purchases.",
             "where": "Document costs and repeat paid demand before scaling."},
        ],
        "grounding": {
            "exists": spec["description"] + " This comparison is unverified, not live market research.",
            "who_pays": "The intended buyers in the description; propose a $20 test price, not proven willingness to pay.",
            "first_hundred": "Offer five delivered purchases at an estimated $20 each to the interview group: $100 revenue, not profit. Refundable deposits are interest, not earned revenue.",
        },
        "image_prompts": [spec["description"] + " Dark studio render, no text.", spec["description"] + " In realistic use, no text."],
    }
    if safety:
        plan["tiers"][0]["description"] += " Qualified electrical review is required before functional construction."
    return deepcopy(plan)
