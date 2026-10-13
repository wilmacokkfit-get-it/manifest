# Browser waiting and disconnect checks

## Run the matrix

```sh
npm ci
npm run install:browser
npm run test:browser -- --workers=3
```

On a supported Ubuntu CI host that needs OS libraries, use
`npm run install:browser -- --with-deps` (requires permission to install system
packages). On macOS, the ordinary install command downloads native browsers.
Reinstall browsers after changing the locked Playwright version. Firefox and
WebKit must use Playwright's matching patched builds, not ordinary system
Firefox or Safari. `PLAYWRIGHT_CHROMIUM_EXECUTABLE` can select another Chromium
executable; Replit defaults to its system Chromium.

The default run executes the waiting and recovery cases in each of four projects:
The configured `browser-waiting` validation installs matching browsers first,
then runs this full matrix with three workers.

| Project | Browser / viewport |
| --- | --- |
| `chromium` | Desktop Chrome preset, 1280 × 720 |
| `firefox` | Desktop Firefox preset, 1280 × 720 |
| `webkit` | Desktop Safari preset, 1280 × 720 |
| `mobile-webkit` | iPhone 13 preset, 390 × 664, touch/mobile, device scale factor 3 |

The two legacy-dismissal cases deliberately use 390 × 844 in every project.
Filter a project with `npm run test:browser -- --project=firefox` or
`--project=mobile-webkit`. No application server, database, credentials, or
provider access is needed for this suite.

The suite loads the actual `public/index.html`, `app.js`, CSS and owner-auth
script in each browser. Context-wide routing intercepts all networking and
service workers are blocked so they cannot bypass interception. It never imports the Python
server, connects to Supabase, invokes a provider, creates ventures, or renders
images. Each test gets a fresh browser context and local storage.
Only allowlisted local assets and explicitly mocked APIs are served. Unknown
API requests fail with synthetic HTTP 500 and fail the test; unknown local
assets are aborted and fail the test. Third-party assets receive empty bodies,
never forwarded requests.

Browser-native Response and ReadableStream objects supply synthetic incremental
SSE and delayed JSON. Playwright's fake clock advances long waits and watchdog
deadlines without actually waiting several minutes. Its default Performance
stub would discard User Timing entries, so the harness retains each engine's native
measure/getEntries methods while using the fake numeric start/end times.
Assertions also check that returned entries are actual `PerformanceMeasure`
instances, not synthetic arrays.

Checks include:

- Elapsed and “still working” feedback before text, and “writing” feedback after
  partial SSE text, for generation and path selection.
- JSON refinement waiting, preservation of the first result and context on
  failure, and enabled retry controls.
- Persistent early-EOF and provider-error messages after transient toasts expire.
- Byte-level SSE splits inside JSON keys, UTF-8 characters (2/3/4 bytes) and the
  final blank-line delimiter, plus one-byte and irregular chunk sizes.
- Multiple events in one chunk preserve event order and record first text only
  once; partial JSON/Unicode or an unterminated final frame cannot confirm a
  result. Fragmented errors persist and manual fragmented retries actually finish.
- Client watchdog aborts, including a stalled refinement JSON body.
- Successful completion of a new synthetic retry after each operation times out.
- Real first-visible-text and completion Performance entries and no duplicate
  first-text entries. JSON refinement intentionally has no first-text entry.
- Navigation before/after partial text aborts requests, clears feedback/deadline
  timers, and prevents old callbacks from changing the new page.
- Navigation while a disconnected manifest is reconciling ignores a late saved
  result instead of reopening the abandoned venture.

Run the deterministic helper/recovery checks separately with `npm test`.
`generation.fixture.cjs` exposes `sendBytes`, `sendChunks(text, sizes)` (cyclic
positive byte sizes), and `sendFrames(frames, sizes)` (one coalesced chunk when
sizes are omitted). Text is encoded once before splitting; `sseFrame` formats
events. These controls retain browser-native decoding and context-wide routing.
Failure traces are written to ignored `generated-artifacts/browser-results/`.
Browser downloads default to ignored
`generated-artifacts/playwright-browsers/`; `PLAYWRIGHT_BROWSERS_PATH` can
override that location.

## Platform-specific differences

