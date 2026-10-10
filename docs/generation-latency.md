# Generation latency and waiting feedback

## Current decision

Keep the configured provider/model and **medium** reasoning default. A faster
response is not worth losing explicit product constraints. `LLM_REASONING_EFFORT`
supports an explicit `low`, `medium`, or `high` setting for GPT-5 models; other
models do not receive that parameter. Unknown settings fail, not switch providers.

The creation, safety, evidence and selected-Mix guidance is unchanged. Refinement
input JSON uses compact separators to remove unnecessary whitespace without
removing content. No evidence here supports a major prompt reduction or a
production default change.

## Small live comparison

Six sequential text-only calls on the configured OpenAI `gpt-5-mini`, run on
2026-10-10, with the same fixed synthetic pantry-label sentence and fixed
refinement/path fixture. Each call had a 120-second total text bound.

| Operation | Effort | First text (s) | Completion (s) |
| --- | --- | ---: | ---: |
| Venture | medium | 19.470 | 31.665 |
| Venture | low | 3.899 | 15.274 |
| Refinement | medium | 24.109 | 36.626 |
| Refinement | low | 13.201 | 25.218 |
| Path | medium | 16.502 | 31.436 |
| Path | low | 2.372 | 15.425 |

All six completed inside the bound and returned JSON objects. This is **one sample
per condition**, not a controlled benchmark or proof of general improvement.
Provider load, output lengths and sampling vary. The earlier roughly 74-second
and 20-second observations used different inputs/settings and cannot establish
a speed regression by themselves. First text means the first nonempty visible
output delta, not an internal reasoning token.

A manual read found that the low-effort venture proposed “refill subscriptions”
despite “no subscription” in the fixed sentence. The low-effort path introduced
a QR-code placeholder and a possible digital reminder into an offline concept.
Medium outputs also remain unverified: the medium path has conflicting pass/fail
wording, and supplier/compliance suggestions are not independently checked.
Neither setting is a factual or safety guarantee. These findings support
retaining medium; build-plan factual/safety work is separate.

No application server or store is imported by the comparison. No ventures,
reservations, images or database rows are created. Raw synthetic outputs are
kept locally in `generated-artifacts/latency-comparison.json`, excluded from Git
and deployment.

## Rerunning safely

Without `--live`, the command refuses to make any provider calls:

```sh
python scripts/compare_generation_latency.py
python scripts/compare_generation_latency.py --live
```

The default makes at most six paid calls: three operations, medium then low.
Use `--operations venture` for two calls, or `--efforts medium` for three.
`--timeout` may shorten, but never exceed, 120 seconds. Operations/efforts cannot
be repeated within a run. A second fixed sentence is available via
`--case repairable --operations venture`. There is no provider/model override;
the runner rejects a configured provider/model that cannot support this
comparison. Interrupting the script closes active provider streams and restores
the process's original reasoning setting.

Review complete output for intent, feasibility, useful mechanisms, first-test
quality and consistency before changing defaults. Do not use JSON validity as
a quality score. There is no promise of repeatability from this sample.

## Runtime measurements and limits

`app/llm.py` emits `llm_timing` structured log records under the application
server logger for venture, refinement and path streams. Records contain only
operation, provider/model, effort, first-text time, total elapsed time, output
character count and outcome. No ideas, generated text, owner IDs or secrets are
logged. Failed streams have a failure outcome; externally cancelled/timed-out
streams have a cancellation outcome and the route communicates the timeout.
SDK streams and clients close on completion, failure and cancellation.

All three routes retain the 120-second **text** bound. This is not a 120-second
whole-request guarantee: claims, saving and cleanup have their own existing
bounded waits. Text timeouts have explicit messages; a timeout during saving
instead asks the creator to check the persisted result, avoiding a false claim
that nothing was saved.

The browser shows elapsed waiting time immediately and explains the two-minute
text limit after 30 seconds. It does not invent stages or promise novelty.
Venture/path partial text remains visibly pending until the `done` event.
Early EOF and terminal provider errors are failures, with persistent messages;
partial ventures cannot be mistaken for confirmed records. A 210-second browser
connection watchdog (240 for refinement's extra save/reconciliation work) is
separate from the server's 120-second text bound. Broken connections advise
checking saved ventures before retrying, because a commit can outlive its response.

Local Performance entries are named `manifest.<operation>.first-visible-text`
and `manifest.<operation>.<outcome>`. These are browser end-to-end observations,
not server first-token metrics and not remotely collected analytics.
Refinement currently uses a single JSON response, so it has end-to-end completion
timing but **no browser first-visible-text measurement**; backend first text is
still measured.

Pending generation reservations are released on failure, cancellation and text
timeout. Successful commits are not refunded. Refinement preserves its existing
charge-on-start usage policy even when text fails; its pending work claim is
cleared and the original venture remains intact. Failed/timed-out path claims
are also released. Cleanup failure still relies on the existing durable lease
recovery rather than pretending a failed store operation succeeded.

## Offline checks

```sh
python -m unittest discover -s tests -p 'test_*.py'
node --check public/app.js
node tests/test_generation_feedback.cjs
```

Timeout tests shorten the bound to milliseconds and cover no-first-text and
partial-text stalls, cleanup, unchanged originals, retries, terminal provider
failure, early provider EOF, cancellation/resource closure and invalid settings.
Feedback tests use the actual production helper with a deterministic clock.
They do not verify a hosting proxy's idle timeout or a real browser's interactive
loading states. The live comparison tests only text providers, not the app routes.
