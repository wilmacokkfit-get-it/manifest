# MANIFEST Studio

The Studio adds an app-building workflow inside the existing MANIFEST product.
It does not replace venture generation, the Floor, the Mix, the Architect, owner
authentication, or provenance records. The existing dark palette and fonts remain.

## Flow

- **Scout:** an actual server-side OpenAI web-search call returns 3–5 close existing tools. Each has a one-line description, a product link, and search evidence. The server checks the evidence URL against the provider's returned citation metadata. Missing sources, malformed output, or provider failure produce a clear error, not invented matches.
- **Diverge:** a separate AI call proposes exactly three directions. Each includes a one-sentence pitch and a plain comparison with supplied existing tools. The user can choose one or keep the original idea. Optional non-negotiable requirements are saved with the project and passed to Diverge, Build, Autopsy and Mutate.
- **Build:** a real OpenAI Responses call returns a complete self-contained HTML app. The draft is saved before review, shown in an isolated preview and downloadable as HTML.
- **Autopsy:** a distinct second AI pass receives the actual build, critiques generic design, features and missing states, applies fixes, and returns the full app with a short fixes list. The UI describes this as static AI code review, not executed browser testing.
- **Mutate:** the server loads the selected current HTML and sends it with the change request. Only a valid full HTML response becomes a new version. Undo changes the saved current-version cursor. Selecting an earlier version and mutating creates a new branch; later versions on the old branch are discarded. The latest five versions, including the initial build and corrected build, are retained.

## Integration

`/studio` is accessible from the navigation. A creator's venture page has a
**Build an app from this** action, prefilled with that venture's idea and
description. The original venture is not modified. `/studio/<project UUID>`
reopens a saved app project. The Studio's **Your projects** list is private.

The module uses the existing OpenAI environment variables, FastAPI server,
creator cookie and optional verified app-owner account. It deliberately does not
trust a caller-supplied `X-Visitor-Id` as identity. Source and version history are
never added to the public Floor or venture payloads.

## Production rollout checklist

- [ ] **Migration ordering:** retain the existing MANIFEST schema and data. If the
  documented `supabase/migration.sql` v1–v3 schema is not already installed,
  apply its required legacy additions first. Then review and apply
  `supabase/app-studio.sql` to the **same Supabase project**. The Studio
  migration only adds the private `app_projects` table and index; it does not
  change existing rows. No migration was run against production for this work.
- [ ] **Environment:** set server-only `OPENAI_API_KEY`, `SUPABASE_URL`, and
  either `SUPABASE_SECRET_KEY` or legacy `SUPABASE_SERVICE_ROLE_KEY`. Optionally
  set `STUDIO_MODEL` to an OpenAI Responses model supporting web search;
  otherwise `LLM_MODEL`, then `gpt-5-mini`, is used. Never expose the Supabase
  privileged key or OpenAI key to the browser. `STUDIO_ALLOW_LOCAL=1` is only
  for disposable local tests and must not replace production Supabase.
- [ ] **Build/check:** this repository has no frontend compilation step; the
  Replit production command in `.replit` is
  `python -m uvicorn server:app --host 0.0.0.0 --port 5000`. Run
  `python -m compileall -q server.py app` and `npm test` before deployment.
- [ ] **Redeploy:** deploy the reviewed code through the existing Replit
  deployment workflow. This repository change does not deploy or change
  production settings.
- [ ] **Authenticated verification:** sign in using the existing MANIFEST
  authentication, open `/studio`, and verify live Scout sources, direction and
  original-idea selection, requirements, Build, Autopsy, Mutate, Undo, reload,
  and project reopening. Verify a different creator cannot list or read the
  project. Confirm Scout fails visibly if native search citations are absent.
- [ ] **Rollback:** redeploy the previous application revision if needed. The
  additive table can remain unused; rolling back the app does not require
  deleting it. Do not drop `app_projects` unless its private projects and HTML
  history have been exported and data loss is approved.

If the table or Supabase connection is unavailable, Studio fails clearly and
does **not** save to a local fallback. Existing MANIFEST pages remain usable.
The live app and its Supabase project were not changed by this code-only merge.

Scout requires native OpenAI web-search citation metadata. Some OpenAI-compatible
gateways return answer text while dropping `web_search_call.action.sources` and
message annotations. Such responses deliberately fail the evidence check; do not
disable the check or manufacture citation metadata. The Computer test gateway
exhibited this limitation, so a fully live Scout round trip through that gateway
could not be certified. Native response-shape acceptance is covered by fixtures.

For disposable local development only, explicitly set `STUDIO_ALLOW_LOCAL=1`
alongside the existing SQLite test configuration. This is workspace-local
storage, not durable hosted Supabase. Do not enable it on the production app as a
substitute for the selected Supabase deployment.

## Safety and operational behavior

- The iframe uses exactly `sandbox="allow-scripts"` without same-origin access.
- An early injected CSP blocks network access, child frames, external resources,
  form submissions and base URL changes. Remote dependencies and redirects are
  rejected before a document becomes a version.
- No crypto, wallets, autonomous spending or self-replication is added.
- No canned HTML app or mock provider is used by Studio. Test fixtures are only
  present in test files and disposable test databases.
- Every stage is saved on the server. Reopening checks saved work rather than
  starting a new billed call. Failed operations preserve the previous valid app.
- Atomic revision guards prevent two tabs or workers overwriting each other.
  A four-minute operation lease permits recovery after abandoned calls; an old
  call cannot commit over newer work.
- Existing Mix limits protect the new AI endpoints: six operations per hour,
  twelve per day per caller, and one hundred per process per day for non-owner
  visitors. These inherited in-process limits reset on process restart and are
  not a durable multi-worker billing ceiling. The configured verified app owner
  retains its existing bypass. Each workflow comprises multiple billed AI calls.
- App HTML/version history is persisted; transient data entered inside generated
  previews is not. This loop does not add databases or network integrations to
  each generated app.

## Verification

Run `python -m unittest discover -s tests -p 'test_studio.py' -v` and `npm test`.
Provider tests mock at the explicit provider boundary; they are not proof of a
live OpenAI call. API tests use disposable SQLite directories or mock Supabase
HTTP transports, never production data. Live-call and browser verification are
reported separately in the pull request.

### Verification recorded for this integration

- Python regression suite: 333 tests run, passing with two existing skips.
- Chromium regression suite: 58 tests passed, including four Studio browser
  scenarios covering the complete fixture flow, review/mutation failure and
  retry, history cap/branching, linked evidence, and responsive layout.
- PostgreSQL: the migration executed in an isolated local cluster. Repeat
  application, existing-data preservation, RLS/client-role privileges,
  revision guards, and the five-version database constraint passed.
- Live AI: Diverge returned three complete, selectable directions. A live
  response-length overrun was found and fixed with a concise prompt plus a
  generous validation margin. Separately, a complete timer was built, critiqued/fixed by the second call, and
  mutated to a new heading by a third call. Start/Pause/Reset were exercised.
  Undo, reload and reopening from the project library retained the expected
  HTML versions. These calls used explicitly labelled synthetic research
  fixtures in a disposable local database, not fabricated live Scout results.
- Live Scout: the managed test gateway omitted native citation metadata and was
  correctly rejected. Fully live native-OpenAI Scout and production Supabase
  read/write behavior still require the deployment smoke test.
- Visual inspection: desktop and 375px, including populated views. No horizontal
  overflow or browser page errors were observed. Firefox/WebKit were not signed
  off in this environment. Existing global design tokens were not changed.