- **Firefox initialization:** Playwright's clock scripts are context-scoped.
  Firefox runs those before page-scoped scripts, so a page-scoped capture of
  `performance.measure` can accidentally save the fake implementation and return
  no measures. Native capture, clock installation, and restoration are therefore
  all registered in the same context scope, in that order. The same approach is
  used for Chromium and WebKit, including after reload/navigation.
- **Replit/Nix libraries:** `scripts/browser-libraries.nix` resolves library paths
  against the `.replit` channel. The installer downloads matching Ubuntu 24.04
  x64 Firefox/WebKit builds; `scripts/browser-environment.cjs` supplies Nix
  libraries only to browser processes, not Node (mixing Node's SQLite/ICU with
  older browser libraries can prevent Node from starting).
  WebKit's downloaded MiniBrowser wrapper normally replaces `LD_LIBRARY_PATH`.
  The installer idempotently appends the inherited path after its bundled
  libraries. Unexpected wrapper changes fail explicitly instead of silently
  dropping WebKit coverage.
- **Headless WebKit:** Linux uses WPE WebKit, not Apple's Safari application.
  Mesa's EGL vendor file and software-rendering driver path must be explicit
  on Nix or WebKit can launch but abort when creating its first page. The
  Replit setup uses software rendering and skips Playwright's generic OS-library
  precheck because it ignores per-browser environment paths; missing libraries
  still fail browser launch. The fallback-build/validation notices and WPE's
  automation-session warning are not test failures.
- **Mobile WebKit:** The iPhone preset changes viewport, touch behavior, mobile
  layout, pixel density and user agent. It runs all waiting/retry cases, not
  just a screenshot. It is not an actual iPhone, and does not establish iOS
  background-tab suspension, device keyboard behavior, or Safari OS-version
  compatibility. Firefox intentionally uses a desktop preset because
  Playwright does not support Firefox's `isMobile` emulation.
- **Timing and aborts:** No engine-specific timing tolerances, retries, or skips
  are used. All engines assert exact fake elapsed durations, persistent error
  text and cleared feedback/watchdog timers. Stalled JSON assertions check the
  request's abort signal and failed timing entry as well as the rendered error.
  Native in-browser streams are deliberate: `route.fulfill` buffers an entire
  response and cannot test UI updates between SSE chunks. This tests client-side
  abort/body consumption, not a real socket's browser-specific disconnect text.

## Two-tab retirement checks

`tests/browser/retirement.spec.cjs` uses the same intercepted fixture and secure
synthetic origin, with two real pages in one browser context sharing localStorage.
Held GET and retirement POST replies are released explicitly; no sleeps or
provider/server/database calls are needed. The fixture records generation UUIDs
across both tabs and reloads, and rejects unmocked API requests.

Run just this scope in Chromium:

```sh
npm run test:browser -- tests/browser/retirement.spec.cjs --project=chromium --workers=3
```

The checks cover:

- Confirmation cancellation preserves the shared pending identity; confirmed
  retirement sends no generation, and reload/different sentences get a fresh UUID.
- Delayed retired/completed lookup replies, both on load and during submission,
  cannot clear or replace another tab's newer pending identity.
- Delayed retirement replies preserve that newer identity too.
- Save-wins retirement and delayed completed lookup recover the saved venture;
  reloading its result page still retrieves it without generating again.
- A lost retirement response retains the UUID across tabs and reload. Even a
  different sentence cannot generate until a lookup confirms the retirement.
- An interrupted partial preview exposes confirmation directly. Cancelling
  leaves the action reachable after returning home; accepting retains usable
  home navigation and permits only a separate fresh request.

These tests simulate terminal server states; they verify browser behavior, not
database locking or the backend's retirement/save serialization. They do not
exercise iPhone background suspension or a published-host timeout.

## Boundary

For real iPhone app switching, use the separate disposable
[Safari device check](ios-waiting-device-check.md). It reuses the synthetic
runtime with real clocks and memory-only storage. A physical iPhone, exact iOS
version and reviewed foreground/background evidence are required; the automated
device-harness smoke tests are not that sign-off.

These are browser integration checks, not a hosting proxy experiment. They do
not establish the published host's idle timeout or behavior when real upstream
connections disconnect. A future production check must read the deployment
instructions first and use disposable synthetic endpoints, not real ventures
or provider calls. The opt-in endpoint and real-clock runner are described in
[published-wait-probe.md](published-wait-probe.md). Published measurements require
temporary enablement and republishing; local checks are not production evidence.
