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

  function conceptDisclosure() {
    return h('aside', { class: 'concept-disclosure', 'aria-label': 'Important concept limitations' },
      h('strong', { text: 'AI-generated concept · Not a tested product' }),
      h('p', { text: 'Streaming drafts are unchecked; do not act on them. Proposed advantages are hypotheses, not proof of performance, safety or originality. Prices and plans are estimates; renders are illustrations. Verify claims, rights and safety with qualified professionals before building, selling or investing.' }),
      h('a', { href: href('/about'), 'data-route': '/about', text: 'Read important limitations' }));
  }

  // ---------- api ----------
  async function api(path, opts = {}) {
    const res = await fetch(API + path, {
      ...opts,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    });
    let data = null;
    try { data = await res.json(); } catch (error) {
      opts.signal?.throwIfAborted();
      if (error.name === 'AbortError') throw error;
      if (res.ok) throw new Error('The response ended before valid data arrived. Please check the saved result before retrying.');
    }
    if (!res.ok) throw new Error((data && data.error) || 'Something went wrong.');
    return data;
  }
  const assetUrl = (u) => (u && u.startsWith('/') ? API + u : u);

  // Timings are local Performance entries, not idea text or visitor analytics.
  const activeGenerationWaits = new Set();
  function generationWait(operation, paint, limitMs = 210000) {
    const started = performance.now();
    const controller = new AbortController();
    let firstText = false, stopped = false, cancelled = false;
    const tick = () => {
      const seconds = Math.floor((performance.now() - started) / 1000);
      paint(`${firstText ? 'Writing' : 'Considering your idea'}… ${seconds}s. ${seconds >= 30 ? 'Still working; text generation has a two-minute limit.' : 'Text appears when it is ready.'}`);
    };
    tick();
    const timer = setInterval(tick, 1000);
    const deadline = setTimeout(() => controller.abort(), limitMs);
    const measure = (suffix) => performance.measure(`manifest.${operation}.${suffix}`, { start: started, end: performance.now() });
    const wait = {
      signal: controller.signal,
      get cancelled() { return cancelled; },
      first() { if (!firstText) { firstText = true; measure('first-visible-text'); tick(); } },
      finish(outcome = 'completed') {
        if (stopped) return;
        stopped = true; clearInterval(timer); clearTimeout(deadline);
        measure(outcome);
      },
      cancel() { cancelled = true; wait.finish('interrupted'); wait.dispose(); },
      pause() { clearInterval(timer); clearTimeout(deadline); controller.abort(); },
      dispose() { activeGenerationWaits.delete(wait); wait.pause(); },
    };
    activeGenerationWaits.add(wait);
    return wait;
  }

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
    if (route === '/' && currentRoute() === '/' && !activeView) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
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
    // Route changes abandon browser work, not the server's durable request.
    // Late callbacks must not remount the old view or overwrite the new route.
    for (const wait of activeGenerationWaits) wait.cancel();
    const route = currentRoute();
    setAccent(null);
    if (route === '/') { mount(null, '/'); resetHero(); checkReveals(); return; }
    if (route.startsWith('/v/')) { mount(VentureView({ slug: route.slice(3) }), null); return; }
    if (route === '/marketplace') { mount(FloorView(), '/marketplace'); return; }
    if (route === '/waitlist') { mount(WaitlistView(), '/waitlist'); return; }
    if (route === '/about') { mount(AboutView(), null); return; }
    if (route === '/architect') { mount(ArchitectView(), '/architect'); return; }
    if (route === '/mix') { mount(MixView(), '/mix'); return; }
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
  let manifestBusy = false;

  ideaInput.addEventListener('input', () => { ideaInput.style.height = 'auto'; ideaInput.style.height = ideaInput.scrollHeight + 'px'; });
  ideaInput.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ideaForm.requestSubmit(); } });
  $('#cta-top').addEventListener('click', (e) => { e.preventDefault(); window.scrollTo({ top: 0, behavior: 'smooth' }); setTimeout(() => ideaInput.focus(), 500); });

  let pendingSentence = null;
  let pendingMixId = null;
  let pendingMixSentence = null;
  // Manual edits always detach the sentence from its source mix.
  ideaInput.addEventListener('input', () => {
    if (pendingMixSentence !== null && ideaInput.value !== pendingMixSentence) {
      pendingMixId = null;
      pendingMixSentence = null;
    }
  });
  function resetHero() {
    orbWrap.classList.remove('forming'); formingName.textContent = ''; formingStatus.textContent = '';
    heroError.textContent = ''; heroTag.style.opacity = '';
    if (pendingSentence) {
      const sentence = pendingSentence;
      pendingSentence = null;
      ideaInput.value = sentence;
      pendingMixSentence = sentence;
      ideaInput.dispatchEvent(new Event('input'));
      setTimeout(() => { window.scrollTo({ top: 0, behavior: 'smooth' }); ideaInput.focus(); ideaInput.setSelectionRange(ideaInput.value.length, ideaInput.value.length); }, 80);
    }
  }
  function manifestSentence(sentence, mixId = null) { pendingSentence = sentence; pendingMixId = mixId; go('/'); }

  ideaForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const idea = ideaInput.value.trim();
    if (idea.length < 8) { heroError.textContent = 'Give it a full sentence.'; return; }
    heroError.textContent = '';
    const mixId = pendingMixSentence === idea ? pendingMixId : null;
    startManifest(idea, mixId);
  });

  function checkReveals() {
    document.querySelectorAll('.reveal:not(.in)').forEach(el => {
      if (el.getBoundingClientRect().top < innerHeight * 0.88) el.classList.add('in');
    });
  }
  addEventListener('scroll', checkReveals, { passive: true });
  addEventListener('resize', checkReveals);

  // ---------- durable manifest recovery ----------
  const MANIFEST_REQUEST_KEY = 'manifest.pending-request.v1';
  let memoryRequest = null;
  function pendingManifest() {
    try {
      const value = JSON.parse(localStorage.getItem(MANIFEST_REQUEST_KEY));
      if (value && typeof value.request_id === 'string' && typeof value.idea === 'string') return value;
    } catch (_) {}
    return memoryRequest;
  }
  function rememberManifest(value) {
    memoryRequest = value;
    try {
      if (value) localStorage.setItem(MANIFEST_REQUEST_KEY, JSON.stringify(value));
      else localStorage.removeItem(MANIFEST_REQUEST_KEY);
    } catch (_) {} // Recovery still works in this tab when storage is disabled.
  }
  function clearManifest(requestId) {
    if (pendingManifest()?.request_id === requestId) rememberManifest(null);
  }
  function dismissLegacyManifest(requestId) {
    const pending = pendingManifest();
    if (!pending?.legacy || pending.request_id !== requestId || manifestBusy) return false;
    if (!window.confirm(
      'The earlier attempt is unconfirmed, not confirmed failed. It may already have saved a venture or may still finish. Generating again could create a duplicate and use another generation allowance.\n\n' +
      'Continue only if you accept that risk. This clears only the old pending marker in this browser. It does not delete ventures, clear your creator cookie, cancel server work, or send another request.'
    )) return false;
    const current = pendingManifest();
    if (!current?.legacy || current.request_id !== requestId || manifestBusy) return false;
    clearManifest(requestId);
    heroError.textContent = 'Old pending marker dismissed. Your ventures and creator access are unchanged. No new request has been sent. Press Manifest it only when you want to start a new attempt.';
    return true;
  }
  function showLegacyManifestNotice(pending) {
    heroError.replaceChildren(
      h('p', { text: 'This earlier attempt has no recovery tracking, so we cannot confirm whether it finished. It will not be resubmitted automatically.' }),
      h('button', {
        type: 'button', class: 'btn small', text: 'Dismiss this old pending attempt',
        onclick: () => dismissLegacyManifest(pending.request_id),
      }),
      h('p', { text: 'Dismissing does not cancel the old attempt or delete a saved venture. Generating again may create a duplicate. You will be asked to confirm.' }),
    );
  }
  async function reconcileManifest(requestId, attempts = 15) {
    let result = { state: 'unconfirmed' };
    for (let i = 0; i < attempts; i++) {
      try {
        // Independent deadline: the original generation signal may be aborted.
        const response = await fetch(API + `/api/manifest/requests/${encodeURIComponent(requestId)}`, {
          credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000),
        });
        if (response.ok) {
          result = await response.json();
          if (result.state === 'completed' && result.venture) return result;
          if (['failed', 'retired', 'unconfirmed'].includes(result.state)) return result;
        } else if (response.status === 503) {
          const error = await response.json();
          if (error.code === 'manifest_recovery_unavailable') return { state: 'unavailable', message: error.error };
        }
      } catch (_) {} // Offline/404 is not evidence that the save failed.
      if (i + 1 < attempts) await new Promise(resolve => setTimeout(resolve, 2000));
    }
    return result;
  }
  function showRecoveredManifest(venture, requestId) {
    clearManifest(requestId);
    orbWrap.classList.remove('forming');
    formingStatus.textContent = '';
    heroTag.style.opacity = '';
    const view = VentureView({ live: { meta: null, data: venture, streaming: null, idea: venture.idea_raw } });
    mount(view, null);
    view.complete(venture);
    history.replaceState({}, '', href(`/v/${venture.slug}`));
    toast('Your saved venture was recovered.');
  }
  function showUnconfirmedManifest(result, requestId) {
    heroError.textContent = result.state === 'pending'
      ? 'Your previous request has not confirmed completion yet. No new generation has been sent.'
      : 'This previous request is still unconfirmed. Check its result, or safely retire it if its waiting period has ended.';
    heroError.append(h('button', {
      class: 'btn small', type: 'button', text: 'Check previous request',
      onclick: () => resumeManifestRecovery(),
    }));
    if (result.state !== 'unconfirmed' || pendingManifest()?.legacy) return;
    heroError.append(' ');
    heroError.append(h('button', {
      class: 'btn small', type: 'button', text: 'Retire abandoned request',
      onclick: () => retireManifest(requestId),
    }));
  }
  async function retireManifest(requestId) {
    if (manifestBusy || pendingManifest()?.request_id !== requestId) return;
    if (!confirm('Retire this abandoned request? If it has already saved, your venture will be recovered. Otherwise it will be permanently blocked from saving, so you can start a new request.')) return;
    manifestBusy = true;
    try {
      const response = await fetch(API + `/api/manifest/requests/${encodeURIComponent(requestId)}/retire`, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        if (error.code === 'manifest_recovery_unavailable') {
          heroError.textContent = error.error;
          return;
        }
        throw new Error('Retirement could not be confirmed.');
      }
      const result = await response.json();
      if (result.state === 'completed' && result.venture) showRecoveredManifest(result.venture, requestId);
      else if (result.state === 'retired') {
        clearManifest(requestId);
        heroError.textContent = 'The abandoned request is permanently retired. You can now submit a new idea.';
      } else {
        heroError.textContent = 'This request is still active and cannot be retired yet. Check it again after its waiting period.';
      }
    } catch (_) {
      // A lost retirement response is ambiguous too. Keep the identity so GET
      // recovery can confirm the durable fence before any new submission.
      heroError.textContent = 'Retirement is unconfirmed. Reconnect or check the same request again; no new request has been sent.';
    } finally { manifestBusy = false; }
  }
  async function resumeManifestRecovery() {
    const pending = pendingManifest();
    if (!pending || manifestBusy) return;
    if (!ideaInput.value.trim()) {
      ideaInput.value = pending.idea;
      pendingMixId = pending.mix_id || null;
      pendingMixSentence = pending.idea;
      ideaInput.dispatchEvent(new Event('input'));
    }
    if (pending.legacy) {
      showLegacyManifestNotice(pending);
      return;
    }
    manifestBusy = true;
    heroError.textContent = 'Checking your previous request… No second generation is being sent.';
    try {
      // One bounded lookup on load, not a silent multi-minute polling lock.
      // The creator can explicitly check again if work is still pending.
      const result = await reconcileManifest(pending.request_id, 1);
      if (result.state === 'completed') showRecoveredManifest(result.venture, pending.request_id);
      else if (result.state === 'failed' || result.state === 'retired') {
        clearManifest(pending.request_id);
        heroError.textContent = 'The interrupted request did not save a venture. You can try again.';
      } else if (result.state === 'unavailable') {
        heroError.textContent = result.message;
      } else {
        showUnconfirmedManifest(result, pending.request_id);
      }
    } finally { manifestBusy = false; }
  }
  addEventListener('online', resumeManifestRecovery);

  // ---------- the reveal ----------
  async function startManifest(idea, mixId = null) {
    if (manifestBusy) {
      heroError.textContent = 'Your request is already being checked or generated. No second generation has been sent.';
      return;
    }
    manifestBusy = true;
    let pending = pendingManifest();
    if (pending?.legacy) {
      manifestBusy = false;
      showLegacyManifestNotice(pending);
      return;
    }
    if (pending) {
      heroError.textContent = 'Checking your previous request before starting another…';
      try {
        const result = await reconcileManifest(pending.request_id, 1);
        if (result.state === 'completed') { showRecoveredManifest(result.venture, pending.request_id); return; }
        if (!['failed', 'retired'].includes(result.state)) {
          showUnconfirmedManifest(result, pending.request_id);
          return;
        }
        clearManifest(pending.request_id);
        pending = null;
      } finally { manifestBusy = false; }
      manifestBusy = true;
    }
    const requestId = pending?.request_id || crypto.randomUUID();
    rememberManifest({ request_id: requestId, idea, mix_id: mixId });
    orbWrap.classList.add('forming');
    heroTag.style.opacity = '0';
    formingName.textContent = '';
    const liveStatus = h('p', { role: 'status', 'aria-live': 'polite', class: 'refinement-note' });
    const wait = generationWait('venture', text => {
      formingStatus.textContent = text;
      liveStatus.textContent = text;
    });

    let view = null;           // VentureView once we move to it
    let moved = false;
    let meta = null;
    let reader = null;
    let legacy = false;
    function completeManifest(data) {
      clearManifest(requestId);
      wait.finish(); liveStatus.remove();
      if (pendingMixSentence === idea) { pendingMixId = null; pendingMixSentence = null; }
      if (!moved) { view = VentureView({ live: { meta, data, streaming: null, idea } }); mount(view, null); }
      view.complete(data);
      history.replaceState({}, '', href(`/v/${data.slug}`));
    }

    try {
      let res = await fetch(API + '/api/manifest', {
        method: 'POST',
        credentials: 'same-origin',
        signal: wait.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idea, request_id: requestId, ...(mixId ? { mix_id: mixId } : {}) }),
      });
      // This is an explicit compatibility path, not a retry after generation:
      // setup 503 is returned before any quota reservation/provider call. Only
      // a brand-new request can downgrade; an existing unknown request cannot.
      if (!pending && res.status === 503) {
        const setup = await res.clone().json().catch(() => ({}));
        if (setup.code === 'manifest_recovery_unavailable') {
          legacy = true;
          rememberManifest({ request_id: requestId, idea, mix_id: mixId, legacy: true });
          liveStatus.textContent = 'Automatic recovery is unavailable. Keep this page open while your venture generates.';
          heroError.textContent = liveStatus.textContent;
          toast(liveStatus.textContent);
          res = await fetch(API + '/api/manifest', {
            method: 'POST', credentials: 'same-origin', signal: wait.signal,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ idea, ...(mixId ? { mix_id: mixId } : {}) }),
          });
        }
      }
      if (!res.ok || !res.body) {
        let msg = 'The engine is unavailable right now.';
        try { msg = (await res.json()).error || msg; } catch (_) {}
        const error = new Error(msg);
        // Validation happens before the request identity is claimed.
        if (res.status === 422 || (legacy && res.status === 429)) {
          clearManifest(requestId); error.rejected = true;
        }
        throw error;
      }
      if (res.headers.get('content-type')?.includes('application/json')) {
        const replay = await res.json();
        if (wait.cancelled) return;
        if (replay.state === 'completed') { completeManifest(replay.venture); return; }
        throw new Error(['failed', 'retired'].includes(replay.state) ?
          'The interrupted request did not save a venture. Submit again to start a new request.' :
          'The original request is still running. Checking for its saved result…');
      }
      reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      let done = false;
      while (!done) {
        const { value, done: rd } = await reader.read();
        if (wait.cancelled) return;
        if (rd) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const ev = parseSSE(raw);
          if (!ev) continue;
          if (ev.event === 'meta') meta = ev.data;
          else if (ev.event === 'partial') {
            wait.first();
            const { data, streaming } = ev.data;
            if (!moved) {
              formingName.textContent = data.name || '';
              formingName.classList.toggle('caret', streaming === 'name');
              if (data.name && streaming && streaming !== 'name') {
                // name is complete → move to the page
                moved = true;
                view = VentureView({ live: { meta, data, streaming, idea } });
                view.prepend(liveStatus);
                mount(view, null);
              }
            } else view.update(data, streaming);
          } else if (ev.event === 'done') {
            done = true; completeManifest(ev.data);
          } else if (ev.event === 'error') {
            const error = new Error(ev.data.message || 'The engine stalled.');
            error.fromServer = true;
            throw error;
          }
        }
      }
      if (!done) throw new Error('The connection ended before completion. This preview is not a confirmed result. Check your ventures before trying again.');
      await reader.cancel();
    } catch (err) {
      if (wait.cancelled) return;
      // Stop the old stream before lookup, but never POST another generation.
      if (reader) { try { await reader.cancel(); } catch (_) {} }
      // Abort the original connection and stop feedback, but keep this operation
      // registered until lookup settles so route changes can still cancel it.
      wait.pause();
      if (wait.cancelled) return;
      formingStatus.textContent = 'Checking for your saved venture…';
      liveStatus.textContent = 'Checking for your saved venture…';
      const result = err.rejected ? { state: 'failed' } : legacy ? {
        state: 'unavailable',
        message: 'Automatic recovery was unavailable and this response is unconfirmed. Check your ventures before trying again; this request will not be resubmitted.',
      } : await reconcileManifest(requestId);
      if (wait.cancelled) return;
      if (result.state === 'completed') { completeManifest(result.venture); return; }
      wait.finish('failed');
      if (['failed', 'retired'].includes(result.state)) clearManifest(requestId);
      const failureMessage = err.name === 'AbortError' ?
        'The connection timed out before completion. Your request was checked; you can try again.' : err.message;
      const message = err.fromServer ? `${err.message}${['failed', 'retired'].includes(result.state) ? '' : ' Completion is unconfirmed; no second request has been sent.'}` :
        result.state === 'unavailable' ? result.message : ['failed', 'retired'].includes(result.state) ? failureMessage :
        'The connection ended before completion. This preview is not a confirmed result. Reconnect or submit the same sentence to check again; no second venture will be generated.';
      if (moved) {
        liveStatus.textContent = message;
        view.querySelectorAll('.caret').forEach(el => el.classList.remove('caret'));
        liveStatus.append(h('a', { href: href('/'), 'data-route': '/', text: ' Return to your ventures.' }));
        toast(message);
      } else {
        orbWrap.classList.remove('forming'); formingStatus.textContent = ''; heroTag.style.opacity = '';
        if (pendingManifest()?.legacy) showLegacyManifestNotice(pendingManifest());
        else {
          heroError.textContent = message;
          if (result.state === 'unconfirmed' && !err.fromServer) showUnconfirmedManifest(result, requestId);
        }
      }
    } finally {
      wait.finish('interrupted');
      wait.dispose();
      manifestBusy = false;
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
    const head = h('header', { class: 'v-head' }, prov, name, tagline, desc, cat, conceptDisclosure());
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
    // what the Architect sees about this venture from above
    const archBox = h('div', { class: 'v-arch hidden' });
    // crossbreed: splice this venture with another, or with a field from left
    const cross = h('div', { class: 'cross hidden' });

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
      h('p', { class: 'fine', text: 'A SHA-256 reference to the submitted words, recorded creation time and creator’s key. This is not independent proof of first invention, legal ownership, a patent, trademark or copyright registration.' }));

    // owner panel
    const owner = h('div', { class: 'owner hidden' });
    // signals
    const signals = h('div', { class: 'signals hidden' });
    // actions
    const actions = h('div', { class: 'v-actions hidden' });

    root.append(halo, head, refinement, renders, tiers, path, roadmap, grounding, cross, archBox, seal, owner, signals, actions);

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
      show(renders);
      renderGrid.classList.toggle('hidden', v.images_status === 'failed' && !(v.images || []).some(Boolean));
      applyImages(v);
      renderCross(v);
      renderRefinement(v);
      renderOwner(v);
      renderSignals(v);
      renderActions(v);
      renderTierPicks(v);
      renderPath(v);
      if (v.visibility === 'public' && !archLoaded) { archLoaded = true; loadArchitect(v); }
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
      const unlimited = v.refinement?.unlimited === true;
      if (!state || state === 'unavailable' || (state === 'skipped' && !unlimited)) {
        refinement.classList.add('hidden');
        refinementState = state;
        return;
      }
      show(refinement);
      const waiting = state === 'waiting' || (unlimited && v.images_status === 'pending' && state !== 'pending');
      const displayState = `${state}:${busy}:${unlimited}:${waiting}:${v.refinement?.context || ''}`;
      if (refinementState === displayState) return;
      refinementState = displayState;
      refinement.replaceChildren();
      if (state === 'completed' && !unlimited) {
        refinement.append(h('p', { class: 'refinement-done', text: 'Your context has been applied. This is your final result.' }));
        return;
      }
      const label = h('label', { for: 'refinement-context', text: unlimited ? 'What would you like to refine?' : 'Anything the first result missed?' });
      const help = h('p', { class: 'refinement-help', id: 'refinement-help', text: unlimited ? 'Owner access: refine this private result as often as you need. Wait for each update and its renders to finish.' : 'Add a little more context or a correction. You get one refinement, not an ongoing chat.' });
      const input = h('textarea', {
        id: 'refinement-context', name: 'context', rows: '3', maxlength: '1000',
        placeholder: 'For example: this is for renters, and it needs to cost under $50.',
        'aria-describedby': 'refinement-help refinement-status',
        required: true,
      });
      input.value = refinementDraft || v.refinement?.context || '';
      const note = h('p', { class: 'refinement-note', id: 'refinement-status', role: 'status', 'aria-live': 'polite' });
      const submit = h('button', { class: 'btn primary', type: 'submit', text: busy ? 'Applying your context…' : unlimited ? 'Refine →' : 'Refine once →' });
      const skip = h('button', { class: 'btn ghost', type: 'button', text: 'Keep this result' });
      input.disabled = busy;
      submit.disabled = busy || waiting;
      skip.disabled = busy;
      if (busy) note.textContent = 'Updating your venture. The first result stays safe until the refinement is ready.';
      else if (waiting) note.textContent = 'You can write now. Refinement will be ready when the first renders finish.';
      input.addEventListener('input', () => { refinementDraft = input.value; note.textContent = waiting ? 'Waiting for the first renders to finish…' : ''; });
      const recover = async (error, cancelled = () => false) => {
        refinementBusy = false;
        // The request may have completed even if its response was interrupted.
        try {
          const fresh = await api(vpath(v, ''));
          if (cancelled()) return;
          complete(fresh);
        } catch (_) {
          if (cancelled()) return;
          refinementState = null; renderRefinement(venture);
        }
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
        const wait = generationWait('refinement', text => {
          const currentNote = refinement.querySelector('.refinement-note');
          if (currentNote) currentNote.textContent = `${text} Your first result stays safe until this update is ready.`;
        }, 240000);
        try {
          const fresh = await api(vpath(v, '/refine'), { method: 'POST', body: JSON.stringify({ context }), signal: wait.signal });
          if (wait.cancelled) return;
          wait.finish();
          refinementBusy = false;
          refinementDraft = '';
          complete(fresh);
          toast(unlimited ? 'Your refinement is complete.' : 'Your one refinement is complete.');
        } catch (error) {
          if (wait.cancelled) return;
          wait.finish('failed');
          if (error.name === 'AbortError') error = new Error('The connection timed out. Checking whether your refinement finished.');
          await recover(error, () => wait.cancelled);
        } finally { wait.finish('interrupted'); wait.dispose(); }
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

    // ---- crossbreed ----
    let crossBusy = false;
    let floorList = null;
    function renderCross(v) {
      if (!v.is_owner) { cross.classList.add('hidden'); cross.replaceChildren(); return; }
      const sp = v.splices;
      const kids = [h('h2', { class: 'v-label', text: 'Crossbreed' }),
        h('p', { class: 'cross-sub', text: 'Splice this with something it should not go with: another venture on the Floor, or a mechanism from a field that has nothing to do with it. Three hybrids, each with what could go wrong, why it might perform better or equally well, and a test of that hypothesis.' })];
      const ctl = h('div', { class: 'cross-ctl' });
      const run = async (body, label) => {
        if (crossBusy) return; crossBusy = true;
        ctl.querySelectorAll('button,select').forEach(b => b.disabled = true);
        status.textContent = label; status.classList.add('caret');
        try { complete(await api(vpath(v, '/crossbreed'), { method: 'POST', body: JSON.stringify(body) })); toast('Three hybrids. Pick the one that bothers you.'); }
        catch (e) { toast(e.message); status.textContent = ''; status.classList.remove('caret'); ctl.querySelectorAll('button,select').forEach(b => b.disabled = false); }
        crossBusy = false;
      };
      const status = h('p', { class: 'cross-status' });
      const sel = h('select', { class: 'cross-sel', 'aria-label': 'Pick a venture from the Floor' }, h('option', { value: '', text: 'Pick a venture from the Floor…' }));
      sel.addEventListener('focus', async () => {
        if (floorList) return;
        try { const d = await api('/api/marketplace'); floorList = [...d.marked, ...d.rising].filter(c => c.slug !== v.slug); floorList.forEach(c => sel.append(h('option', { value: c.slug, text: `${c.name} · ${c.tagline}` }))); } catch (_) {}
      });
      sel.addEventListener('change', () => { if (sel.value) run({ with_slug: sel.value }, `Splicing with ${sel.options[sel.selectedIndex].text.split(' · ')[0]}…`); });
      ctl.append(
        h('button', { class: 'btn small primary', text: 'Let the Architect pick', onclick: () => run({}, 'Reading the Floor for the best donor…') }),
        h('button', { class: 'btn small', text: 'From left field', onclick: () => run({ lateral: true }, 'Pulling a mechanism from somewhere unrelated…') }),
        sel);
      kids.push(ctl, status);
      if (sp && sp.splices && sp.splices.length) {
        const d = sp.donor || {};
        const donorEl = d.kind === 'venture'
          ? h('p', { class: 'cross-donor' }, 'Donor: ', ventureChip({ slug: d.slug, name: d.name, accent: d.accent }), h('span', { class: 'when', text: ` · ${fmtDate(sp.generated_at, true)}` }))
          : h('p', { class: 'cross-donor' }, 'Donor field: ', h('b', { text: d.name || '' }), h('span', { class: 'when', text: ` · ${fmtDate(sp.generated_at, true)}` }));
        kids.push(donorEl, h('div', { class: 'splices' }, ...sp.splices.map(x => spliceEl(x))));
        if ((sp.history || []).length) kids.push(h('p', { class: 'ground-note', text: `${sp.history.length} earlier crossbreed${sp.history.length === 1 ? '' : 's'} kept in your record.` }));
      }
      kids.push(h('p', { class: 'ground-note', text: 'Only you see these. "Manifest this" turns a hybrid into its own venture, sealed to you.' }));
      cross.replaceChildren(...kids); show(cross);
    }
    function spliceEl(x) {
      return h('article', { class: 'splice' },
        h('h3', { class: 's-name', text: x.name }),
        h('p', { class: 's-stole' }, h('b', { text: 'Stole' }), x.stole),
        h('p', { class: 's-hybrid', text: x.hybrid }),
        h('div', { class: 's-why' }, h('div', {}, h('b', { text: 'What could go wrong' }), x.why_not), h('div', {}, h('b', { text: 'Why it could work better or equal' }), x.why_it_does)),
        h('p', { class: 'ground-note', text: 'A proposed mechanism, not proof of performance. Test it against the normal way.' }),
        h('p', { class: 's-test' }, h('b', { text: 'First test' }), x.first_test),
        h('div', { class: 's-foot' }, h('button', { class: 'btn small primary', text: 'Manifest this →', onclick: () => manifestSentence(x.sentence) }),
          h('span', { class: 's-sentence', text: x.sentence })));
    }
    let archLoaded = false;
    async function loadArchitect(v) {
      try {
        const d = await api('/api/architect');
        const mine = (d.readings || []).filter(r => (r.slugs || []).includes(v.slug) || (r.ask_ids || []).some(id => (v.needs || []).some(n => n.id === id)));
        if (!mine.length) { if (d.pending) setTimeout(() => { archLoaded = false; if (venture) { archLoaded = true; loadArchitect(venture); } }, 6000); return; }
        archBox.replaceChildren(h('h2', { class: 'v-label', text: 'From the Architect' }),
          h('p', { class: 'v-arch-sub', text: `Seen from above the whole Floor, not just this page. Read ${fmtDate(d.generated_at, true)}.` }),
          h('div', { class: 'readings compact' }, ...mine.map(r => readingEl(r, d, { hideSlug: v.slug }))),
          h('p', { class: 'ground-note' }, h('a', { href: href('/architect'), 'data-route': '/architect', text: 'See everything the Architect sees →' })));
        show(archBox);
      } catch (_) {}
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
      if (allIn) renderStatus.classList.add('hidden');
      else if (v.images_status === 'ready') {
        renderStatus.classList.remove('hidden');
        renderStatus.textContent = 'Some renders are unavailable. Your venture is saved.';
        renderSlots.forEach((s, i) => { if (!(v.images || [])[i]) s.classList.add('done'); });
      }
      else if (v.images_status === 'failed') {
        renderStatus.classList.remove('hidden');
        renderStatus.textContent = (v.images || []).some(Boolean)
          ? 'Some renders could not finish. Your venture is saved.'
          : 'The image service could not finish these renders. Your venture is saved.';
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
        if (Date.now() - pollStart > 660000) {
          const note = refinement.querySelector('.refinement-note');
          if (venture.refinement?.state === 'pending' && note) note.textContent = 'Refinement is taking longer than usual. Refresh to check its progress.';
          else {
            renderStatus.classList.remove('hidden');
            renderStatus.textContent = 'Renders are taking longer than usual. Refresh in a minute.';
          }
          return;
        }
        try {
          const fresh = await api(`/api/ventures/${encodeURIComponent(venture.slug)}`, { signal: AbortSignal.timeout(10000) });
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
          h('p', { class: 'path-sub', text: 'The engine is writing your step-by-step guide. Steps appear as they land.' }),
          h('p', { class: 'ground-note', text: 'Unchecked draft while streaming. Do not begin construction or testing; qualified safety review is still required.' })),
        h('div', { class: 'psteps' }));
      show(path); roadmap.classList.add('hidden');
      path.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const wait = generationWait('path', text => {
        const note = path.querySelector('.path-sub');
        if (note) note.textContent = text;
      });
      try {
        const res = await fetch(API + vpath(v, '/path'), { method: 'POST', credentials: 'same-origin', signal: wait.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tier }) });
        if (!res.ok || !res.body) { const j = await res.json().catch(() => ({})); throw new Error(j.detail || j.message || 'The engine stalled.'); }
        const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
        while (true) {
          const { value, done } = await reader.read(); if (done) break;
          if (wait.cancelled) return;
          buf += dec.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
            const ev = parseSSE(block); if (!ev) continue;
            if (ev.event === 'partial') { wait.first(); paintPartialPath(ev.data.data, ev.data.streaming); }
            else if (ev.event === 'done') { wait.finish(); pathStreaming = false; complete(ev.data); toast('Your path is ready. Start with tonight.'); }
            else if (ev.event === 'error') throw new Error(ev.data.message);
          }
        }
        if (pathStreaming) throw new Error('The stream ended early.');
      } catch (e) {
        if (wait.cancelled) return;
        wait.finish('failed'); pathStreaming = false;
        const message = e.name === 'AbortError' ? 'The connection timed out. Refresh to check whether your path finished before retrying.' : e.message;
        toast(message); renderTierPicks(venture); renderPath(venture);
        path.after(h('p', { role: 'status', class: 'refinement-note', text: message }));
      } finally { wait.finish('interrupted'); wait.dispose(); }
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
    const archStrip = h('a', { class: 'arch-strip hidden', href: href('/architect'), 'data-route': '/architect' });
    const root = h('section', { class: 'view floor' },
      h('div', { class: 'floor-head' }, h('div', { class: 'eyebrow', text: 'The Floor' }),
        h('h2', { text: 'Ventures that earned their place.' }),
        h('p', { class: 'lead', text: 'Every venture here was born from one sentence, sealed to its creator, and released on purpose. The Marked ones crossed a real signal threshold.' })),
      h('div', { class: 'floor-section' }, h('h3', { class: 'v-label', text: 'Marked' }), marked),
      h('div', { class: 'floor-section' }, h('h3', { class: 'v-label', text: 'Rising' }), rising),
      h('div', { class: 'floor-section' }, h('h3', { class: 'v-label', text: 'Open asks' }), reqs),
      archStrip,
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
        api('/api/architect').then(a => {
          if (a.thin || !a.readings.length) return;
          archStrip.replaceChildren(h('div', { class: 'eyebrow', text: 'The Architect' }), h('div', { class: 'as-title', text: a.readings[0].title }),
            h('div', { class: 'as-sub', text: `${a.readings.length} reading${a.readings.length === 1 ? '' : 's'} across the Floor. See what no single page shows →` }));
          archStrip.classList.remove('hidden');
        }).catch(() => {});
        const rq = data.requests || [];
        reqs.replaceChildren(...(rq.length ? rq.map(req) : [h('p', { class: 'empty', text: 'No open asks right now. When a creator needs a shop, a part, or a person, it shows up here.' })]));
      } catch (_) {}
    };
    load(); timer = setInterval(load, 20000);
    root.cleanup = () => clearInterval(timer);
    return root;
  }

  // ---------- the architect ----------
  const KIND_LABEL = { convergence: 'Convergence', adjacency: 'Adjacency', pool: 'Pool it', echo: 'Echo', gap: 'Gap', stall: 'Stall' };
  function ventureChip(v) {
    const a = h('a', { class: 'vchip', href: href(`/v/${v.slug}`), 'data-route': `/v/${v.slug}` }, h('i'), v.name || v.slug);
    a.style.setProperty('--accent', v.accent || '#e8eeff');
    return a;
  }
  function readingEl(r, data, opts = {}) {
    const el = h('article', { class: `reading kind-${r.kind}` },
      h('div', { class: 'r-kind', text: KIND_LABEL[r.kind] || 'Reading' }),
      h('h3', { class: 'r-title', text: r.title }),
      h('p', { class: 'r-insight', text: r.insight }),
      r.action ? h('p', { class: 'r-action' }, h('b', { text: 'Tomorrow' }), r.action) : null);
    const chips = h('div', { class: 'r-evidence' });
    (r.slugs || []).forEach(s => { const v = data.ventures[s]; if (v && s !== opts.hideSlug) chips.append(ventureChip(v)); });
    (r.ask_ids || []).forEach(id => { const a = data.asks[id]; if (a) chips.append(h('a', { class: 'vchip ask', href: href(`/v/${a.venture.slug}`), 'data-route': `/v/${a.venture.slug}` }, h('i'), `Ask: ${a.title}`)); });
    if (chips.children.length) el.append(chips);
    return el;
  }
  function ArchitectView() {
    const counts = h('p', { class: 'arch-counts' });
    const status = h('p', { class: 'arch-status' });
    const list = h('div', { class: 'readings' });
    const crossSec = h('div', { class: 'arch-cross hidden' });
    const facts = h('div', { class: 'arch-facts hidden' });
    const root = h('section', { class: 'view architect' },
      h('div', { class: 'floor-head' }, h('div', { class: 'eyebrow', text: 'The Architect' }),
        h('h2', { text: 'What the Floor looks like from above.' }),
        h('p', { class: 'lead', text: 'Every creator sees their own venture. The Architect reads all of them at once: whose paths call for the same shop, whose buyers overlap, which asks one supplier could fill together, where the real signals are landing, and what is stalled.' }),
        counts, status),
        list, crossSec, facts,
        h('a', { class: 'arch-strip', href: href('/mix'), 'data-route': '/mix' },
          h('span', { class: 'eyebrow', text: 'A different kind of reading' }),
          h('div', { class: 'as-title', text: 'Mix live product signals with the Floor.' }),
          h('span', { class: 'as-sub', text: 'Bring evidence and real ventures into one new combination →' })));
    let timer = null;
    const fmtWhen = (iso) => iso ? fmtDate(iso, true) : '';
    const load = async () => {
      try {
        const d = await api('/api/architect');
        const c = d.counts;
        counts.replaceChildren(h('b', { text: String(c.ventures) }), ` venture${c.ventures === 1 ? '' : 's'} · `, h('b', { text: String(c.with_paths) }), ` on a path · `, h('b', { text: String(c.open_asks) }), ` open ask${c.open_asks === 1 ? '' : 's'} · `, h('b', { text: String(c.hands) }), ` hand${c.hands === 1 ? '' : 's'} raised · `, h('b', { text: String(c.signals) }), ` signal${c.signals === 1 ? '' : 's'}`);
        if (d.thin) {
          status.textContent = 'Not enough on the Floor to see a pattern yet. Patterns need at least two released ventures, or one open ask.';
          list.replaceChildren(h('p', { class: 'empty' }, 'Release a venture, pick a path, post an ask. The Architect starts seeing things when there are things to see. ', h('a', { href: href('/'), 'data-route': '/', text: 'Manifest one' }), '.'));
          facts.classList.add('hidden');
          return;
        }
        status.textContent = d.pending
          ? (d.readings.length ? `The Floor changed. The Architect is re-reading it… showing the last reading from ${fmtWhen(d.generated_at)}.` : 'The Architect is reading the Floor…')
          : `Read ${fmtWhen(d.generated_at)}. Counts are real rows. The prose is the engine’s. Verify before you act.`;
        status.classList.toggle('caret', !!d.pending);
        if (d.readings.length) list.replaceChildren(...d.readings.map(r => readingEl(r, d)));
        else if (!d.pending) list.replaceChildren(h('p', { class: 'empty', text: 'The Architect looked and found nothing worth saying yet. That is an honest answer.' }));
        renderCrosses(d);
        renderFacts(d);
        if (d.pending) { clearTimeout(timer); timer = setTimeout(load, 4000); }
      } catch (e) { status.textContent = e.message; }
    };
    function renderCrosses(d) {
      const cs = d.crossbreeds || [];
      if (!cs.length) { crossSec.classList.add('hidden'); return; }
      crossSec.replaceChildren(h('h3', { class: 'v-label', text: 'Crossbreeds' }),
        h('p', { class: 'arch-facts-sub', text: 'Two real ventures spliced. One mechanism borrowed from the donor, grafted onto the host. The hybrid is an untested concept, not a verified product or a grant of intellectual-property rights.' }),
        conceptDisclosure(),
        h('div', { class: 'splices' }, ...cs.map(c => {
          const host = d.ventures[c.host_slug] || { slug: c.host_slug, name: c.host_slug }, donor = d.ventures[c.donor_slug] || { slug: c.donor_slug, name: c.donor_slug };
          return h('article', { class: 'splice' },
            h('div', { class: 'r-evidence', style: { marginTop: '0', marginBottom: '14px' } }, ventureChip(host), h('span', { class: 'x', text: '×' }), ventureChip(donor)),
            h('h3', { class: 's-name', text: c.name }),
            h('p', { class: 's-stole' }, h('b', { text: 'Stole' }), c.stole),
            h('p', { class: 's-hybrid', text: c.hybrid }),
            h('p', { class: 's-test' }, h('b', { text: 'Why it could work better or equal' }), c.why_it_does),
            h('p', { class: 'ground-note', text: 'Proposed benefits are hypotheses unless supported by evidence.' }),
            h('div', { class: 's-foot' }, h('button', { class: 'btn small primary', text: 'Manifest this →', onclick: () => manifestSentence(c.sentence) }), h('span', { class: 's-sentence', text: c.sentence })));
        })));
      crossSec.classList.remove('hidden');
    }
    function renderFacts(d) {
      const p = d.patterns; const rows = [];
      const names = (slugs) => slugs.map(s => (d.ventures[s] || {}).name || s).join(', ');
      p.convergence.forEach(c => rows.push(['Same supplier world', `${c.family}: ${names(c.slugs)}`]));
      p.ask_pairs.forEach(a => rows.push(['Asks one shop could fill', `${a.family || a.shared.join(', ')}: ${names(a.slugs)}`]));
      p.echo.filter(e => e.buy + e.build + e.fund > 0).forEach(e => rows.push(['Signals landing', `${e.world}: ${e.buy} buy · ${e.build} build · ${e.fund} fund (${names(e.ventures)})`]));
      if (p.stalled_paths.length) rows.push(['Paths not moving', names(p.stalled_paths)]);
      if (p.unanswered_asks.length) rows.push(['Asks with no hands', p.unanswered_asks.map(id => (d.asks[id] || {}).title || id).join(' · ')]);
      p.gaps.forEach(g => rows.push(['Needed, not on the Floor', `${g.family} (needed by ${names(g.slugs)})`]));
      facts.replaceChildren(h('h3', { class: 'v-label', text: 'What it counted' }), h('p', { class: 'arch-facts-sub', text: 'The rows underneath the readings. Computed, not written.' }),
        h('dl', { class: 'facts' }, ...rows.flatMap(([k, v]) => [h('dt', { text: k }), h('dd', { text: v })])));
      facts.classList.toggle('hidden', rows.length === 0);
    }
    load();
    root.cleanup = () => clearTimeout(timer);
    return root;
  }

  // ---------- trend + Floor mixer ----------
  function MixView() {
    const trends = [];
    const floor = [];
    const selectedTrends = new Set();
    const selectedFloor = new Set();
    let researchId = null;
    let busy = false;

    const topic = h('input', { id: 'mix-topic', class: 'mix-topic', type: 'text', maxlength: '120', placeholder: 'e.g. quieter ways to manage household energy', 'aria-label': 'Product research topic', required: true });
    const researchStatus = h('p', { class: 'mix-status', role: 'status', 'aria-live': 'polite' });
    const trendList = h('div', { class: 'mix-trends', 'aria-label': 'Sourced product trends' });
    const floorList = h('div', { class: 'mix-floor-list', 'aria-label': 'Public Floor ventures' });
    const resultList = h('div', { class: 'mix-results', 'aria-live': 'polite' });
    const combineStatus = h('p', { class: 'mix-status', role: 'status', 'aria-live': 'polite' });
    const combineButton = h('button', { class: 'btn primary', type: 'button', text: 'Generate hybrids →', disabled: true });
    const customA = h('textarea', { id: 'mix-custom-a', class: 'mix-custom-input', rows: '3', maxlength: '600', placeholder: 'First idea, product, industry or mechanism…' });
    const customB = h('textarea', { id: 'mix-custom-b', class: 'mix-custom-input', rows: '3', maxlength: '600', placeholder: 'Second idea—something unexpected to borrow from…' });
    const customButton = h('button', { class: 'btn primary', type: 'button', text: 'Merge these two →', disabled: true });
    customA.addEventListener('input', updateEligibility);
    customB.addEventListener('input', updateEligibility);
    customButton.addEventListener('click', () => {
      if (!customButton.disabled) generateMix([customA.value.trim(), customB.value.trim()]);
    });
    const researchButton = h('button', { class: 'btn primary', type: 'submit', text: 'Research the live edge →' });
    const selectNote = h('p', { class: 'mix-select-note', text: 'Select 2–3 ingredients. Floor ventures are optional.' });
    const root = h('section', { class: 'view mix-page' },
      h('header', { class: 'mix-head' },
        h('div', { class: 'eyebrow', text: 'The Mix / 01' }),
        h('h1', {}, 'Steal a mechanism.', h('br'), 'Give it a new life.'),
        h('p', { class: 'lead', text: 'Merge two new ideas, borrow from live product research, or bring in a Floor venture. Find the useful collision; then give it a first test.' }),
        h('p', { class: 'mix-disclaimer', text: 'Sources support the ingredients, not the new hybrid’s performance. Floor signals are interest—not sales or proof of demand. Untested advantages are hypotheses; novelty and safety are not guaranteed.' })),
      conceptDisclosure(),
      h('section', { class: 'mix-custom', 'aria-labelledby': 'mix-custom-title' },
        h('h2', { id: 'mix-custom-title', text: 'Two new ideas. One unexpected merge.' }),
        h('p', { class: 'mix-help', text: 'No Floor venture or research required. Your inputs are concepts, not verified market evidence.' }),
        h('div', { class: 'mix-custom-fields' },
          h('label', { for: 'mix-custom-a' }, 'Idea one', customA),
          h('label', { for: 'mix-custom-b' }, 'Idea two', customB)),
        customButton),
      h('form', { class: 'mix-research', onsubmit: async (e) => {
        e.preventDefault();
        const value = topic.value.trim();
        if (!value || value.length > 120) { researchStatus.textContent = 'Enter a topic between 1 and 120 characters.'; topic.focus(); return; }
        busy = true;
        researchButton.disabled = true;
        topic.disabled = true;
        updateEligibility();
        researchStatus.textContent = 'Searching for sourced product mechanisms…';
        researchStatus.classList.add('caret');
        trendList.replaceChildren(h('div', { class: 'mix-skeleton' }, h('i'), h('i'), h('i')));
        root.querySelector('.mix-output').classList.add('hidden');
        resultList.replaceChildren();
        try {
          const data = await api('/api/mix/research', { method: 'POST', signal: AbortSignal.timeout(110000), body: JSON.stringify({ topic: value }) });
          researchId = data.research_id;
          trends.splice(0, trends.length, ...(Array.isArray(data.trends) ? data.trends : []));
          selectedTrends.clear();
          renderTrends(data);
          researchStatus.textContent = `Research captured ${fmtDate(data.researched_at, true)} · expires ${fmtDate(data.expires_at, true)}.`;
          researchStatus.classList.remove('caret');
          updateEligibility();
        } catch (error) {
          trends.splice(0);
          researchId = null;
          trendList.replaceChildren(h('div', { class: 'mix-error' }, h('b', { text: 'Research did not complete.' }), h('span', { text: error.message }), h('button', { class: 'btn small', type: 'button', text: 'Try again', onclick: () => researchButton.click() })));
          researchStatus.textContent = '';
        } finally {
          busy = false;
          researchButton.disabled = false;
          topic.disabled = false;
          updateEligibility();
        }
      } }, h('label', { for: 'mix-topic', text: 'What product territory should we search?' }),
      h('div', { class: 'mix-form-row' }, topic, researchButton),
      h('p', { class: 'mix-help', text: 'Research returns dated, linked evidence. Nothing is substituted if research fails.' }),
      researchStatus),
      h('div', { class: 'mix-columns' },
        h('section', { class: 'mix-panel' }, h('div', { class: 'mix-panel-head' }, h('span', { class: 'mix-index', text: 'A' }), h('div', {}, h('h2', { text: 'Product mechanisms' }), h('p', { text: 'Select up to three sourced trends.' }))), trendList),
        h('section', { class: 'mix-panel floor-panel' }, h('div', { class: 'mix-panel-head' }, h('span', { class: 'mix-index', text: 'B' }), h('div', {}, h('h2', { text: 'The public Floor' }), h('p', { text: 'Real ventures and their buy / build / fund signals.' }))), floorList)),
      h('div', { class: 'mix-action' }, selectNote, combineButton, combineStatus),
      h('section', { class: 'mix-output hidden' }, h('div', { class: 'mix-output-head' }, h('div', { class: 'eyebrow', text: 'The collision' }), h('h2', { text: 'Three ways this could exist.' }), h('p', { class: 'mix-output-date' })), resultList));

    function currentTotal() { return selectedTrends.size + selectedFloor.size; }
    function updateEligibility() {
      const count = currentTotal();
      combineButton.disabled = busy || (selectedTrends.size > 0 && !researchId) || count < 2 || count > 3;
      const a = customA.value.trim(), b = customB.value.trim();
      customButton.disabled = busy || a.length < 3 || b.length < 3 || a.toLowerCase() === b.toLowerCase();
      customA.disabled = busy; customB.disabled = busy;
      selectNote.textContent = !floor.length && count >= 1
        ? 'No public Floor ventures are available right now. You can still select multiple trends; this mix will have no Floor evidence.'
        : `Selected ${count} of 3 ingredients · Floor ventures are optional.`;
      root.querySelectorAll('.mix-choice').forEach(btn => {
        const isSelected = btn.getAttribute('aria-pressed') === 'true';
        btn.disabled = busy || (!isSelected && count >= 3);
      });
    }
    function toggle(set, id, btn) {
      if (set.has(id)) set.delete(id);
      else if (currentTotal() < 3) set.add(id);
      btn.setAttribute('aria-pressed', String(set.has(id)));
      btn.classList.toggle('selected', set.has(id));
      updateEligibility();
    }
    function sourcesEl(sources) {
      if (!Array.isArray(sources) || !sources.length) return h('span', { class: 'mix-no-sources', text: 'No source links returned.' });
      return h('div', { class: 'mix-sources' }, ...sources.map(source => {
        let valid = false;
        try { valid = new URL(source.url).protocol === 'https:' || new URL(source.url).protocol === 'http:'; } catch (_) {}
        return valid ? h('a', { href: source.url, target: '_blank', rel: 'noopener noreferrer', class: 'mix-source' }, source.title || source.url, h('span', { 'aria-hidden': 'true', text: ' ↗' }))
          : h('span', { class: 'mix-source unavailable', text: source.title || 'Source link unavailable' });
      }));
    }
    function renderTrends(data) {
      if (!trends.length) {
        trendList.replaceChildren(h('p', { class: 'mix-empty', text: 'Research returned no product trends. Try another topic; nothing has been invented to fill the gap.' }));
        return;
      }
      trendList.replaceChildren(...trends.map((trend, index) => {
        const button = h('button', { class: 'mix-choice trend-choice', type: 'button', 'aria-pressed': 'false', onclick: () => toggle(selectedTrends, trend.id, button) },
          h('span', { class: 'mix-choice-top' }, h('span', { class: 'mix-kind', text: `Trend ${String(index + 1).padStart(2, '0')}` }), h('span', { class: 'mix-date', text: trend.source_date || 'Date unknown' })),
          h('strong', { text: trend.title }),
          h('span', { class: 'mix-product', text: trend.product }),
          h('span', { class: 'mix-mechanism' }, h('b', { text: 'Mechanism' }), trend.mechanism),
          h('span', { class: 'mix-evidence' }, h('b', { text: 'Evidence' }), trend.evidence));
        return h('article', { class: 'mix-entry' }, button, sourcesEl(trend.sources));
      }));
      updateEligibility();
    }
    function renderFloor() {
      if (!floor.length) {
        floorList.replaceChildren(h('div', { class: 'mix-empty-floor' }, h('span', { class: 'mix-empty-mark', 'aria-hidden': 'true' }, '—'), h('p', { text: 'No public ventures on the Floor yet.' }), h('small', { text: 'Research and select multiple trends instead. No Floor evidence will be implied.' })));
        return;
      }
      floorList.replaceChildren(...floor.map((venture, index) => {
        const sig = venture.signals || {};
        const button = h('button', { class: 'mix-choice floor-choice', type: 'button', 'aria-pressed': 'false', onclick: () => toggle(selectedFloor, venture.slug, button) },
          h('span', { class: 'mix-choice-top' }, h('span', { class: 'mix-kind', text: `Floor / ${String(index + 1).padStart(2, '0')}` }), h('span', { class: 'mix-category', text: venture.category || 'Venture' })),
          h('strong', { text: venture.name }),
          h('span', { class: 'mix-product', text: venture.tagline }),
          h('span', { class: 'mix-evidence', text: venture.description }),
          h('span', { class: 'mix-signals' }, ...[['buy', 'Buy'], ['build', 'Build'], ['fund', 'Fund']].map(([key, label]) => h('span', {}, h('b', { text: String(sig[key] ?? '—') }), ` ${label}`))));
        const link = h('a', { class: 'mix-venture-link', href: href(`/v/${venture.slug}`), 'data-route': `/v/${venture.slug}` }, 'Open venture record →');
        return h('article', { class: 'mix-entry' }, button, link);
      }));
    }
    floorList.replaceChildren(h('div', { class: 'mix-skeleton' }, h('i'), h('i')));
    function renderConcepts(data) {
      const concepts = Array.isArray(data.concepts) ? data.concepts : [];
      const output = root.querySelector('.mix-output');
      output.classList.remove('hidden');
      output.querySelector('.mix-output-date').textContent = `Generated ${data.generated_at ? fmtDate(data.generated_at, true) : 'date unavailable'} · novelty is not guaranteed.`;
      const oldEvidence = output.querySelector('.mix-result-evidence');
      if (oldEvidence) oldEvidence.remove();
      resultList.replaceChildren(...concepts.map((concept, index) => {
        const sentence = String(concept.sentence || '').slice(0, 300);
        return h('article', { class: 'mix-concept' },
          h('div', { class: 'mix-concept-index', text: `0${index + 1}` }),
          h('h3', { text: concept.name }),
          h('p', { class: 'mix-stole' }, h('b', { text: 'The borrowed mechanism' }), concept.stole),
          h('p', { class: 'mix-hybrid', text: concept.hybrid }),
          h('div', { class: 'mix-details' },
            h('p', {}, h('b', { text: 'Buyer' }), concept.buyer),
            h('p', {}, h('b', { text: 'Why now' }), concept.why_now),
            h('p', { class: 'mix-comparison' }, h('b', { text: 'Why it could work better or equal' }),
              concept.why_it_does || 'Comparison unavailable for this older mix. Generate a new mix to see the reasoning.'),
            h('p', {}, h('b', { text: 'First test' }), concept.first_test),
            h('p', {}, h('b', { text: 'Closest existing' }), concept.closest_existing)),
          h('div', { class: 'mix-concept-foot' },
            h('span', { class: 'mix-sentence', text: sentence }),
            h('button', { class: 'btn primary small', type: 'button', text: 'Manifest this →', disabled: !sentence, onclick: () => manifestSentence(sentence, data.mix_id) })));
      }));
      if (!concepts.length) resultList.replaceChildren(h('p', { class: 'mix-empty', text: 'No concepts were returned. Try combining a different set of ingredients.' }));
      const floorNames = Array.isArray(data.floor) ? data.floor.map(v => v.name).filter(Boolean) : [];
      output.append(h('aside', { class: 'mix-result-evidence' },
        h('h3', { text: 'Evidence behind this mix' }),
        h('p', { text: 'Sources support the ingredients, not proof that this new combination performs better. Compare the hypothesis with the normal way in the first test.' }),
        floorNames.length
          ? h('p', { text: `Public Floor ventures included: ${floorNames.join(' · ')}. Signals are not proof of demand.` })
          : h('p', { text: 'No Floor evidence was included in this mix.' }),
        sourcesEl(data.sources)));
    }
    async function generateMix(customIdeas = null) {
      if (busy || (!customIdeas && combineButton.disabled)) return;
      busy = true;
      combineButton.disabled = true;
      updateEligibility();
      combineStatus.textContent = 'Asking the Architect to make the collision concrete…';
      root.querySelector('.mix-output').classList.remove('hidden');
      root.querySelector('.mix-output').scrollIntoView({ behavior: 'smooth', block: 'start' });
      const started = Date.now();
      const clock = setInterval(() => {
        combineStatus.textContent = `Exploring the mechanisms and testing the merge… ${Math.floor((Date.now() - started) / 1000)}s.`;
      }, 1000);
      combineStatus.classList.add('caret');
      resultList.replaceChildren(h('div', { class: 'mix-skeleton' }, h('i'), h('i'), h('i')));
      try {
        const data = await api('/api/mix/combine', { method: 'POST', signal: AbortSignal.timeout(110000), body: JSON.stringify(customIdeas ? { custom_ideas: customIdeas } : {
          research_id: researchId,
          trend_ids: [...selectedTrends],
          floor_slugs: [...selectedFloor],
        }) });
        renderConcepts(data);
        combineStatus.textContent = '';
        combineStatus.classList.remove('caret');
        root.querySelector('.mix-output').scrollIntoView({ behavior: 'smooth', block: 'start' });
      } catch (error) {
        combineStatus.classList.remove('caret');
        combineStatus.replaceChildren(h('span', { text: error.message }), ' ', h('button', { class: 'btn small', type: 'button', text: 'Try again', onclick: () => generateMix(customIdeas) }));
      } finally {
        clearInterval(clock);
        busy = false;
        updateEligibility();
      }
    }
    combineButton.addEventListener('click', () => generateMix());
    async function loadFloor() {
      floorList.replaceChildren(h('div', { class: 'mix-skeleton' }, h('i'), h('i')));
      try {
        const data = await api('/api/mix/floor');
        floor.splice(0, floor.length, ...(Array.isArray(data.ventures) ? data.ventures : []));
        renderFloor();
        updateEligibility();
      } catch (error) {
        floorList.replaceChildren(h('div', { class: 'mix-error' }, h('b', { text: 'The Floor could not be loaded.' }), h('span', { text: error.message }), h('button', { class: 'btn small', type: 'button', text: 'Retry Floor', onclick: loadFloor })));
      }
    }
    loadFloor();
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
      h('p', { class: 'lead', style: { marginTop: '18px' }, text: 'The hash provides a reference for checking the recorded inputs. The timestamp is recorded by MANIFEST, not independently certified. This record does not establish first invention, legal ownership or exclusive rights.' }),
      h('p', { class: 'lead', style: { marginTop: '18px' }, text: 'It is not a patent, a trademark, or a copyright registration. Public disclosure can affect intellectual-property rights; get qualified legal advice before releasing an idea you may want to protect.' }),
      h('section', { class: 'limitations', 'aria-labelledby': 'limitations-title' },
        h('h2', { id: 'limitations-title', text: 'Important limitations' }),
        h('p', { class: 'lead', text: 'MANIFEST generates speculative concepts, comparisons, plans and illustrations with AI. They may contain errors or omissions. A proposed mechanism explains why something might work; it is not evidence that the new product has been built, tested, certified or shown to outperform an alternative.' }),
        h('p', { class: 'lead', text: 'Where sources are supplied, they support only what those sources actually establish—not every claim about a new combination. Benefits, demand, prices, costs and timelines may be estimates or hypotheses. No result guarantees performance, safety, profit, originality, patentability or freedom from infringement.' }),
        h('p', { class: 'lead', text: 'Generated content is not professional legal, financial, engineering, medical or safety advice. Independently check claims, supplier information, intellectual-property rights and applicable requirements. Obtain qualified review before building or testing safety-critical products, publishing, selling or investing.' }),
        h('p', { class: 'lead', text: 'These notices explain the limits of the content; they are not a promise of protection from legal claims and do not remove rights or obligations under applicable law.' })),
      h('div', { style: { marginTop: '40px' } }, h('a', { class: 'btn', href: href('/'), 'data-route': '/', text: '← Back' })));
  }

  function NotFound(msg) {
    return h('section', { class: 'view notfound' },
      h('h2', { text: 'Nothing here yet.' }), h('p', { text: msg || 'No venture lives at this address.' }),
      h('a', { class: 'btn primary', href: href('/'), 'data-route': '/', text: 'Manifest one' }));
  }

  // ---------- start ----------
  render();
  resumeManifestRecovery();
  setTimeout(checkReveals, 50);
})();
