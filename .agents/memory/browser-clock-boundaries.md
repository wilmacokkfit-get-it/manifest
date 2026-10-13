---
name: Browser clock boundaries
description: Keeping browser waiting tests provider-free while observing real Performance entries
---

Use browser-native streaming response objects and fail-closed network interception
for waiting/disconnect checks. Fake elapsed time must not replace the browser's
actual User Timing buffer.

**Why:** Playwright's fake Performance object discards measures by default, so a
test can paint elapsed feedback correctly yet observe no timing entries. Buffered
route responses also cannot exercise feedback between incremental SSE chunks.

**How to apply:** Preserve native measure/getEntries methods before installing the
clock and retain fake numeric start/end values. Keep preservation, clock installation,
and restoration in the same context-script scope: Firefox executes context
scripts before page scripts, unlike the ordering previously observed in Chromium.
Require native PerformanceMeasure instances, not only matching duration arrays.
Use a secure synthetic origin
because request identity creation depends on secure-context crypto APIs.
Synthetic browser checks do not verify a hosting proxy's idle behavior.

Physical Safari sign-off requires observations from a real iPhone and its exact
iOS version. Keep device checks on a dedicated HTTPS origin, with memory-only
app storage and fail-closed provider networking.

**Why:** Mobile WebKit emulation cannot establish iOS suspension behavior. LAN
HTTP does not supply secure-context request identities, and an existing origin's
service worker can intercept a fixture before its in-page safeguards load.

**How to apply:** Record both wall and performance elapsed time and genuine
visibility transitions, then review watchdog/retry and post-navigation state.
Never label automated harness results or unreviewed exports as physical-device
verification, and never interpret in-browser synthetic streams as host-timeout
measurements.

For phone-only device-check handoffs, provide a working HTTPS link that opens
in external Safari rather than requiring desktop terminal setup. Any temporary
replacement of the development preview needs approval and subsequent restoration.

**Why:** A user working from the iOS Replit app could not use the original
computer-based fixture instructions; an approved temporary synthetic preview
enabled the real Safari checks without deploying or using providers.

**How to apply:** Keep provider access and durable storage blocked. Separate
user-reported manual outcomes from exported measurements in the evidence summary;
a brief success confirmation must not become invented timer or abort data.

Treat retirement confirmation as user consent, not as a cross-tab lock. A
terminal response for an old identity does not authorize replacing newer work
started in another tab.

**Why:** Network replies can arrive after another tab has retired an identity
and begun a replacement; confirmation dialogs only pause the confirming page.

**How to apply:** Exercise held lookup and retirement replies with real shared
browser storage, including a different-sentence submission already waiting on
the old lookup. Keep server save/retirement serialization a separate check.

Keep raw HTTP wait measurements separate from creator-UI measurements when
testing a published proxy. Do not use real generation requests as probes.

**Why:** A client watchdog can end the UI before the host would disconnect;
provider work also adds unrelated latency and may consume quotas or create
durable records, making a hosting experiment both ambiguous and unsafe.

**How to apply:** Use disposable synthetic upstream bytes and real clocks;
correlate client disconnects with server cleanup logs before naming a host
timeout boundary. Successful waits establish lower bounds, not unlimited waits.

Keep browser compatibility libraries isolated from the test runner process.

**Why:** Nix libraries needed by upstream browser downloads can override Node's
newer SQLite/ICU/C++ runtime and prevent the runner from starting. Headless WPE
also needs an explicitly discoverable Mesa EGL vendor and software renderer;
a successful browser launch alone does not prove it can create a page.

**How to apply:** Supply resolved libraries and renderer settings only to browser
launches. Preserve inherited library paths in upstream wrappers that overwrite
them. Smoke-test page creation before debugging application assertions.
