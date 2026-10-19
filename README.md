# MANIFEST v2

One sentence in. A venture out.

Type a sentence, watch the name appear inside the orb, then the tagline, the three ways to build it, the five-step plan, the honest grounding, and two product renders fading in while you read. Every venture is private by default, sealed with a SHA-256 provenance record, and only becomes public when its creator clicks Release. Once public, real people can leave one of three signals (I'd buy this / I'd build this / I'd fund this). Enough real signals and it earns the Mark and steps onto the Floor.

Then the creator picks a path. One of the three ways to build it becomes a step-by-step guide written by the engine: what to do, why, suggested suppliers to call (labeled as suggestions, not vetted), rough cost and time, and what "done" looks like. The creator checks steps off, keeps notes, and when stuck posts an ask to the Floor. Real people raise a hand with a real email. Marketplace and Shark Tank in one page.

Then the Architect. Every creator sees only their own page. The Architect reads the whole Floor at once and writes readings over real rows: whose paths call for the same shop (pool the order), whose buyers overlap (share the interviews), which open asks one supplier could fill together, where real signals are landing, what is stalled. And it crossbreeds: it splices two real ventures, or a venture with a field that has nothing to do with it (vending machines, beehives, ski lifts), steals one specific mechanism, and shows the hybrid that should not work but does. One tap turns a hybrid into its own venture, sealed to whoever manifests it.

There are no fake numbers anywhere in this product. Every count you see is a row in the database that a real person created.

---

## App-building Studio

The **Studio** integrates Scout → Diverge → Build → Autopsy → Mutate into
MANIFEST, with private project reopening, sandboxed working previews and five
retained HTML versions. It reuses the existing server-side OpenAI configuration,
creator identity and Supabase connection. Before enabling it in production,
apply the additive `supabase/app-studio.sql` migration. See
[the Studio integration and rollout guide](docs/app-studio.md).

---

## What is in the box

```
manifest/
  server.py              FastAPI app: streaming generation, ventures, signals, Floor, waitlist, HTML routes
  app/
    llm.py               Text (Anthropic or OpenAI) and image (OpenAI Images) providers, streaming
    partial_json.py      Turns a half-written JSON stream into a usable object, live
    provenance.py        SHA-256 record + human-readable MNF code
    store.py             Two storage backends: SQLite file (default) or Supabase
    generate_image.py    Render pipeline (compresses to WebP)
  public/
    index.html           Page shell (nav, landing, placeholders the server fills in)
    app.js               The whole front end. Vanilla JS, no build step.
    styles.css           Design system: near-black, Playfair Display + Inter, breathing orb
    favicon.svg, og-default.png
    architect.py         The Architect: facts from rows, readings + crossbreeds over them
  supabase/
    migration.sql        Tables + storage bucket (v1 applied; v2 and v3 below, run once)
  requirements.txt
  .env.example           Every setting, explained
```

Run command, everywhere: `uvicorn server:app --host 0.0.0.0 --port 8000`

---

## Set it up on Replit, click by click

You need one OpenAI API key. About 10 minutes. Everything runs on your key and your Replit account; nothing else bills you.

### 1. Create the Repl

1. Go to replit.com and sign in.
2. Click **Create Repl** (or **+ Create**).
3. Pick the **Python** template. Name it `manifest`. Click **Create Repl**.

### 2. Upload the code

1. Unzip `manifest-v2.zip` on your computer. You get a folder named `manifest`.
2. In the Replit file panel (left side), click the three dots next to the top folder, then **Upload folder**. Pick the `manifest` folder.
3. If the files landed inside a `manifest/` subfolder, select them all and drag them up one level so `server.py` sits at the top of the Repl.
4. Delete Replit's default `main.py` if it is there.
5. The zip includes a `.replit` file with the run command already set, so Replit knows how to start it. If Replit asks whether to overwrite `.replit`, say yes.

### 3. Install packages

Open the **Shell** tab (bottom) and run:
```
pip install -r requirements.txt
```

### 4. Add your secrets

Click the padlock icon (**Secrets**) in the left toolbar. Add these one at a time, key on the left, value on the right. Copy the names exactly.

| Key | Value | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | your key | platform.openai.com → API keys → Create new secret key. Put $10 of credit on the account. |
| `LLM_PROVIDER` | `openai` | |
| `LLM_MODEL` | `gpt-5-mini` | writes the ventures and paths, about half a cent each |
| `IMAGE_PROVIDER` | `openai` | set to `none` to skip renders entirely |
| `IMAGE_MODEL` | `gpt-image-1` | |
| `IMAGE_QUALITY` | `medium` | `low` is 1 cent a render, `medium` 4 cents |
| `RENDER_COUNT` | `2` | renders per venture, 0 to 3 |
| `PUBLIC_URL` | `https://manifest-yourname.replit.app` | your Repl's public address; fill it in after step 6 |

Spend knobs (optional, defaults shown): `GEN_PER_HOUR` 3, `GEN_PER_DAY` 8, `GLOBAL_PER_DAY` 150. At two medium renders a venture costs about 9 cents, so 150 a day is a $14 worst case. Set `GLOBAL_PER_DAY` to `50` for a $5 ceiling.

Want Claude writing the text instead? Add `ANTHROPIC_API_KEY`, set `LLM_PROVIDER` to `anthropic` and `LLM_MODEL` to `claude-sonnet-4-5`. Renders still use OpenAI.

### 5. Run it

1. Click **Run**. The Webview shows the orb.
2. Type a sentence and hit **Manifest it**. Name in a few seconds, full page in about half a minute, two renders trail in after.
3. Open `/api/health` in the Webview to see the store and provider in use.

### 6. Deploy so people can use it

1. Click **Deploy** (top right) → **Autoscale**. Autoscale sleeps when nobody is there, so it is the cheap option.
2. Leave the run command as `uvicorn server:app --host 0.0.0.0 --port 8000`. Deploy.
3. Copy the `.replit.app` address it gives you, paste it into the `PUBLIC_URL` secret, and click **Redeploy** once so share links and link-preview cards use the right address.
4. Send the link. Ventures, the Floor, signals, paths, asks and offers all work. The Floor starts empty, and the first venture on it will be real.

**About the database.** By default everything is saved in a SQLite file inside the Repl (`data/manifest.db`). That survives restarts and redeploys on Replit as long as you do not delete the folder, and it is fine for hundreds of ventures. When you want the durable option, add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as secrets and run the v2 migration described below.

---

## Supabase

The schema creates the venture tables and a private `renders` storage bucket. Row Level Security is on and there are no anon policies, so only the server (using its backend secret key) can read or write. Images are delivered through the app: private images require the creator's cookie; released images remain visible to everyone.

At startup the Supabase backend makes its configured renders bucket private and verifies the setting, failing startup if that cannot be done. Re-running `supabase/migration.sql` also makes the default bucket private. Neither action moves or deletes objects or rewrites venture records. Existing stored public image references are translated to app URLs when displayed. Do not add browser-readable Storage policies for this bucket. Copies previously downloaded or cached while the bucket was public cannot be recalled; disabling public access prevents new origin downloads but may not immediately purge old CDN caches.

**v2 and v3 need one run.** The Path layer adds a `path` column on `ventures` and two tables, `needs` and `offers`; the Architect adds a `splices` column and an `architect` cache table. The Supabase connection timed out when I tried to apply it, so do this once, about a minute:

1. Open your Supabase project, click **SQL Editor** in the left bar, then **New query**.
2. Open `supabase/migration.sql`, copy everything from the line `-- v2: the Path` to the end (v2 and v3), paste it in, click **Run**.
3. You should see "Success. No rows returned." Done. (Every statement is `if not exists`, so running it twice is harmless.)

If you skip this and use `STORE=supabase`, choosing a path will fail with a column error. SQLite mode needs nothing; it adds the column and tables itself.

---

## How the pieces work

**Streaming reveal.** `POST /api/manifest` opens a server-sent-event stream. The model is asked to write one JSON object; `partial_json.py` parses the incomplete stream on every chunk so the front end can show the name the moment it is written, then the tagline, then each tier and step as they land. Renders are kicked off in the background and the page polls until three are in.

**Private by default.** Every venture is born `private`. The creator is identified by an HttpOnly cookie the server sets on first visit (nothing is stored in the page or in localStorage). Only the creator can see a private venture or click Release.

**Provenance.** At creation the server hashes the exact idea text, the UTC second, and the creator's key with SHA-256 and derives a short `MNF-XXXX-XXXX-XXXX` code. It proves this text existed at this moment. It is not a patent, trademark, or ownership claim, and the copy says so. If you want an external anchor later, drop the hash into opentimestamps.org and store the `.ots` proof next to the venture.

**Signals.** Three buttons, each needs a real email, one per email per type per venture (enforced by a unique constraint). "I'd buy this" can carry an optional price. Counts are cached on the venture row. When `total >= MARK_THRESHOLD_SIGNALS` or `build + fund >= MARK_THRESHOLD_BUILD_FUND`, the venture gets `promoted_at` and moves to the Marked shelf on the Floor.

**The Path.** `POST /api/ventures/<slug>/path` with `{tier: 0|1|2}` streams a 5-7 step guide the same way the venture streams; the front end shows the first move and each step title as it lands. Steps live in the `path` JSON on the venture: `title, why, how[], suppliers[{kind, examples[], search}], cost, time, done_when, status, note`. The owner PATCHes `/path/steps/<n>` with `{status}` or `{note}`. Re-choosing is blocked (409) once any step has moved; the owner has to Reset first (DELETE `/path`). Supplier names are the model's suggestions; the UI labels them "Suggested by the engine, not vetted" and every one is just a Google search link. Notes are owner-only and stripped from public views.

**The Architect.** `GET /api/architect` computes facts in Python from real rows (`app/architect.py`: supplier families across paths, buyer-world overlaps, rhyming asks, signal echoes, stalls, gaps), hashes them, and only calls the model when the rows changed (and at most every `ARCHITECT_MIN_INTERVAL` seconds, default 300). The model writes 3-6 readings and 2-3 crossbreeds over those facts; anything it names that is not a real venture or ask is dropped. Readings are cached in the `architect` table. Thin Floor (fewer than two ventures, no asks) means no model call and an honest "nothing to see yet." Venture pages show the readings that mention them.

**Crossbreed.** `POST /api/ventures/<slug>/crossbreed` with `{with_slug}` (another public venture), `{lateral: true}` (a random field from `LATERAL_FIELDS`), or `{}` (the Architect picks the strongest adjacency). Returns three splices, each with what was stolen, the hybrid, why it should not work, why it does, a first test with a number, and a one-sentence prompt. Owner-only, `SPLICE_PER_DAY` (6) runs per venture per day, saved on the venture with history. "Manifest this" drops the sentence into the orb.

**Asks and offers.** The owner posts an ask on a step (`POST /needs`, max 12 open per venture). If the venture is public it shows on the Floor under "Open asks." Anyone can raise a hand (`POST /needs/<id>/offers`) with a real email, one per email per ask. Only the owner sees the emails and messages, and can mark the ask filled. Offer counts are real rows, same rule as signals.

**No repeats.** Every generation gets a random angle (physical product, pro tool, service business, subscription, marketplace, and so on) and a list of this creator's recent names and taglines to avoid. The same sentence twice gives two clearly different ventures.

**Rate limits.** Per visitor: `GEN_PER_HOUR` and `GEN_PER_DAY`. Whole site: `GLOBAL_PER_DAY`. A hit only counts once a venture is actually created, so a failed generation is free.

**Share pages.** `/v/<slug>` is served by the server with real Open Graph tags (title, description, first render) so links unfurl properly in iMessage, X, Slack. This only works on your own deployment; the Perplexity preview uses `?v=<slug>` links instead.

---

## Honesty rules baked in

- No demo signals, no seeded demand, no fake counts. The Floor starts empty and fills only with real ventures and real signals.
- Every venture page says "Written by the engine, not verified."
- Provenance fine print states plainly what it is and is not.
- Rate limits and a global cap protect your API bill by default.
- Suppliers in a path are suggestions from the model, labeled that way, never presented as vetted or partnered.
- No fake asks or offers. Every "hand raised" is a real email in the `offers` table.

---

## Local run (Mac or Windows)

For the opt-in, disposable live Supabase image-privacy check, see
[the integration check guide](docs/supabase-image-privacy.md). Ordinary tests
do not contact real Supabase Storage.

For the manual, reviewed cleanup of an abandoned render attempt, see the
[render-attempt cleanup operator guide](docs/render-attempt-cleanup.md).

Run regression tests with `python -m unittest discover -s tests -v`.
The storage-policy audit tests require PostgreSQL server/client tools (`initdb`,
`pg_ctl`, and `psql`) on PATH; Replit's system dependencies include PostgreSQL.
They create a fresh cluster under `/tmp`, use a private Unix socket with TCP
disabled, ignore inherited connection settings and credentials, and stop and
remove the cluster afterward. Missing tools fail explicitly rather than skipping
these checks. No existing local database or Supabase project is accessed.
Run just those tests with
`python -m unittest discover -s tests -p 'test_storage_policy_audit*.py' -v`.
The audit reports metadata for operator review; these checks do not certify
arbitrary policy expressions as safe.

```
pip install -r requirements.txt
cp .env.example .env      # fill in your keys
uvicorn server:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000.
