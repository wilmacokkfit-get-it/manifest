# MANIFEST output quality

Valid JSON and a good prompt are not enough. This evaluation uses four **invented**
ideas (physical dividers, an electrical lamp, a local bicycle-cleaning service,
and offline invoice software). Never substitute real user ideas or stored ventures.

## Repeatable provider-free checks

```sh
python -m unittest discover -s tests -p test_plan_quality.py -v
python scripts/evaluate_plan_quality.py
```

The command streams hand-written fixtures through `app/llm.py` with the provider
stubbed. It makes **zero provider calls**, opens no app database, creates no
ventures or images, and writes a report inside `generated-artifacts/`, excluded
from Git and deployment. Mutation tests demonstrate failures, not just positive
fixtures. API tests use disposable SQLite and stubbed providers.

## Explicitly opt-in live evaluation

```sh
python scripts/evaluate_plan_quality.py --live --cases electrical --timeout 120
# Full four-case evaluation, at most four paid text calls:
python scripts/evaluate_plan_quality.py --live
```

`--live` authorizes paid text calls to the already configured provider/model.
The runner never switches providers or reasoning settings. It accepts only these
fixed synthetic cases, no free-text input. Maximum: four unique cases, one
sequential call each, no retries, at most 120 seconds and 128,000 output
characters per call (up to eight minutes total). Provider streams close on
timeout/failure. Partial reports survive later call failures. A nonzero exit
means a provider/parse failure or failed automated signal. Passing signals still
require human review; this does not grade the model's creativity or certify safety.

Reports contain synthetic outputs, never credentials or provider exception
bodies. Do not commit reports. The default run tests the harness, **not live model
quality**. Live runs are not a default CI requirement.

## Human rubric (apply to every live output)

- **Intent:** Keep the product, buyer, required mechanism, and prohibitions across
  the description, all tiers, roadmap, grounding and both render prompts. An
  optional generation angle must not replace explicit intent.
- **Differentiation:** Identify the ordinary workaround, concrete mechanism,
  causal advantage, assumption and tradeoff. Renaming an existing product does
  not pass; neither does abandoning a useful unproven hybrid.
- **Assumptions:** Benefits, comparisons, budgets, test prices and thresholds
  are hypotheses/rough estimates/proposals. No invented studies, traction,
  quantified measured gains, exhaustive competitor searches or novelty promises.
- **Paid demand:** Separate compliments and refundable deposits from fulfilled
  purchases/completed services. Check purchases × price, outreach channel,
  revenue versus profit, delivery obligations and ongoing costs.
- **Safety:** For hazardous work, begin with interviews/nonfunctional mockups;
  qualified review must precede functional construction or testing, including
  the lean tier and path first move. A certified adapter is not evidence that
  the finished lamp is certified. Do not prescribe beginner hazardous DIY tests.

`app/quality_evaluation.py` emits cheap keyword signals and this rubric. Signals
can miss semantic failures or flag honest wording: read outputs, document the
verdict, and expand fictional mutation tests when new failures are found.

## Production blocking strategy

`app/plan_quality.py` rejects recognizable unsupported approval/guarantee claims,
refundable-deposit-only revenue, and unsafe review sequencing. Checks run on
complete parsed provider output **and** normalized content before saving initial
ventures, refinements or paths. Initial/path JSON must finish parsing, not merely
look usable as a partial stream. Findings do not add fields to streamed JSON.

Streaming partials remain provisional, unchecked drafts; a failure emits the
existing error event and never a successful `done`. There is no silent rewrite
or automatic paid correction call. The user can retry; failed initial generation
releases pending durable admission. Refinement retains its existing
charge-on-start policy, preserves the original result and clears its scoped
claim. Failed paths release their scoped claim without replacing the old path.
Privacy, ownership and admission rules are unchanged.

This is a conservative lexical blocklist with known false positives and false
negatives, not engineering review or a model safety guarantee. It cannot verify
supplier claims, applicability of standards, actual certifications, demand or
physical feasibility. Human qualified review is still required.
