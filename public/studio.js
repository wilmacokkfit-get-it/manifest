/* MANIFEST Studio. Generated HTML is only ever assigned to sandboxed srcdoc.
   All model text in the parent UI is inserted through the existing text builder. */
(() => {
  'use strict';
  window.ManifestStudio = function ({ api, h, href, projectId, seed }) {
    const root = h('section', { class: 'app-studio', 'data-testid': 'studio' });
    let project = null, busy = null, error = '', disposed = false, poll = null;
    let idea = seed?.idea || '', change = '', selected = null, projects = null, libraryOpen = false;
    const controller = new AbortController();
    const labels = {
      scout: 'Searching the web for the closest tools.',
      diverge: 'Finding three genuinely different directions.',
      build: 'Building a complete app for your chosen direction.',
      autopsy: 'Reviewing generic design, features and missing states. Applying fixes.',
      mutate: 'Updating the current app with your change.',
    };
    const link = (url, text, id) => h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', text, 'data-testid': id });
    const button = (text, id, action, disabled = false, primary = false) => h('button', {
      class: `btn ${primary ? 'primary' : 'ghost'}`, type: 'button',
      text, 'data-testid': id, disabled, onclick: action,
    });
    const note = text => h('p', { class: 'studio-note', text });
    function isPending() {
      return project?.payload.operation && Date.parse(project.payload.operation.lease_until) > Date.now();
    }
    async function call(path, body, method = 'POST') {
      return api(path, { method, body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(180000)]) });
    }
    function apply(result) {
      if (disposed) return;
      project = result;
      idea = result.idea;
      selected = result.payload.selected_id || selected;
      history.replaceState({}, '', href(`/studio/${project.id}`));
      paint();
    }
    async function refresh() {
      if (!project || disposed) return;
      try { apply(await call(`/api/studio/projects/${project.id}`, null, 'GET')); }
      catch (err) { if (!disposed) { error = err.message; paint(); } }
    }
    async function listProjects() {
      libraryOpen = true; projects = null; paint();
      try {
        const result = await call('/api/studio/projects', null, 'GET');
        if (!disposed) { projects = result.projects; paint(); }
      } catch (err) {
        if (!disposed) { projects = []; error = err.message; paint(); }
      }
    }
    async function run(kind) {
      if (busy || isPending() || disposed) return false;
      busy = kind; error = ''; paint();
      try {
        const result = await call(`/api/studio/projects/${project.id}/${kind}`, {
          revision: project.revision, request_id: crypto.randomUUID(),
          direction_id: selected, change: kind === 'mutate' ? change.trim() : '',
        });
        if (disposed) return false;
        if (kind === 'mutate') change = '';
        apply(result);
        return true;
      } catch (err) {
        if (!disposed) {
          error = err.name === 'AbortError' || err.name === 'TimeoutError'
            ? 'The connection ended before confirmation. Check the saved result before retrying.'
            : err.message;
          await refresh();
        }
        return false;
      } finally { if (!disposed) { busy = null; paint(); } }
    }
    async function scout() {
      if (busy || idea.trim().length < 8 || isPending()) return;
      if (!project) {
        busy = 'saving'; error = ''; paint();
        try {
          apply(await call('/api/studio/projects', {
            id: crypto.randomUUID(), idea: idea.trim(), venture_slug: seed?.venture_slug || null,
          }));
        } catch (err) { error = err.message; busy = null; paint(); return; }
        busy = null;
      }
      if (await run('scout')) await run('diverge');
    }
    async function build() {
      if (!selected) return;
      if (await run('build')) await run('autopsy');
    }
    async function restore(id) {
      if (busy || isPending()) return;
      busy = 'restoring'; error = ''; paint();
      try { apply(await call(`/api/studio/projects/${project.id}/restore`, { revision: project.revision, version_id: id })); }
      catch (err) { error = err.message; await refresh(); }
      finally { busy = null; paint(); }
    }
    function section(title, subtitle, ...content) {
      return h('section', { class: 'studio-section' },
        h('div', { class: 'studio-section-head' }, h('h2', { text: title }), note(subtitle)), ...content);
    }
    function paint() {
      if (disposed) return;
      clearTimeout(poll);
      const p = project?.payload;
      const pending = isPending();
      const disabled = !!busy || !!pending;
      const current = p?.versions[p.cursor];
      const direction = p?.directions.find(d => d.id === (p.selected_id || selected));
      const status = busy ? labels[busy] || (busy === 'loading' ? 'Opening your saved project.' : busy === 'restoring' ? 'Restoring your saved version.' : 'Saving your project.') : pending
        ? `${labels[p.operation.kind]} The server is still working; this view checks its saved result.`
        : project ? 'Project and retained versions saved privately.' : 'Private by default. Built for your intended use.';
      const libraryButton = button(libraryOpen ? 'Close projects' : 'Your projects', 'button-projects',
        () => { if (libraryOpen) { libraryOpen = false; paint(); } else listProjects(); }, disabled);
      const intro = h('header', { class: 'studio-intro' },
        h('div', {}, h('p', { class: 'studio-kicker', text: 'MANIFEST / THE STUDIO' }),
          h('h1', { text: 'Not another version of everything else.' }),
          h('p', { text: 'Scout the familiar. Choose your difference. Bring it to life.' })),
        h('div', { class: 'studio-actions' }, libraryButton,
          project ? h('a', { class: 'btn ghost', href: href('/studio'), 'data-route': '/studio', text: 'New idea', 'data-testid': 'link-new-project' }) : null));
      const steps = h('ol', { class: 'studio-steps', 'aria-label': 'App creation workflow' },
        ...Object.keys(labels).map((kind, index) => {
          const done = kind === 'scout' ? p?.matches.length : kind === 'diverge' ? p?.directions.length :
            kind === 'build' ? current : kind === 'autopsy' ? p?.autopsy === 'complete' :
              p?.versions.some(v => v.label !== 'Initial build' && v.label !== 'Autopsy fixes');
          return h('li', { class: busy === kind || (pending && p.operation.kind === kind) ? 'active' : done ? 'done' : '',
            'aria-current': busy === kind ? 'step' : null },
          h('span', { text: `0${index + 1}` }), kind.toUpperCase());
        }));
      const statusEl = h('p', { class: 'studio-status', role: 'status', 'aria-live': 'polite', text: status, 'data-testid': 'status-studio' });
      const children = [intro, steps, statusEl];
      if (error || p?.error) children.push(h('div', { class: 'studio-error', role: 'alert', 'data-testid': 'status-error' },
        h('p', { text: error || p.error }),
        project ? button('Check saved result', 'button-refresh', refresh, !!busy) : null));
      if (libraryOpen) {
        children.push(section('YOUR PROJECTS', 'Private to the same creator identity used by MANIFEST.',
          projects === null ? note('Loading saved projects…') : projects.length ? h('ul', { class: 'studio-project-list' },
            ...projects.map(item => h('li', {}, h('a', {
              href: href(`/studio/${item.id}`), 'data-route': `/studio/${item.id}`,
              'data-testid': `link-project-${item.id}`,
            }, h('span', { text: item.idea }), h('time', { text: new Date(item.updated_at).toLocaleDateString() }), '↗'))))
            : note('No saved app projects are available. Storage errors appear above; no local fallback is used.')));
      }
      const input = h('textarea', { id: 'studio-idea', maxlength: '6000', rows: '3',
        'data-testid': 'input-studio-idea', placeholder: 'An idea that deserves something made for it…',
        disabled: disabled || !!project, oninput: e => { idea = e.target.value; submit.disabled = disabled || idea.trim().length < 8; } });
      input.value = idea;
      const submit = h('button', { class: 'btn primary', type: 'submit', text: 'Breathe Life →',
        'data-testid': 'button-scout', disabled: disabled || idea.trim().length < 8 || !!p?.matches.length });
      children.push(h('form', { class: 'studio-prompt', onsubmit: e => { e.preventDefault(); scout(); } },
        h('label', { for: 'studio-idea', class: 'sr-only', text: 'Describe the app you want to create' }), input,
        h('div', { class: 'studio-prompt-bottom' }, note(project && !p.matches.length ? 'Retry this saved idea, or start a new one.' : 'First, see what already exists.'), submit)));
      if (p?.matches.length) {
        children.push(section('SCOUT', `The closest matches from live web search · ${new Date(p.researched_at).toLocaleDateString()}. AI-ranked, not exhaustive.`,
          h('ul', { class: 'studio-matches' }, ...p.matches.map((m, i) => h('li', { 'data-testid': `match-${i}` },
            link(m.url, m.name + ' ↗', `link-match-${i}`),
            h('p', {}, m.summary, ' ', link(m.source_url, 'Search evidence ↗', `link-evidence-${i}`))))),
          !p.directions.length ? button('Find three directions', 'button-diverge', () => run('diverge'), disabled, true) : null));
      }
      if (p?.directions.length) {
        children.push(section('DIVERGE', 'Three different mechanisms, not three coats of paint. Choose one.',
          h('div', { class: 'studio-directions' }, ...p.directions.map(d =>
            button('', `button-direction-${d.id}`, () => { selected = d.id; paint(); }, disabled || !!current))),
          !current ? h('div', { class: 'studio-build-action' }, note('Your choice becomes one complete, working HTML app.'),
            button('Build this direction →', 'button-build', build, disabled || !selected, true)) : null));
        const options = children.at(-1).querySelector('.studio-directions');
        p.directions.forEach((d, i) => {
          const option = options.children[i]; option.className = 'studio-direction' + ((selected || p.selected_id) === d.id ? ' selected' : '');
          option.setAttribute('aria-pressed', String((selected || p.selected_id) === d.id));
          option.append(h('span', { class: 'studio-direction-index', text: d.id.toUpperCase() }),
            h('span', {}, h('strong', { text: d.title }), h('span', { class: 'studio-direction-pitch', text: d.pitch }),
              h('span', { class: 'studio-direction-difference' }, h('b', { text: 'The difference: ' }), d.difference)),
            h('span', { class: 'studio-direction-check', text: (selected || p.selected_id) === d.id ? '✓' : '↗', 'aria-hidden': 'true' }));
        });
      }
      const canvas = h('div', { class: 'studio-canvas' });
      if (current) {
        const frame = h('iframe', { title: 'Your generated app', sandbox: 'allow-scripts',
          referrerpolicy: 'no-referrer', 'data-testid': 'iframe-studio-preview' });
        frame.srcdoc = current.html;
        canvas.append(frame);
      } else {
        canvas.append(h('div', { class: 'studio-empty' }, h('h3', { text: busy === 'build' ? 'Your direction is taking shape.' : 'A blank canvas. A different possibility.' }),
          note('A real AI-generated app will appear here. No saved templates stand in for a result.')));
      }
      children.push(section('THE CANVAS', direction?.title || 'One chosen direction. One creation.', canvas,
        current ? note(`Version ${p.cursor + 1} of ${p.versions.length} retained. Scripts run in an isolated iframe with no same-origin access to MANIFEST.`) : null));
      if (current) {
        const report = current.label === 'Autopsy fixes' ? current :
          p.versions.slice(0, p.cursor + 1).filter(v => v.label === 'Autopsy fixes').at(-1);
        children.push(section('AUTOPSY', 'A second AI pass: generic design, broken features, missing states.',
          p.autopsy !== 'complete' ? h('div', { class: 'studio-review-pending' },
            note('The initial app is shown. Its review has not completed.'),
            button('Run Autopsy', 'button-autopsy', () => run('autopsy'), disabled, true)) :
            report ? h('div', {}, report.fixes.length ? h('ul', { class: 'studio-fixes', 'data-testid': 'list-studio-fixes' },
              ...report.fixes.map(fix => h('li', {}, h('span', { class: 'studio-fix-category', text: fix.category }),
                h('span', {}, h('span', { text: fix.fixed }), h('small', { text: 'Was: ' + fix.issue }))))) :
              note('The review reported no changes. It did not execute browser tests.')) :
              note('This is an earlier version, before the retained review.'),
          note('This is AI code review, not proof that every feature works. Try the main actions.')));
        const mutation = h('textarea', { id: 'studio-change', maxlength: '6000', rows: '2',
          'data-testid': 'input-studio-change', placeholder: 'Keep everything, but change…',
          disabled, oninput: e => { change = e.target.value; mutateButton.disabled = disabled || !change.trim(); } });
        mutation.value = change;
        const mutateButton = h('button', { class: 'btn primary', type: 'submit', text: 'Mutate →',
          'data-testid': 'button-mutate', disabled: disabled || !change.trim() });
        children.push(section('MUTATE', 'Keep what works. Change what does not.',
          h('div', { class: 'studio-version-actions' }, button('Undo', 'button-undo',
            () => restore(p.versions[p.cursor - 1].id), disabled || p.cursor <= 0)),
          h('form', { class: 'studio-prompt', onsubmit: e => { e.preventDefault(); if (change.trim()) run('mutate'); } },
            h('label', { for: 'studio-change', class: 'sr-only', text: 'Describe your change request' }), mutation,
            h('div', { class: 'studio-prompt-bottom' }, note('Current HTML + your words. Full updated app returned.'), mutateButton)),
          h('ol', { class: 'studio-versions', 'aria-label': 'Last five retained versions' },
            ...p.versions.map((v, index) => h('li', {}, button(`V${index + 1} · ${v.label}`, `button-version-${index}`,
              () => restore(v.id), disabled, index === p.cursor)))),
          note('The last five versions are saved. Changing an older version replaces its later branch. Preview form data is not included in HTML history.')));
      }
      root.replaceChildren(...children);
      if (pending && !busy) poll = setTimeout(refresh, 4000);
    }
    root.cleanup = () => { disposed = true; clearTimeout(poll); controller.abort(); };
    paint();
    if (projectId) {
      busy = 'loading'; paint();
      call(`/api/studio/projects/${encodeURIComponent(projectId)}`, null, 'GET')
        .then(apply).catch(err => { if (!disposed) error = err.message; })
        .finally(() => { if (!disposed) { busy = null; paint(); } });
    }
    return root;
  };
})();
