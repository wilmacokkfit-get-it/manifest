/* MANIFEST — client
   Vanilla JS, no build step. Every piece of text is inserted with textContent,
   never innerHTML, so nothing a model or a stranger types can run in the page. */
(() => {
  'use strict';

  // ---------- environment ----------
  const SERVED = !!window.__MANIFEST_SERVED__;                 // HTML came from server.py (clean URLs)
  const API = '__PORT_8000__'.startsWith('__') ? '' : '__PORT_8000__'; // proxy prefix when deployed as a preview
  const $ = (s, r = document) => r.querySelector(s);

  // ---------- identity ----------
  // The creator key never lives in the page. The server sets an HttpOnly cookie
  // (or the hosting proxy supplies a visitor id), so nothing here can be forged or leaked.

  // ---------- tiny DOM builder ----------
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'text') el.textContent = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const svgNS = 'http://www.w3.org/2000/svg';
  function svg(markup) { const t = document.createElement('template'); t.innerHTML = markup.trim(); return t.content.firstChild; }

  // ---------- api ----------
  async function api(path, opts = {}) {
    const res = await fetch(API + path, {
      ...opts,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    });
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error((data && data.error) || 'Something went wrong.');
    return data;
  }
  const assetUrl = (u) => (u && u.startsWith('/') ? API + u : u);

  // ---------- routing ----------
  function href(route) {
    if (SERVED) return route;
    if (route === '/') return location.pathname;
    if (route.startsWith('/v/')) return `${location.pathname}?v=${encodeURIComponent(route.slice(3))}`;
    return `${location.pathname}?p=${encodeURIComponent(route.slice(1))}`;
  }
  function currentRoute() {
    if (SERVED) return location.pathname.replace(/\/$/, '') || '/';
    const q = new URLSearchParams(location.search);
    if (q.get('v')) return `/v/${q.get('v')}`;
    if (q.get('p')) return `/${q.get('p')}`;
    return '/';
  }
  function go(route, { replace = false } = {}) {
    const url = href(route);
    if (replace) history.replaceState({}, '', url); else history.pushState({}, '', url);
    render();
  }
  function shareUrl(slug) {
    return location.origin + href(`/v/${slug}`);
  }
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-route]');
    if (!a) return;
    e.preventDefault();
    const route = a.dataset.route;
    if (route === '/' && currentRoute() === '/') { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
    go(route);
  });
  window.addEventListener('popstate', render);

  const app = $('#app');
  const landing = $('#view-landing');
  let activeView = null;   // non-landing view element

  function mount(el, route) {
    if (activeView) { if (typeof activeView.cleanup === 'function') activeView.cleanup(); activeView.remove(); }
    activeView = el;
    if (el) { landing.classList.add('hidden'); app.append(el); }
    else landing.classList.remove('hidden');
    document.querySelectorAll('.nav-links a').forEach(a => {
      if (a.dataset.route === route) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    window.scrollTo(0, 0);
  }

  function render() {
    const route = currentRoute();
    setAccent(null);
    if (route === '/') { mount(null, '/'); resetHero(); checkReveals(); return; }
    if (route.startsWith('/v/')) { mount(VentureView({ slug: route.slice(3) }), null); return; }
    if (route === '/marketplace') { mount(FloorView(), '/marketplace'); return; }
    if (route === '/waitlist') { mount(WaitlistView(), '/waitlist'); return; }
    if (route === '/about') { mount(AboutView(), null); return; }
    mount(NotFound('No page lives at this address.'), null);
  }

  // ---------- accent ----------
  function setAccent(hex) {
    document.documentElement.style.setProperty('--accent', hex || '#e8eeff');
  }

  // ---------- toast ----------
  let toastTimer;
  function toast(msg) {
    const t = $('#toast'); t.textContent = msg; t.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
  }

  // ---------- landing ----------
  const ideaForm = $('#idea-form'), ideaInput = $('#idea'), orbWrap = $('#orb-wrap');
  const formingName = $('#forming-name'), formingStatus = $('#forming-status'), heroError = $('#hero-error'), heroTag = $('#hero-tag');
  const phrases = ['Reaching into the idea space…', 'Finding the shape of it…', 'Naming what does not exist yet…', 'Drafting the launch path…', 'Sealing the record…'];

  ideaInput.addEventListener('input', () => { ideaInput.style.height = 'auto'; ideaInput.style.height = ideaInput.scrollHeight + 'px'; });
  ideaInput.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ideaForm.requestSubmit(); } });
  $('#cta-top').addEventListener('click', (e) => { e.preventDefault(); window.scrollTo({ top: 0, behavior: 'smooth' }); setTimeout(() => ideaInput.focus(), 500); });

  function resetHero() {
    orbWrap.classList.remove('forming'); formingName.textContent = ''; formingStatus.textContent = '';
    heroError.textContent = ''; heroTag.style.opacity = '';
  }

  ideaForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const idea = ideaInput.value.trim();
    if (idea.length < 8) { heroError.textContent = 'Give it a full sentence.'; return; }
    heroError.textContent = '';
    startManifest(idea);
  });

  function checkReveals() {
    document.querySelectorAll('.reveal:not(.in)').forEach(el => {
      if (el.getBoundingClientRect().top < innerHeight * 0.88) el.classList.add('in');
    });
  }
  addEventListener('scroll', checkReveals, { passive: true });
  addEventListener('resize', checkReveals);

  // ---------- the reveal ----------
  async function startManifest(idea) {
    orbWrap.classList.add('forming');
    heroTag.style.opacity = '0';
    let pi = 0; formingStatus.textContent = phrases[0];
    const phraseTimer = setInterval(() => { pi = (pi + 1) % phrases.length; formingStatus.textContent = phrases[pi]; }, 2200);

    let view = null;           // VentureView once we move to it
    let moved = false;
    let meta = null;

    const finish = () => clearInterval(phraseTimer);

    try {
      const res = await fetch(API + '/api/manifest', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idea }),
      });
      if (!res.ok || !res.body) {
        let msg = 'The engine is unavailable right now.';
        try { msg = (await res.json()).error || msg; } catch (_) {}
        throw new Error(msg);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      let done = false;
      while (!done) {
        const { value, done: rd } = await reader.read();
        if (rd) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const ev = parseSSE(raw);
          if (!ev) continue;
          if (ev.event === 'meta') meta = ev.data;
          else if (ev.event === 'partial') {
            const { data, streaming } = ev.data;
            if (!moved) {
              formingName.textContent = data.name || '';
              formingName.classList.toggle('caret', streaming === 'name');
              if (data.name && streaming && streaming !== 'name') {
                // name is complete → move to the page
                moved = true; finish();
                view = VentureView({ live: { meta, data, streaming, idea } });
                mount(view, null);
              }
            } else view.update(data, streaming);
          } else if (ev.event === 'done') {
            done = true; finish();
            if (!moved) { view = VentureView({ live: { meta, data: ev.data, streaming: null, idea } }); mount(view, null); }
            view.complete(ev.data);
            history.replaceState({}, '', href(`/v/${ev.data.slug}`));
          } else if (ev.event === 'error') {
            throw new Error(ev.data.message || 'The engine stalled.');
          }
        }
      }
    } catch (err) {
      finish();
      if (moved) { toast(err.message); }
      else { orbWrap.classList.remove('forming'); formingStatus.textContent = ''; heroTag.style.opacity = ''; heroError.textContent = err.message; }
    }
  }

  function parseSSE(block) {
    let event = 'message', data = '';
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data += line.slice(5).trim();
    }
    if (!data) return null;
    try { return { event, data: JSON.parse(data) }; } catch (_) { return null; }
  }

  // ---------- venture view ----------
  function VentureView({ slug, live }) {
    const root = h('section', { class: 'view venture' });
    const halo = h('div', { class: 'halo', 'aria-hidden': 'true' });

    // header
    const prov = h('div', { class: 'v-prov' });
    const name = h('h1', { class: 'v-name' });
    const tagline = h('p', { class: 'v-tagline' });
    const desc = h('p', { class: 'v-desc' });
    const cat = h('div', { class: 'v-cat' });
    const head = h('header', { class: 'v-head' }, prov, name, tagline, desc, cat);
    const refinement = h('section', { class: 'refinement hidden', 'aria-label': 'One optional refinement' });
    let refinementDraft = '', refinementState = null, refinementBusy = false;

    // renders
    const renderSlots = [];
    const renderGrid = h('div', { class: 'render-grid' });
    const renderStatus = h('p', { class: 'render-status', text: 'Rendering the product…' });
    const renders = h('div', { class: 'renders hidden' }, h('h2', { class: 'v-label', text: 'Product renders' }), renderGrid, renderStatus);
    function ensureSlots(n) {
      n = Math.max(1, Math.min(3, n || 2));
      while (renderSlots.length < n) { const s = h('div', { class: 'render' }, h('div', { class: 'shimmer' }), h('img', { alt: '' })); renderSlots.push(s); renderGrid.append(s); }
      renderGrid.classList.toggle('two', renderSlots.length === 2);
    }

    // tiers
    const tierEls = [];
    const tiers = h('div', { class: 'tiers hidden' });

    // roadmap
    const road = h('div', { class: 'road' });
    const roadmap = h('div', { class: 'roadmap hidden' }, h('h2', { class: 'v-label', text: 'Five steps to launch' }), road);

    // the path (chosen tier, walked step by step)
    const path = h('div', { class: 'path hidden' });

    // grounding
    const gEls = {};
    const groundGrid = h('div', { class: 'ground-grid' },
      ...[['exists', 'What already exists'], ['who_pays', 'Who actually pays'], ['first_hundred', 'The first hundred dollars']].map(([k, label]) => {
        gEls[k] = h('p'); return h('div', { class: 'ground' }, h('b', { text: label }), gEls[k]);
      }));
    const grounding = h('div', { class: 'grounding hidden' }, h('h2', { class: 'v-label', text: 'The honest read' }), groundGrid,
      h('p', { class: 'ground-note', text: 'Written by the engine, not verified. Treat it as a sharp friend’s first take.' }));

    // seal
    const sealCode = h('div', { class: 'code' }), sealWhen = h('div', { class: 'when' }), sealHash = h('div', { class: 'hash' });
    const seal = h('div', { class: 'seal hidden' },
      svg(`<svg class="seal-mark" viewBox="0 0 54 54" fill="none" aria-hidden="true"><circle cx="27" cy="27" r="25" stroke="currentColor" stroke-width="1"/><circle cx="27" cy="27" r="17" stroke="currentColor" stroke-width=".8" opacity=".5"/><circle cx="27" cy="27" r="4" fill="currentColor"/><path d="M27 2v8M27 44v8M2 27h8M44 27h8" stroke="currentColor" stroke-width="1" stroke-linecap="round" opacity=".7"/></svg>`),
      h('div', { class: 'v-label', text: 'Provenance record', style: { marginBottom: '14px' } }),
      sealCode, sealWhen, sealHash,
      h('p', { class: 'fine', text: 'A SHA-256 hash of the exact words, the exact second, and the creator’s key. It proves this existed, here, first. It is not a patent or a trademark, and MANIFEST never takes ownership of what you make.' }));

    // owner panel
    const owner = h('div', { class: 'owner hidden' });
    // signals
    const signals = h('div', { class: 'signals hidden' });
    // actions
    const actions = h('div', { class: 'v-actions hidden' });

    root.append(halo, head, refinement, renders, tiers, path, roadmap, grounding, seal, owner, signals, actions);

    let venture = null;   // full record once complete/loaded
    let pollTimer = null, pollStart = 0;
    const born = new Set();
    const show = (el) => { if (!el.classList.contains('hidden')) return; el.classList.remove('hidden'); el.classList.add('born'); };
    const setText = (el, txt, streaming) => { el.textContent = txt || ''; el.classList.toggle('caret', !!streaming); };

    // ---- progressive update while the model writes ----
    function update(d, streaming) {
      if (d.accent && /^#[0-9a-fA-F]{6}$/.test(d.accent)) setAccent(d.accent);
      setText(name, d.name, false);
      if (d.tagline != null) setText(tagline, d.tagline, streaming === 'tagline');
      if (d.description != null) setText(desc, d.description, streaming === 'description');
      if (d.category && streaming !== 'category') cat.textContent = d.category;
      if (Array.isArray(d.tiers) && d.tiers.length) {
        show(tiers);
        d.tiers.forEach((t, i) => {
          if (!tierEls[i]) {
            tierEls[i] = { root: h('div', { class: 'tier born' }, h('div', { class: 'tl' }), h('h3', { class: 'tt' }), h('p', { class: 'td' })) };
            tiers.append(tierEls[i].root);
          }
          const r = tierEls[i].root;
          r.querySelector('.tl').textContent = t.label || ['The Lean Build', 'The Real Product', 'The Empire'][i];
          const last = streaming === 'tiers' && i === d.tiers.length - 1;
          setText(r.querySelector('.tt'), t.title, last && t.description == null);
          setText(r.querySelector('.td'), t.description, last && t.description != null);
        });
      }
      if (Array.isArray(d.roadmap) && d.roadmap.length) {
        show(roadmap);
        d.roadmap.forEach((s, i) => {
          let el = road.children[i];
          if (!el) {
            el = h('div', { class: 'road-step born' },
              h('div', { class: 'road-head' }, h('div', { class: 'road-num', text: String(i + 1).padStart(2, '0') }), h('div', { class: 'road-action' }), h('div', { class: 'road-caret', text: '⌄' })),
              h('div', { class: 'road-where' }));
            el.addEventListener('click', () => el.classList.toggle('open'));
            road.append(el);
          }
          const last = streaming === 'roadmap' && i === d.roadmap.length - 1;
          setText(el.querySelector('.road-action'), s.action, last && s.where == null);
          el.querySelector('.road-where').textContent = s.where || '';
        });
      }
      if (d.grounding && typeof d.grounding === 'object') {
        show(grounding);
        const keys = ['exists', 'who_pays', 'first_hundred'];
        keys.forEach((k, i) => {
          if (d.grounding[k] == null) return;
          const isLast = streaming === 'grounding' && keys.slice(i + 1).every(kk => d.grounding[kk] == null);
          setText(gEls[k], d.grounding[k], isLast);
        });
      }
      if (d.image_prompts && !streaming) { ensureSlots(Array.isArray(d.image_prompts) ? d.image_prompts.length : 2); show(renders); }
    }

    // ---- full record (after done, or loaded from the API) ----
    function complete(v) {
      const newlyRefined = venture && venture.refinement?.state !== 'completed' && v.refinement?.state === 'completed';
      if (newlyRefined) {
        stopPolling();
        renderSlots.forEach(slot => {
          slot.classList.remove('done');
          const img = slot.querySelector('img');
          img.classList.remove('on');
          img.removeAttribute('src');
        });
        renderStatus.classList.remove('hidden');
      }
      venture = v;
      const slotCount = v.render_count || (v.images || []).length || 2;
      while (renderSlots.length > slotCount) renderSlots.pop().remove();
      ensureSlots(slotCount);
      while (road.children.length > (v.roadmap || []).length) road.lastChild.remove();
      update({ ...v, image_prompts: true }, null);
      [tagline, desc, ...Object.values(gEls)].forEach(el => el.classList.remove('caret'));
      root.querySelectorAll('.caret').forEach(el => el.classList.remove('caret'));
      prov.replaceChildren('Provenance ', h('b', { text: v.provenance.code }), h('span', { class: 'v-prov-when' }, ' · minted ', fmtDate(v.provenance.minted_at)));
      sealCode.textContent = v.provenance.code;
      sealWhen.textContent = `Minted ${fmtDate(v.provenance.minted_at, true)}`;
      sealHash.textContent = v.provenance.hash;
      show(seal);
      if (v.images_status === 'failed' && !(v.images || []).some(Boolean)) renders.classList.add('hidden');
      else { show(renders); applyImages(v); }
      renderRefinement(v);
      renderOwner(v);
      renderSignals(v);
      renderActions(v);
      renderTierPicks(v);
      renderPath(v);
      if (v.images_status === 'pending' || v.refinement?.state === 'pending') startPolling();
      else stopPolling();
    }

    function renderRefinement(v) {
      const state = v.is_owner ? v.refinement?.state : null;
      const busy = refinementBusy || state === 'pending';
      root.querySelectorAll('button').forEach(button => {
        if (refinement.contains(button)) return;
        if (busy && !button.hasAttribute('data-refinement-disabled')) {
          button.setAttribute('data-refinement-disabled', String(button.disabled));
          button.disabled = true;
        } else if (!busy && button.hasAttribute('data-refinement-disabled')) {
          button.disabled = button.getAttribute('data-refinement-disabled') === 'true';
          button.removeAttribute('data-refinement-disabled');
        }
      });
      root.classList.toggle('refining', busy);
      root.setAttribute('aria-busy', String(busy));
      if (!state || state === 'unavailable' || state === 'skipped') {
        refinement.classList.add('hidden');
        refinementState = state;
        return;
      }
      show(refinement);
      const displayState = `${state}:${busy}`;
      if (refinementState === displayState) return;
      refinementState = displayState;
      refinement.replaceChildren();
      if (state === 'completed') {
        refinement.append(h('p', { class: 'refinement-done', text: 'Your context has been applied. This is your final result.' }));
        return;
      }
      const label = h('label', { for: 'refinement-context', text: 'Anything the first result missed?' });
      const help = h('p', { class: 'refinement-help', id: 'refinement-help', text: 'Add a little more context or a correction. You get one refinement, not an ongoing chat.' });
      const input = h('textarea', {
        id: 'refinement-context', name: 'context', rows: '3', maxlength: '1000',
        placeholder: 'For example: this is for renters, and it needs to cost under $50.',
        'aria-describedby': 'refinement-help refinement-status',
        required: true,
      });
      input.value = refinementDraft || v.refinement?.context || '';
      const note = h('p', { class: 'refinement-note', id: 'refinement-status', role: 'status', 'aria-live': 'polite' });
      const submit = h('button', { class: 'btn primary', type: 'submit', text: busy ? 'Applying your context…' : 'Refine once →' });
      const skip = h('button', { class: 'btn ghost', type: 'button', text: 'Keep this result' });
      const waiting = state === 'waiting';
      input.disabled = busy;
      submit.disabled = busy || waiting;
      skip.disabled = busy;
      if (busy) note.textContent = 'Updating your venture. The first result stays safe until the refinement is ready.';
      else if (waiting) note.textContent = 'You can write now. Refinement will be ready when the first renders finish.';
      input.addEventListener('input', () => { refinementDraft = input.value; note.textContent = waiting ? 'Waiting for the first renders to finish…' : ''; });
      const recover = async (error) => {
        refinementBusy = false;
        // The request may have completed even if its response was interrupted.
        try { complete(await api(vpath(v, ''))); }
        catch (_) { refinementState = null; renderRefinement(venture); }
        const currentNote = refinement.querySelector('.refinement-note');
        if (currentNote && venture.refinement?.state !== 'pending') {
          currentNote.textContent = error.message + ' Your first result is still here.';
        } else if (venture.refinement?.state !== 'pending') toast(error.message);
      };
      const form = h('form', { onsubmit: async (e) => {
        e.preventDefault();
        const context = input.value.trim();
        if (!context || context.length > 1000) { note.textContent = 'Add between 1 and 1,000 characters.'; input.focus(); return; }
        if (refinementBusy || waiting) return;
        refinementDraft = context;
        refinementBusy = true;
        stopPolling();
        renderRefinement(v);
        try {
          const fresh = await api(vpath(v, '/refine'), { method: 'POST', body: JSON.stringify({ context }) });
          refinementBusy = false;
          refinementDraft = '';
          complete(fresh);
          toast('Your one refinement is complete.');
        } catch (error) { await recover(error); }
      } }, label, help, input, h('div', { class: 'refinement-buttons' }, submit, skip), note);
      skip.addEventListener('click', async () => {
        if (refinementBusy) return;
        refinementBusy = true;
        renderRefinement(v);
        try {
          const fresh = await api(vpath(v, '/refinement/skip'), { method: 'POST' });
          refinementBusy = false;
          refinementDraft = '';
          complete(fresh);
          toast('First result kept.');
        } catch (error) { await recover(error); }
      });
      refinement.append(form);
    }

    function applyImages(v) {
      (v.images || []).forEach((u, i) => {
        const slot = renderSlots[i]; if (!u || !slot || slot.classList.contains('done')) return;
        const img = slot.querySelector('img');
        img.alt = `${v.name} render ${i + 1}`;
        img.onload = () => { img.classList.add('on'); slot.classList.add('done'); };
        img.src = assetUrl(u) + (v.refinement?.state === 'completed' ? (u.includes('?') ? '&' : '?') + 'refined=1' : '');
      });
      const allIn = (v.images || []).filter(Boolean).length >= renderSlots.length;
      if (v.images_status === 'ready' || allIn) renderStatus.classList.add('hidden');
      else if (v.images_status === 'failed') {
        renderStatus.textContent = (v.images || []).some(Boolean) ? '' : 'Renders unavailable for this one.';
        renderSlots.forEach(s => { if (!s.classList.contains('done')) s.classList.add('done'); });
      } else {
        const n = (v.images || []).filter(Boolean).length;
        renderStatus.textContent = n ? `${n} of ${renderSlots.length} renders in…` : 'Rendering the product…';
      }
    }

    function startPolling() {
      stopPolling(); pollStart = Date.now();
      let delay = 2500;
      const tick = async () => {
        if (Date.now() - pollStart > 150000) {
          const note = refinement.querySelector('.refinement-note');
          if (venture.refinement?.state === 'pending' && note) note.textContent = 'Refinement is taking longer than usual. Refresh to check its progress.';
          else {
            renderStatus.classList.remove('hidden');
            renderStatus.textContent = 'Renders are taking longer than usual. Refresh in a minute.';
          }
          return;
        }
        try {
          const fresh = await api(`/api/ventures/${encodeURIComponent(venture.slug)}`);
          const enteredCompleted = venture.refinement?.state !== 'completed' && fresh.refinement?.state === 'completed';
          if (enteredCompleted || (venture.refinement?.state === 'pending' && fresh.refinement?.state !== 'pending')) {
            refinementState = null;
            complete(fresh);
            return;
          }
          venture = { ...venture, ...fresh };
          applyImages(fresh);
          renderRefinement(fresh);
          if (fresh.images_status === 'pending' || fresh.refinement?.state === 'pending') { delay = Math.min(delay * 1.25, 8000); pollTimer = setTimeout(tick, delay); }
        } catch (_) { pollTimer = setTimeout(tick, 6000); }
      };
      pollTimer = setTimeout(tick, delay);
    }
    function stopPolling() { if (pollTimer) clearTimeout(pollTimer); pollTimer = null; }

    // ---- The Path: pick one of the three, get walked through it ----
    const TIER_NAMES = ['The Lean Build', 'The Real Product', 'The Empire'];
    const gsearch = (q) => 'https://www.google.com/search?q=' + encodeURIComponent(q);
    const vpath = (v, rest) => `/api/ventures/${encodeURIComponent(v.slug)}${rest}`;
    let pathStreaming = false;

    function renderTierPicks(v) {
      root.querySelector('.pick-hint')?.remove();
      tierEls.forEach((t, i) => {
        const r = t.root;
        r.querySelectorAll('.pick, .chosen-tag').forEach(x => x.remove());
        const chosen = !!(v.path && v.path.tier === i);
        r.classList.toggle('chosen', chosen);
        if (chosen) r.append(h('span', { class: 'chosen-tag', text: v.is_owner ? 'Your path' : 'The path they chose' }));
        else if (v.is_owner && !v.path && !pathStreaming) r.append(h('button', { class: 'btn small pick', text: 'Walk this path →', disabled: v.refinement?.state === 'pending', onclick: () => choosePath(v, i) }));
      });
      if (v.is_owner && !v.path && !pathStreaming) tiers.after(h('p', { class: 'pick-hint', text: 'Three ways to build it. Pick one and the engine walks you through it step by step, with suppliers to call, notes, and a place to ask the Floor for help. You can reset and pick again.' }));
    }

    async function choosePath(v, tier) {
      if (pathStreaming) return;
      pathStreaming = true;
      renderTierPicks(v);
      path.replaceChildren(
        h('div', { class: 'path-head' }, h('div', { class: 'v-label', text: 'Your path' }), h('h2', { class: 'path-title caret', text: TIER_NAMES[tier] }),
          h('p', { class: 'path-sub', text: 'The engine is writing your step-by-step guide. Steps appear as they land.' })),
        h('div', { class: 'psteps' }));
      show(path); roadmap.classList.add('hidden');
      path.scrollIntoView({ behavior: 'smooth', block: 'start' });
      try {
        const res = await fetch(API + vpath(v, '/path'), { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tier }) });
        if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.detail || j.message || 'The engine stalled.'); }
        const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
        while (true) {
          const { value, done } = await reader.read(); if (done) break;
          buf += dec.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
            const ev = parseSSE(block); if (!ev) continue;
            if (ev.event === 'partial') paintPartialPath(ev.data.data, ev.data.streaming);
            else if (ev.event === 'done') { pathStreaming = false; complete(ev.data); toast('Your path is ready. Start with tonight.'); }
            else if (ev.event === 'error') throw new Error(ev.data.message);
          }
        }
        if (pathStreaming) throw new Error('The stream ended early.');
      } catch (e) { pathStreaming = false; toast(e.message); renderTierPicks(venture); renderPath(venture); }
    }

    function paintPartialPath(d, streaming) {
      const list = path.querySelector('.psteps'); if (!list) return;
      if (d.first_move != null) {
        let fm = path.querySelector('.first-move');
        if (!fm) { fm = h('div', { class: 'first-move born' }, h('b', { text: 'Tonight' }), h('span')); list.before(fm); }
        setText(fm.querySelector('span'), d.first_move, streaming === 'first_move');
      }
      (d.steps || []).forEach((st, i) => {
        let el = list.children[i];
        if (!el) {
          el = h('div', { class: 'pstep born' }, h('div', { class: 'pstep-head' }, h('div', { class: 'pstep-num', text: String(i + 1).padStart(2, '0') }), h('div', { class: 'pstep-title' }), h('div', { class: 'pstep-status', text: 'writing' })));
          list.append(el);
        }
        const last = streaming === 'steps' && i === d.steps.length - 1;
        setText(el.querySelector('.pstep-title'), st.title, last && st.why == null);
        if (last && st.why != null) el.querySelector('.pstep-status').textContent = 'writing details';
        else el.querySelector('.pstep-status').textContent = 'to do';
      });
      const t = path.querySelector('.path-title'); if (t) t.classList.toggle('caret', !d.steps);
    }

    function renderPath(v) {
      const p = v.path;
      if (!p || !p.steps || !p.steps.length) { path.classList.add('hidden'); path.replaceChildren(); roadmap.classList.remove('hidden'); return; }
      const own = v.is_owner;
      roadmap.classList.add('hidden');
      const current = p.steps.find(st => st.status !== 'done');
      const fill = h('div', { class: 'mark-fill' });
      const finished = p.done === p.total;
      const progress = h('div', { class: 'path-progress' }, h('div', { class: 'mark-line' }, fill),
        h('div', { class: 'mark-text' }, h('b', { text: String(p.done) }), ` of ${p.total} steps done${finished ? ' · built' : ''}`));
      setTimeout(() => { fill.style.width = (p.total ? (p.done / p.total) * 100 : 0).toFixed(1) + '%'; }, 60);
      const head = h('div', { class: 'path-head' },
        h('div', { class: 'v-label', text: own ? 'Your path' : 'The path' }),
        h('h2', { class: 'path-title', text: p.tier_label }),
        h('p', { class: 'path-sub', text: own
          ? 'Step by step. Check them off as you go, keep notes, use your own people or the suggested ones, and ask the Floor when you need a hand.'
          : `${v.name} is being built in the open. If you can help with an open ask, raise a hand.` }),
        progress);
      const list = h('div', { class: 'psteps' }, ...p.steps.map(st => stepEl(v, st, !!current && st.n === current.n)));
      const kids = [head];
      if (p.first_move) kids.push(h('div', { class: 'first-move' }, h('b', { text: 'Tonight' }), h('span', { text: p.first_move })));
      kids.push(list);
      const loose = (v.needs || []).filter(n => !p.steps.some(st => st.n === n.step_n));
      loose.forEach(n => kids.push(needEl(v, n)));
      if (p.watch_out) kids.push(h('div', { class: 'watch-out' }, h('b', { text: 'Watch out' }), p.watch_out));
      if (own) {
        const reset = h('button', { class: 'btn ghost small', text: 'Reset path', onclick: async () => {
          if (reset.dataset.armed !== '1') { reset.dataset.armed = '1'; reset.textContent = 'Sure? Notes and progress go too'; setTimeout(() => { reset.dataset.armed = ''; reset.textContent = 'Reset path'; }, 4000); return; }
          try { complete(await api(vpath(v, '/path'), { method: 'DELETE' })); toast('Path reset. Pick again.'); } catch (e) { toast(e.message); }
        } });
        kids.push(h('div', { class: 'path-foot' }, reset));
      }
      path.replaceChildren(...kids);
      show(path);
    }

    function stepEl(v, st, isCurrent) {
      const own = v.is_owner;
      const el = h('div', { class: `pstep ${st.status}${isCurrent ? ' current open' : ''}` });
      const statusText = ({ todo: isCurrent ? 'up next' : 'to do', doing: 'in progress', done: 'done' })[st.status] || '';
      const head = h('div', { class: 'pstep-head', onclick: () => el.classList.toggle('open') },
        h('div', { class: 'pstep-num', text: String(st.n).padStart(2, '0') }), h('div', { class: 'pstep-title', text: st.title }), h('div', { class: 'pstep-status', text: statusText }));
      const body = h('div', { class: 'pstep-body' });
      if (st.why) body.append(h('p', { class: 'pstep-why', text: st.why }));
      if (st.how && st.how.length) body.append(h('ul', { class: 'pstep-how' }, ...st.how.map(x => h('li', { text: x }))));
      const meta = [];
      if (st.cost) meta.push(h('span', {}, 'Cost ', h('b', { text: st.cost })));
      if (st.time) meta.push(h('span', {}, 'Time ', h('b', { text: st.time })));
      if (meta.length) body.append(h('div', { class: 'pstep-meta' }, ...meta));
      if (st.done_when) body.append(h('p', { class: 'pstep-done-when' }, h('b', { text: 'Done when: ' }), st.done_when));
      if (st.suppliers && st.suppliers.length) {
        const sup = h('div', { class: 'sup' }, h('div', { class: 'sup-label', text: 'Who can help with this step' }));
        st.suppliers.forEach(x => {
          const row = h('div', { class: 'sup-row' }, h('span', { class: 'sup-kind', text: x.kind }));
          (x.examples || []).forEach(n => row.append(h('a', { class: 'chip', href: gsearch(n), target: '_blank', rel: 'noopener', text: n })));
          if (x.search) row.append(h('a', { class: 'chip search', href: gsearch(x.search), target: '_blank', rel: 'noopener', text: 'Search: ' + x.search }));
          sup.append(row);
        });
        sup.append(h('p', { class: 'sup-note', text: 'Suggested by the engine, not vetted. Use your own people, or ask the Floor.' }));
        body.append(sup);
      }
      (v.needs || []).filter(n => n.step_n === st.n).forEach(n => body.append(needEl(v, n)));
      if (own) {
        const ctl = h('div', { class: 'pstep-ctl' });
        const set = async (status) => {
          try { complete(await api(vpath(v, `/path/steps/${st.n}`), { method: 'PATCH', body: JSON.stringify({ status }) })); if (status === 'done') toast(`Step ${st.n} done.`); }
          catch (e) { toast(e.message); }
        };
        if (st.status !== 'done') ctl.append(h('button', { class: 'btn small primary', text: 'Mark done', onclick: () => set('done') }));
        if (st.status === 'todo') ctl.append(h('button', { class: 'btn small', text: 'Start it', onclick: () => set('doing') }));
        if (st.status === 'done') ctl.append(h('button', { class: 'btn small ghost', text: 'Undo', onclick: () => set('todo') }));
        const askBtn = h('button', { class: 'btn small ghost', text: 'Ask the Floor for help', onclick: () => {
          askBtn.classList.add('hidden'); const f = needForm(v, st); ctl.after(f); f.querySelector('input').focus();
        } });
        ctl.append(askBtn);
        const note = h('textarea', { class: 'pstep-note', placeholder: 'Your notes on this step: who you called, what they quoted, what you learned.' });
        note.value = st.note || '';
        let saved = note.value;
        note.addEventListener('blur', async () => {
          if (note.value === saved) return;
          try { venture = await api(vpath(v, `/path/steps/${st.n}`), { method: 'PATCH', body: JSON.stringify({ note: note.value }) }); saved = note.value; toast('Note saved.'); }
          catch (e) { toast(e.message); }
        });
        body.append(ctl, note);
      }
      el.append(head, body);
      return el;
    }

    function needForm(v, st) {
      const title = h('input', { placeholder: `What do you need for step ${st.n}? A shop, a part, a person…`, maxlength: '120', required: true });
      const detail = h('textarea', { placeholder: 'Details: quantity, location, budget, timing.', maxlength: '600' });
      const note = h('div', { class: 'form-note' });
      return h('form', { class: 'need-form', onsubmit: async (e) => {
        e.preventDefault();
        try {
          complete(await api(vpath(v, '/needs'), { method: 'POST', body: JSON.stringify({ step_n: st.n, title: title.value, detail: detail.value }) }));
          toast(v.visibility === 'public' ? 'Posted to the Floor.' : 'Saved. It shows on the Floor once you release the venture.');
        } catch (err) { note.textContent = err.message; }
      } }, title, detail, h('button', { class: 'btn small primary', type: 'submit', text: 'Post the ask' }), note);
    }

    function needEl(v, n) {
      const own = v.is_owner;
      const el = h('div', { class: 'need' + (n.status !== 'open' ? ' filled' : ''), id: 'need-' + n.id });
      el.append(h('div', { class: 'need-head' },
        h('div', { class: 'need-title' }, h('i', { text: n.status === 'open' ? 'Open ask' : n.status }), n.title),
        h('div', { class: 'need-count', text: n.offer_count === 1 ? '1 hand raised' : `${n.offer_count} hands raised` })));
      if (n.detail) el.append(h('p', { class: 'need-detail', text: n.detail }));
      if (own) {
        (n.offers || []).forEach(o => el.append(h('div', { class: 'offer' }, h('b', { text: o.name || 'Someone' }), ' ', h('span', { text: o.email }), o.message ? h('div', { text: o.message }) : null)));
        if (n.status === 'open') el.append(h('div', { class: 'pstep-ctl' }, h('button', { class: 'btn small ghost', text: 'Mark filled', onclick: async () => {
          try { complete(await api(vpath(v, `/needs/${n.id}`), { method: 'PATCH', body: JSON.stringify({ status: 'filled' }) })); } catch (e) { toast(e.message); }
        } })));
      } else if (n.status === 'open' && v.visibility === 'public') {
        const name = h('input', { placeholder: 'Your name or shop', maxlength: '80' });
        const email = h('input', { type: 'email', placeholder: 'you@domain.com', required: true, autocomplete: 'email' });
        const msg = h('textarea', { placeholder: 'What you can do, where you are, rough terms.', maxlength: '600' });
        const note = h('div', { class: 'form-note' });
        const btn = h('button', { class: 'btn small primary', type: 'submit', text: 'I can help' });
        el.append(h('form', { class: 'offer-form', onsubmit: async (e) => {
          e.preventDefault(); btn.disabled = true;
          try {
            const u = await api(vpath(v, `/needs/${n.id}/offers`), { method: 'POST', body: JSON.stringify({ email: email.value, name: name.value, message: msg.value }) });
            complete(u); toast(u.added ? 'Hand raised. The creator sees your email.' : 'You already raised a hand on this one.');
          } catch (err) { btn.disabled = false; note.textContent = err.message; }
        } }, name, email, msg, btn, note));
      }
      return el;
    }

    function renderOwner(v) {
      owner.replaceChildren();
      if (!v.is_owner) { owner.classList.add('hidden'); return; }
      if (v.visibility !== 'public') {
        const btn = h('button', { class: 'btn primary', text: 'Release it →', onclick: async () => {
          btn.disabled = true; btn.textContent = 'Releasing…';
          try {
            const updated = await api(`/api/ventures/${encodeURIComponent(v.slug)}/release`, { method: 'POST' });
            toast('Released. The world can see it now.');
            complete(updated);
          } catch (e) { btn.disabled = false; btn.textContent = 'Release it →'; toast(e.message); }
        } });
        btn.disabled = v.refinement?.state === 'pending';
        owner.append(
          h('h3', { text: 'This venture is private.' }),
          h('p', { text: 'Only you can see this page. Releasing it makes it public, lets people signal what they’d do with it, and starts its climb toward the Mark. The record already proves it was yours first.' }),
          h('div', { class: 'row' }, btn, h('button', { class: 'btn ghost', text: 'Keep it private', onclick: () => toast('Kept private. It’s here whenever you come back.') })),
        );
      } else {
        owner.append(
          h('h3', { text: 'Released.' }),
          h('p', { text: `Public since ${fmtDate(v.released_at, true)}. Share the link. Every signal it collects is real.` }),
          h('div', { class: 'row' }, copyBtn(v, 'Copy share link'), h('a', { class: 'btn ghost', href: href('/marketplace'), 'data-route': '/marketplace', text: 'See the Floor →' })),
        );
      }
      show(owner);
    }

    function copyBtn(v, label) {
      return h('button', { class: 'btn primary', text: label, onclick: async () => {
        try { await navigator.clipboard.writeText(shareUrl(v.slug)); toast('Link copied.'); }
        catch (_) { toast(shareUrl(v.slug)); }
      } });
    }

    let activeSignal = null;
    function renderSignals(v) {
      signals.replaceChildren();
      if (v.visibility !== 'public') { signals.classList.add('hidden'); return; }
      const s = v.signals || { buy: 0, build: 0, fund: 0, total: 0 };
      const mk = (type, label, sub) => {
        const el = h('div', { class: 'sig' + (activeSignal === type ? ' active' : ''), role: 'button', tabindex: '0' },
          h('div', { class: 'n', text: String(s[type] || 0) }),
          h('div', { class: 'l' }, h('i', { text: label }), ` · ${sub}`));
        const open = () => { activeSignal = type; renderSignals(venture); form.querySelector('input[type=email]').focus(); };
        el.addEventListener('click', open);
        el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
        return el;
      };
      const row = h('div', { class: 'sig-row' }, mk('buy', 'I’d buy this', 'customer'), mk('build', 'I’d build this', 'builder'), mk('fund', 'I’d fund this', 'investor'));

      const email = h('input', { type: 'email', placeholder: 'you@domain.com', required: true, autocomplete: 'email' });
      const price = h('input', { class: 'price', type: 'number', min: '0', step: '1', placeholder: 'Price $' });
      const note = h('div', { class: 'sig-note' });
      const form = h('form', { class: 'sig-form' + (activeSignal ? '' : ' hidden'), onsubmit: async (e) => {
        e.preventDefault();
        const body = { type: activeSignal, email: email.value.trim() };
        if (activeSignal === 'buy' && price.value) body.price = Number(price.value);
        try {
          const updated = await api(`/api/ventures/${encodeURIComponent(v.slug)}/signals`, { method: 'POST', body: JSON.stringify(body) });
          venture = updated;
          activeSignal = null;
          renderSignals(updated);
          const n = signals.querySelector('.sig-note'); if (n) { n.textContent = updated.added ? 'Signal received. It counts.' : 'You already signaled this one.'; n.classList.add('ok'); }
          if (updated.mark.marked && !v.mark.marked) toast('This venture just earned the Mark.');
          if (updated.is_owner) renderOwner(updated);
        } catch (err) { note.textContent = err.message; }
      } });
      if (activeSignal === 'buy') form.append(price);
      form.append(email, h('button', { class: 'btn small primary', type: 'submit', text: activeSignal === 'buy' ? 'I’d buy it' : activeSignal === 'build' ? 'I’d build it' : 'I’d fund it' }));

      const m = v.mark || {};
      const progress = Math.min(1, Math.max(s.total / (m.threshold_signals || 25), ((s.build || 0) + (s.fund || 0)) / (m.threshold_build_fund || 5)));
      const fill = h('div', { class: 'mark-fill' });
      const track = m.marked
        ? h('div', { class: 'mark-track' }, h('span', { class: 'marked-badge' }, svg('<svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><circle cx="5" cy="5" r="5"/></svg>'), `Marked · ${fmtDate(m.marked_at)}`))
        : h('div', { class: 'mark-track' }, h('div', { class: 'mark-line' }, fill),
            h('div', { class: 'mark-text' }, h('b', { text: String(s.total) }), ` of ${m.threshold_signals} signals · or ${m.threshold_build_fund} builders and funders · to the Mark`));
      setTimeout(() => { fill.style.width = (progress * 100).toFixed(1) + '%'; }, 60);

      signals.append(
        h('h2', { class: 'sig-title', text: 'Would you move on this?' }),
        h('p', { class: 'sig-sub', text: s.avg_price ? `People who’d buy it say about $${Math.round(s.avg_price)}. Real emails only, one signal per person per kind.` : 'Three honest buttons. Real emails only, one signal per person per kind. No fake bids, ever.' }),
        row, form, note, track,
      );
      show(signals);
    }

    function renderActions(v) {
      actions.replaceChildren(...[
        v.visibility === 'public' ? copyBtn(v, 'Copy share link') : null,
        h('button', { class: 'btn', text: 'Copy summary', onclick: async () => {
          const t = `${v.name}\n${v.tagline}\n\n${v.description}\n\nProvenance ${v.provenance.code}\n${shareUrl(v.slug)}`;
          try { await navigator.clipboard.writeText(t); toast('Summary copied.'); } catch (_) { toast('Could not copy on this device.'); }
        } }),
        h('a', { class: 'btn ghost', href: href('/'), 'data-route': '/', text: 'Manifest another' }),
      ].filter(Boolean));
      show(actions);
    }

    // ---- boot ----
    if (live) {
      if (live.meta) prov.replaceChildren('Provenance ', h('b', { text: live.meta.provenance.code }), ' · minting…');
      update(live.data, live.streaming);
    } else if (slug) {
      prov.textContent = 'Loading…';
      api(`/api/ventures/${encodeURIComponent(slug)}`).then(complete).catch((e) => {
        mount(NotFound(e.message), null);
      });
    }

    root.update = update;
    root.complete = complete;
    root.cleanup = stopPolling;
    return root;
  }

  function fmtDate(iso, withTime = false) {
    if (!iso) return '';
    const d = new Date(iso);
    const date = d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    if (!withTime) return date;
    return `${date} · ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })}`;
  }

  // ---------- floor ----------
  function FloorView() {
    const marked = h('div', { class: 'cards' }), rising = h('div', { class: 'cards' }), reqs = h('div', { class: 'reqs' });
    const root = h('section', { class: 'view floor' },
      h('div', { class: 'floor-head' }, h('div', { class: 'eyebrow', text: 'The Floor' }),
        h('h2', { text: 'Ventures that earned their place.' }),
        h('p', { class: 'lead', text: 'Every venture here was born from one sentence, sealed to its creator, and released on purpose. The Marked ones crossed a real signal threshold.' })),
      h('div', { class: 'floor-section' }, h('h3', { class: 'v-label', text: 'Marked' }), marked),
      h('div', { class: 'floor-section' }, h('h3', { class: 'v-label', text: 'Rising' }), rising),
      h('div', { class: 'floor-section' }, h('h3', { class: 'v-label', text: 'Open asks' }), reqs),
    );
    const req = (r) => {
      const el = h('a', { class: 'req', href: href(`/v/${r.venture.slug}`), 'data-route': `/v/${r.venture.slug}` },
        h('div', { class: 'from', text: `${r.venture.name}${r.step_n ? ` · step ${r.step_n}` : ''}` }),
        h('p', { class: 'rt', text: r.title }), r.detail ? h('p', { class: 'rd', text: r.detail }) : null,
        h('div', { class: 'rm' }, h('span', { text: r.offer_count === 1 ? '1 hand raised' : `${r.offer_count} hands raised` }), h('span', { text: 'I can help →' })));
      el.style.setProperty('--accent', r.venture.accent);
      return el;
    };
    const card = (c) => {
      const el = h('a', { class: 'card', href: href(`/v/${c.slug}`), 'data-route': `/v/${c.slug}` },
        h('div', { class: 'thumb' }, c.image ? h('img', { src: assetUrl(c.image), alt: '', loading: 'lazy' }) : null),
        h('div', { class: 'name', text: c.name }), h('div', { class: 'tag', text: c.tagline }),
        h('div', { class: 'meta' }, h('span', { text: c.category || '' }), h('span', {}, h('b', { text: String(c.signals.total) }), ` signal${c.signals.total === 1 ? '' : 's'}`)),
        c.path ? h('div', { class: 'meta' }, h('span', { text: c.path.tier_label }), h('span', { class: 'prog', text: `${c.path.done}/${c.path.total} steps` })) : null);
      el.style.setProperty('--accent', c.accent);
      return el;
    };
    let timer;
    const load = async () => {
      try {
        const data = await api('/api/marketplace');
        marked.replaceChildren(...(data.marked.length ? data.marked.map(card) : [h('p', { class: 'empty', text: 'No venture has earned the Mark yet. The first one will be remembered.' })]));
        rising.replaceChildren(...(data.rising.length ? data.rising.map(card) : [h('p', { class: 'empty' }, 'Nothing released yet — ', h('a', { href: href('/'), 'data-route': '/', text: 'manifest the first one' }), '.')]));
        const rq = data.requests || [];
        reqs.replaceChildren(...(rq.length ? rq.map(req) : [h('p', { class: 'empty', text: 'No open asks right now. When a creator needs a shop, a part, or a person, it shows up here.' })]));
      } catch (_) {}
    };
    load(); timer = setInterval(load, 20000);
    root.cleanup = () => clearInterval(timer);
    return root;
  }

  // ---------- waitlist ----------
  function WaitlistView() {
    const name = h('input', { placeholder: 'Name (optional)', autocomplete: 'name' });
    const email = h('input', { type: 'email', placeholder: 'Email', required: true, autocomplete: 'email' });
    const role = h('select', {}, h('option', { value: 'Builder', text: 'I build things' }), h('option', { value: 'Investor', text: 'I fund things' }), h('option', { value: 'Curious', text: 'Just curious' }));
    const done = h('div', { class: 'wl-done hidden', text: 'You’re on the list. MANIFEST will find you.' });
    const form = h('form', { class: 'wl-form', onsubmit: async (e) => {
      e.preventDefault();
      try { await api('/api/waitlist', { method: 'POST', body: JSON.stringify({ name: name.value.trim(), email: email.value.trim(), role: role.value }) }); form.classList.add('hidden'); done.classList.remove('hidden'); }
      catch (err) { toast(err.message); }
    } }, name, email, role, h('button', { class: 'btn primary', type: 'submit', text: 'Join the waitlist' }));
    return h('section', { class: 'view waitlist' },
      h('div', { class: 'eyebrow', text: 'Early access' }),
      h('h2', {}, 'Reserve your place', h('br'), 'at the edge of invention.'),
      h('p', { class: 'lead', text: 'Be first in when the Floor opens to builders and funders, and when new Marked ventures land.' }),
      form, done);
  }

  // ---------- about / record explainer ----------
  function AboutView() {
    return h('section', { class: 'view section' },
      h('div', { class: 'eyebrow', text: 'The record' }),
      h('h2', { text: 'What the provenance record is, and isn’t.' }),
      h('p', { class: 'lead', text: 'When a venture is born, MANIFEST computes a SHA-256 hash of three things: the exact sentence you typed, the exact second it was minted, and your private creator key. That hash is stored with the venture and shown on its page.' }),
      h('p', { class: 'lead', style: { marginTop: '18px' }, text: 'Because the hash can only be produced from those exact inputs, it proves the sentence existed at that moment and was minted by the holder of that key. Anyone can verify it. Nobody can back-date it.' }),
      h('p', { class: 'lead', style: { marginTop: '18px' }, text: 'It is not a patent, a trademark, or a copyright registration. Publishing an idea can affect your ability to patent it later, so releasing is your call and yours alone. MANIFEST never takes ownership of anything you make here.' }),
      h('div', { style: { marginTop: '40px' } }, h('a', { class: 'btn', href: href('/'), 'data-route': '/', text: '← Back' })));
  }

  function NotFound(msg) {
    return h('section', { class: 'view notfound' },
      h('h2', { text: 'Nothing here yet.' }), h('p', { text: msg || 'No venture lives at this address.' }),
      h('a', { class: 'btn primary', href: href('/'), 'data-route': '/', text: 'Manifest one' }));
  }

  // ---------- start ----------
  render();
  setTimeout(checkReveals, 50);
})();
