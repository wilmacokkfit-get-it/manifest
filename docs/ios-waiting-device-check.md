# Real iPhone Safari waiting check

## Status and boundary

**Venture lifecycle has limited physical-device evidence:** elapsed feedback
was observed and watchdog/retry and navigation were reported successful on a
user-confirmed iPhone 12 running iOS 27.2. The
[reviewed observation summary](ios-waiting-observations.md) distinguishes exported
measurements from manual confirmation. The full device matrix below has not
been completed. Automated mobile WebKit results are not real Safari results.
Do not fill in an iOS version from an emulated user agent.
This check observes client timers, feedback, aborts, and SPA navigation. All
responses are in-browser synthetic streams or JSON; there is no upstream socket
to time out. It cannot measure a published host's timeout.

## Disposable setup

Run on a computer with Node and this checkout:

```sh
npm run test:ios-device
# Dedicated fixture server on port 8099. Ctrl-C removes it.
```

No npm install is required to serve the check. No Python application, credentials,
environment secrets, provider, Supabase connection, or database is used.
The server serves only `/` (controls) and `/app.html` (actual app markup, CSS,
client and owner-auth scripts); every other route/method returns 404. Assets are
inlined, external links/fonts removed, and caching disabled.

Open this **separate fixture server** through an already trusted HTTPS local
reverse proxy or temporary HTTPS tunnel on the iPhone. Do not route it to the
ordinary app server, deploy it as the app, or enable published wait probes.
Use a fresh dedicated hostname/origin with no existing app storage or service
worker; a previously registered worker can intercept a page before its safeguards
load. Opening a LAN `http://` address is insufficient: the app needs secure-context
crypto. Desktop `http://localhost:8099` works for a smoke check only.
Do not weaken Safari security or supply any app keys to the fixture.

Fetch is fail-closed: only the fixture's explicit mock APIs are allowed;
`connect-src 'none'`, disabled workers, blocked forms, and blocked non-SPA
links provide additional containment. Generation streams and stalled JSON use
the same runtime as `tests/browser/generation.fixture.cjs`.
The frame replaces localStorage and sessionStorage with fresh in-memory maps
before the app starts. A failed replacement prevents the app from starting.
No app cookies, quota claims, records or renders are created.
Do not test other live product controls: unmocked requests intentionally fail.

## Extended physical-device matrix

Enter the exact version from **Settings → General → About**, the iPhone model,
and confirm that this is Safari on a physical iPhone. Record whether switching
apps or locking the screen was used in the notes. Use normal Safari first;
Private Browsing is not a substitute for the main matrix.

For each case click **Start fresh case**. This discards the previous frame and
its memory-only request identity. The report retains observations for each case
in memory until you reload/close the outer page. Never reload it mid-matrix.

| Operation | Cases |
| --- | --- |
| Venture | Elapsed before text; elapsed after partial; watchdog/retry; navigation before text; navigation after partial |
| Path | Elapsed before text; elapsed after partial; watchdog/retry; navigation before text; navigation after partial |
| Refinement | Elapsed before text; watchdog/retry; stalled JSON body watchdog/retry; navigation before text |

1. **Elapsed:** switch to another app for 45–60 seconds and return to Safari.
   Wait two seconds and select **Record snapshot**. Feedback should include
   the background interval, not restart at 0s. Compare feedback seconds with
   `wallElapsedMs` and `performanceElapsedMs` (allow three seconds for paint and
   tapping). Before text expect “Considering your idea”; after partial expect
   “Writing”. “Still working” should be present after 30 seconds.
   Select **Complete current response**: the result must show and both tracked
   timer counts must be zero. Completion must have a native timing entry.
2. **Watchdog:** switch apps for **at least 4 minutes 15 seconds**. The real
   deadlines are 210 seconds for venture/path and 240 seconds for refinement.
   Return, wait two seconds, record the error text, aborted flag and retry state.
   Expect the deadline to have taken effect, persistent actionable error,
   one failed timing entry, zero tracked timers and enabled retry controls.
   If Safari postpones the deadline further, record what happened; do not call
   it a pass or infer a host timeout. Select **Retry and complete**. A second
   request must actually finish and leave zero timers, not just enable a button.
   For stalled JSON the fixture sends headers and `{"name":` before backgrounding.
3. **Navigation:** switch apps for 45–60 seconds while waiting, return and select
   **Navigate to About**. Expect `/about`, old request aborted, zero tracked
   timers, and an interrupted timing entry. Switch apps again for over 4 minutes
   15 seconds, return, and record a snapshot. About must remain open with no
   new callback, failed timing entry, or venture replacing it.
4. Every sample must have empty `errors` and `unexpectedRequests`. A duplicate
   first-text/completion entry, unexpected API, retained timer, or page reopening
   is a failure to investigate. Record exact observed text and notes, not only
   “works”. If Safari reloads or discards the tab, record that separately; missing
   history is not evidence of safe resumption.

The outer page records visibility changes, pagehide/pageshow (including
back-forward-cache flags), immediate foreground state and a two-second settled
state, both clocks, timer counts, request aborts, retry controls and native
Performance entries. It does not synthesize visibility events or alter clocks.
You must see a genuine hidden → visible sequence for each tested transition.
Capturing these events alone does not prove the OS actually suspended JavaScript.

## Evidence and cleanup

Select **Export observation JSON**, save/share the download from Safari and
attach it for review. Export is the only intentional file write; no automatic
telemetry is sent. Reports are labeled `UNVERIFIED_DEVICE_OBSERVATIONS` until
a reviewer checks the complete physical-device matrix and metadata.

Keep evidence in ignored `generated-artifacts/ios-device/` if copied into the
checkout. Record the exact iOS version, model, mode, each case's outcome and
failures in a reviewed summary; do not commit a raw user-agent/report dump.
Close the tab, stop the fixture server and remove the temporary proxy/tunnel.

Harness smoke checks run as part of `npm run test:browser`, or separately:

```sh
npm run test:browser -- tests/browser/device-check.spec.cjs --workers=3
```

These smoke checks verify isolation, native timing, completion and navigation
with real clocks in desktop/mobile engines. They cannot switch an iPhone to
another app and cannot complete the physical-device sign-off.
