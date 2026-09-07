# MANIFEST v2

One sentence in. A venture out.

Type a sentence, watch the name appear inside the orb, then the tagline, the three ways to build it, the five-step plan, the honest grounding, and three product renders fading in while you read. Every venture is private by default, sealed with a SHA-256 provenance record, and only becomes public when its creator clicks Release. Once public, real people can leave one of three signals (I'd buy this / I'd build this / I'd fund this). Enough real signals and it earns the Mark and steps onto the Floor.

There are no fake numbers anywhere in this product. Every count you see is a row in the database that a real person created.

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
  supabase/
    migration.sql        Tables + storage bucket (already applied to your project)
  requirements.txt
  .env.example           Every setting, explained
```

Run command, everywhere: `uvicorn server:app --host 0.0.0.0 --port 8000`

---

## Set it up on Replit, click by click

You will need: an Anthropic API key (text), an OpenAI API key (renders), and your Supabase service role key. About 15 minutes.

### 1. Create the Repl

1. Go to replit.com and sign in.
2. Click **Create Repl**.
3. Pick the **Python** template. Name it `manifest`. Click **Create Repl**.

### 2. Upload the code

1. Unzip `manifest-v2.zip` on your computer.
2. In the Replit file panel (left side), click the three dots next to the folder name, then **Upload folder**.
3. Upload the whole `manifest` folder's contents so `server.py` sits at the top level of the Repl (not inside a `manifest/` subfolder). If it lands in a subfolder, drag the files up one level.
4. Delete Replit's default `main.py` if it is there.

### 3. Tell Replit how to run it

1. Click the `.replit` file (turn on "Show hidden files" from the three-dot menu if you do not see it).
2. Replace the `run` line with:
   ```
   run = "uvicorn server:app --host 0.0.0.0 --port 8000"
   ```
3. Open the **Shell** tab and run:
   ```
   pip install -r requirements.txt
   ```

### 4. Add your secrets

Click the padlock icon (**Secrets**) in the left toolbar and add these one at a time. Copy the names exactly.

| Key | Value | Where to get it |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | your key | console.anthropic.com → Settings → API Keys → Create Key |
| `LLM_PROVIDER` | `anthropic` | |
| `LLM_MODEL` | `claude-sonnet-4-5` | |
| `OPENAI_API_KEY` | your key | platform.openai.com → API keys → Create new secret key |
| `IMAGE_PROVIDER` | `openai` | (set to `none` to skip renders and save money while testing) |
| `IMAGE_MODEL` | `gpt-image-1` | |
| `SUPABASE_URL` | `https://lvtehfciwiiakpdtwlyx.supabase.co` | |
| `SUPABASE_SERVICE_ROLE_KEY` | your key | supabase.com → your **Manifest** project → **Project Settings** (gear) → **API Keys** → the **service_role** key (click Reveal). Never put this in the front end. |
| `PUBLIC_URL` | `https://manifest-yourname.replit.app` | Your Repl's public address. Replit shows it after the first deploy. Update it once you know it. |

Optional knobs (defaults in parentheses): `GEN_PER_HOUR` (3), `GEN_PER_DAY` (8), `GLOBAL_PER_DAY` (150), `MARK_THRESHOLD_SIGNALS` (25), `MARK_THRESHOLD_BUILD_FUND` (5).

Leave `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` blank if you just want to test with the built-in SQLite file first.

### 5. Run it

1. Click **Run**. The Webview should show the orb.
2. Type a sentence and click **Manifest it**. The name should appear in the orb within a few seconds; the full page in about half a minute; renders trail in after that.
3. Open `/api/health` in the Webview to confirm `"store": "supabase"` once your keys are in.

### 6. Deploy so the world can see it

1. Click **Deploy** (top right) → **Autoscale** (or Reserved VM if you want it always warm).
2. Run command: `uvicorn server:app --host 0.0.0.0 --port 8000`
3. Deploy. Copy the `.replit.app` URL it gives you and paste it into the `PUBLIC_URL` secret. Redeploy once so share cards use the right address.

---

## Supabase

The three tables (`ventures`, `signals`, `waitlist`) and the public `renders` storage bucket are already created in your Manifest project. Row Level Security is on and there are no anon policies, so only the server (using the service role key) can read or write. `supabase/migration.sql` is the exact SQL, kept for reference or for a second environment.

---

## How the pieces work

**Streaming reveal.** `POST /api/manifest` opens a server-sent-event stream. The model is asked to write one JSON object; `partial_json.py` parses the incomplete stream on every chunk so the front end can show the name the moment it is written, then the tagline, then each tier and step as they land. Renders are kicked off in the background and the page polls until three are in.

**Private by default.** Every venture is born `private`. The creator is identified by an HttpOnly cookie the server sets on first visit (nothing is stored in the page or in localStorage). Only the creator can see a private venture or click Release.

**Provenance.** At creation the server hashes the exact idea text, the UTC second, and the creator's key with SHA-256 and derives a short `MNF-XXXX-XXXX-XXXX` code. It proves this text existed at this moment. It is not a patent, trademark, or ownership claim, and the copy says so. If you want an external anchor later, drop the hash into opentimestamps.org and store the `.ots` proof next to the venture.

**Signals.** Three buttons, each needs a real email, one per email per type per venture (enforced by a unique constraint). "I'd buy this" can carry an optional price. Counts are cached on the venture row. When `total >= MARK_THRESHOLD_SIGNALS` or `build + fund >= MARK_THRESHOLD_BUILD_FUND`, the venture gets `promoted_at` and moves to the Marked shelf on the Floor.

**Rate limits.** Per visitor: `GEN_PER_HOUR` and `GEN_PER_DAY`. Whole site: `GLOBAL_PER_DAY`. A hit only counts once a venture is actually created, so a failed generation is free.

**Share pages.** `/v/<slug>` is served by the server with real Open Graph tags (title, description, first render) so links unfurl properly in iMessage, X, Slack. This only works on your own deployment; the Perplexity preview uses `?v=<slug>` links instead.

---

## Honesty rules baked in

- No demo signals, no seeded demand, no fake counts. The Floor starts empty and fills only with real ventures and real signals.
- Every venture page says "Written by the engine, not verified."
- Provenance fine print states plainly what it is and is not.
- Rate limits and a global cap protect your API bill by default.

---

## Local run (Mac or Windows)

```
pip install -r requirements.txt
cp .env.example .env      # fill in your keys
uvicorn server:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000.
