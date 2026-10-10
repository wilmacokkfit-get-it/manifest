# Published-host waiting probe

## Status

The published app was confirmed as a public, successful **autoscale** deployment
on 2026-10-10 using deployment metadata. The diagnostic URL returned HTTP 404
through that published host at 14:34 UTC. The probe requires a new publish and
temporary enablement before production measurements can be collected.
**No published idle boundary has been measured yet.** Local ASGI tests and the
existing fake-clock browser suite are not evidence of published proxy behavior.
The app's watchdogs, provider deadlines, and heartbeat policy are unchanged.

The user approved splitting locally verified probe setup from live-host
measurements. A successful republish at 16:14 UTC still returned 404 for the
probe at 16:20–16:25 UTC, including invalid parameters that would produce 422
if the new route were present. This is missing probe deployment, not an
observed connection-timeout boundary. Task work must be applied to the main
project before republishing it; the published app's `app.js` did match the
workspace copy, but that alone did not establish that the new backend route
had been included. The temporary production enablement setting was removed
after the blocked check.

**Handoff:** Apply the probe setup to the main project first. Obtain fresh
approval and a fresh short-lived enablement window, republish, confirm the
`x-manifest-wait-probe: wait-probe-1` header, then run the published-host matrix.
The live measurements and server-disconnect log correlation remain a separate
follow-up; do not mark them verified from the setup's passing local checks.

Local validation passed: six isolated probe checks, the existing JavaScript
helper checks, and a real-clock loopback run covering eight raw-network cases
and four creator-feedback cases. The app preview also rendered normally.
These results validate the diagnostic tooling, not the published host's limit.

## Safety and enablement

`app/wait_probe.py` has no provider, image, database, venture, recovery, or
owner-auth dependencies. It returns only synthetic bytes, sleeps, and log lines.
It creates no durable app records. Diagnostic timings are stored by the runner
under ignored `generated-artifacts/published-waits/`, not in the app database.
Ordinary hosting access logs and a first-party browser cookie can still occur.

The GET endpoint `/api/diagnostics/wait-probe` is **404 by default**. Apply this
task's changes to the main version before publishing. To run a test, obtain
approval to temporarily expose it on the published app, then use
the environment-variable tooling to set production `MANIFEST_WAIT_PROBE_UNTIL`
to Unix seconds less than two hours in the future. Republish the verified code.
The endpoint automatically stops admitting requests at that time. Values more
than two hours ahead fail closed. In-flight probes may finish after the window.
Delete the variable after testing; do not leave permanent test enablement.

The endpoint is unauthenticated during that short diagnostic window. It accepts
only five enumerated scenarios, UUID correlation IDs, and delays of 1–310 seconds.
At most two probes execute at once **per server process**; excess requests return
429 immediately. This is not a fleet-wide rate limit: run one runner at a time
and keep the public enablement window short.

## Run

Read the deployment skill and obtain the current published origin using
`getDeploymentInfo()` before running. Never substitute the development domain
or a URL guessed from the project name.

```sh
node scripts/probe-published-waits.cjs --url <verified-published-origin> --delay 185 --phase all
```

`--phase raw` measures response headers, first body bytes, individual received
chunks, EOF, and network errors using real Chromium `fetch` and real clocks:

| Scenario | What it measures |
| --- | --- |
| `first-byte` | No headers or bytes until the delay ends |
| `json` | Delayed non-streaming JSON response |
| `partial-gap` | Immediate SSE partial, then a completely quiet body gap |
| `heartbeat` | Immediate headers and the app's actual 10-second SSE heartbeat wrapper during a delay |
| `early-eof` | Partial SSE followed by clean EOF with no completion event |

It also deliberately aborts `first-byte`, `partial-gap`, and `heartbeat` after
up to three seconds. Check deployment logs for each `probeId` and
`outcome=disconnected`; client-side abort alone does **not** prove the server
noticed the disconnect. Before-header aborts also have caller-supplied UUIDs
so their logs remain correlatable.

`--phase ui` runs the workspace's real `public/app.js` generation UI under the
published origin. Assets are isolated local fixtures, but probe fetches go
through the real published proxy; streams are not buffered by route fulfillment
and clocks are not faked. No authentication is bypassed in the real app.
All other backend calls are blocked or synthetic. Generation POST is replaced
with diagnostic GET; recovery lookup is fulfilled locally as failed. Completion
is explicitly translated into a synthetic error rather than a saved venture.

The runner records elapsed “Considering” and “Writing” feedback, User Timing,
failure feedback, retry availability, one-submission counts, and screenshots
after transient toasts have expired. Partial text remains an unconfirmed preview.
These are the workspace creator UI's responses to a real proxy connection,
**not a claim that the published build's UI was exercised end to end**.
The existing browser suite separately checks path/refinement and navigation.

Use delays of 65, 125, and 185 seconds to check common boundary candidates.
Use a raw run at 245 or 305 seconds to separate hosting behavior from the
generation UI's existing 210-second watchdog. A UI heartbeat run with delay
245 or 305 should end at that watchdog even if raw heartbeats survive longer.
Runs are sequential; a 185-second all-phase run can take about 22 minutes.
Do not run the whole matrix concurrently.

## Interpret and record evidence

Write measured results here only after running through the published host:

| UTC time / deployment | Scenario / delay | Header / first-byte time | Last-byte / EOF time | Outcome / server disconnect log | Creator-visible feedback |
| --- | --- | --- | --- | --- | --- |
| Not measured | — | — | — | Pending enablement and republish | — |

Deployment-presence checks only (not delay measurements): HTTP 404 before
publishing at 14:34 UTC and after a successful republish at 16:20–16:25 UTC on
2026-10-10. No long-wait request or production creator-feedback case was run.

- A successful N-second wait establishes only a **lower bound** of N seconds,
  not “no timeout.” Distinguish delayed headers from a quiet gap after SSE.
- A network error is not automatically a proxy idle timeout. Correlate its
  timing with server logs; repeat on either side of a suspected boundary.
- Received heartbeat chunks prove delivery, not just upstream generation.
  Multiple events can arrive coalesced by a proxy; inspect their receive times.
- Clean early EOF, network disconnect, intentional client abort, and the
  335-second diagnostic deadline are different outcomes.
- Preserve both raw and UI observations. The UI can stop at its watchdog even
  while an otherwise identical raw HTTP request survives.
- Record host/deployment type, UTC run time, delay, probe IDs, report path,
  observed gaps, and disconnect-log evidence. Do not record credentials or real
  creator content.
- Do not change application timeout policy from an assumed host limit.

## Local checks

```sh
python -m unittest discover -s tests -p test_wait_probe.py
node --check scripts/probe-published-waits.cjs
python -m unittest discover -s tests -p test_wait_probe_network.py
npm test
```

These validate gating, delayed headers/JSON, partial-body gaps, early EOF,
disconnect cleanup, and capacity release. The network test runs the runner
against a disposable loopback ASGI app with two-second waits, real browser
networking, and the real creator UI. Its reports are marked `localTest: true`;
they do not establish a hosting limit.
