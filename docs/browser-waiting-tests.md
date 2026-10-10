# Browser waiting and disconnect checks

Run `npm ci` and `npm run test:browser`. The Replit configuration includes
system Chromium; elsewhere run `npx playwright install chromium` first.
`PLAYWRIGHT_CHROMIUM_EXECUTABLE` can select another Chromium executable.

The suite loads the actual `public/index.html`, `app.js`, CSS and owner-auth
script in Chromium. It intercepts all networking and never imports the Python
server, connects to Supabase, invokes a provider, creates ventures, or renders
images. Each test gets a fresh browser context and local storage.

Browser-native Response and ReadableStream objects supply synthetic incremental
SSE and delayed JSON. Playwright's fake clock advances long waits and watchdog
deadlines without actually waiting several minutes. Its default Performance
stub would discard User Timing entries, so the harness retains Chromium's native
measure/getEntries methods while using the fake numeric start/end times.

Checks include:

- Elapsed and “still working” feedback before text, and “writing” feedback after
  partial SSE text, for generation and path selection.
- JSON refinement waiting, preservation of the first result and context on
  failure, and enabled retry controls.
- Persistent early-EOF and provider-error messages after transient toasts expire.
- Client watchdog aborts, including a stalled refinement JSON body.
- Real first-visible-text and completion Performance entries and no duplicate
  first-text entries. JSON refinement intentionally has no first-text entry.
- Navigation before/after partial text aborts requests, clears feedback/deadline
  timers, and prevents old callbacks from changing the new page.
- Navigation while a disconnected manifest is reconciling ignores a late saved
  result instead of reopening the abandoned venture.

Run the deterministic helper/recovery checks separately with `npm test`.
Failure traces are written to ignored `generated-artifacts/browser-results/`.

## Boundary

These are browser integration checks, not a hosting proxy experiment. They do
not establish the published host's idle timeout or behavior when real upstream
connections disconnect. A future production check must read the deployment
instructions first and use disposable synthetic endpoints, not real ventures
or provider calls. The opt-in endpoint and real-clock runner are described in
[published-wait-probe.md](published-wait-probe.md). Published measurements require
temporary enablement and republishing; local checks are not production evidence.
