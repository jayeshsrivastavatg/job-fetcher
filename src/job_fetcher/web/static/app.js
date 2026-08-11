(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];

  function toast(message, kind = 'info') {
    const el = $('#toast');
    if (!el) return;
    el.textContent = message;
    el.className = `toast show ${kind}`;
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.className = 'toast', 3600);
  }

  async function api(url, options = {}) {
    const opts = { ...options };
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    const response = await fetch(url, opts);
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) {
      const err = new Error(body.detail || body.error || `Request failed (${response.status})`);
      err.status = response.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  async function startRun(kind, companyId = null) {
    try {
      const body = companyId ? { company_ids: [companyId] } : {};
      const result = await api(`/api/runs/${kind}`, { method: 'POST', body: JSON.stringify(body) });
      location.href = `/runs/${result.run_id}`;
    } catch (err) {
      if (err.status === 409 && err.body?.run_id) {
        toast('Another operation is already running. Opening it now.', 'warn');
        setTimeout(() => location.href = `/runs/${err.body.run_id}`, 650);
        return;
      }
      toast(err.message, 'error');
    }
  }

  $$('[data-start-run]').forEach(btn => btn.addEventListener('click', () => startRun(btn.dataset.startRun)));
  $$('[data-company-run]').forEach(btn => btn.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    startRun(btn.dataset.companyRun, btn.dataset.companyId);
  }));

  async function setEnabled(companyId, enabled) {
    try {
      await api(`/api/companies/${encodeURIComponent(companyId)}/enabled`, {
        method: 'POST', body: JSON.stringify({ enabled })
      });
      toast(enabled ? 'Company enabled.' : 'Company disabled.', 'success');
      setTimeout(() => location.reload(), 350);
    } catch (err) { toast(err.message, 'error'); }
  }
  $$('[data-set-enabled]').forEach(btn => btn.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    setEnabled(btn.dataset.companyId, btn.dataset.setEnabled === 'true');
  }));

  // Dialog helpers
  $$('[data-open-dialog]').forEach(btn => btn.addEventListener('click', () => {
    const dlg = document.getElementById(btn.dataset.openDialog);
    if (dlg?.showModal) dlg.showModal();
  }));
  $$('[data-close-dialog]').forEach(btn => btn.addEventListener('click', () => btn.closest('dialog')?.close()));
  $$('dialog').forEach(dlg => dlg.addEventListener('click', e => {
    if (e.target === dlg) dlg.close();
  }));

  // Company source form fields. Known provider fields are generated from the
  // shared backend schema; uncommon nested objects use a JSON textarea.
  const schema = window.JF_SOURCE_SCHEMA || { fields: {}, types: [] };
  const multiline = new Set(['field_mapping', 'selectors']);
  function renderSourceFields(container, sourceType, initial = {}) {
    if (!container) return;
    const fields = schema.fields?.[sourceType] || [];
    container.innerHTML = '';
    if (!fields.length) {
      container.innerHTML = '<p class="muted form-help">No additional fields are required for this source.</p>';
      return;
    }
    fields.forEach(key => {
      const label = document.createElement('label');
      const title = key.replaceAll('_', ' ').replace(/\b\w/g, x => x.toUpperCase());
      const value = initial[key];
      label.innerHTML = `<span>${title}</span>`;
      let input;
      if (multiline.has(key)) {
        input = document.createElement('textarea');
        input.rows = 4;
        input.placeholder = '{ }';
        if (value !== undefined) input.value = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
      } else {
        input = document.createElement('input');
        input.placeholder = key === 'entry_url' ? 'Defaults to career URL when supported' : '';
        if (value !== undefined && value !== null) input.value = typeof value === 'object' ? JSON.stringify(value) : String(value);
      }
      input.dataset.sourceKey = key;
      label.appendChild(input);
      container.appendChild(label);
    });
  }

  $$('[data-source-select]').forEach(select => {
    const form = select.closest('form');
    const container = $('[data-source-fields]', form);
    let initial = {};
    if (container?.dataset.initial) {
      try { initial = JSON.parse(container.dataset.initial); } catch (_) {}
    }
    renderSourceFields(container, select.value, initial);
    select.addEventListener('change', () => renderSourceFields(container, select.value, {}));
  });

  function collectSourceConfig(form) {
    const out = {};
    $$('[data-source-key]', form).forEach(input => {
      const raw = input.value.trim();
      if (!raw) return;
      if (raw.startsWith('{') || raw.startsWith('[')) {
        try { out[input.dataset.sourceKey] = JSON.parse(raw); }
        catch (_) { out[input.dataset.sourceKey] = raw; }
      } else if (/^\d+$/.test(raw) && ['max_pages','max_jobs'].includes(input.dataset.sourceKey)) {
        out[input.dataset.sourceKey] = Number(raw);
      } else {
        out[input.dataset.sourceKey] = raw;
      }
    });
    return out;
  }

  const addForm = $('#add-company-form');
  if (addForm) addForm.addEventListener('submit', async e => {
    e.preventDefault();
    const error = $('[data-form-error]', addForm); error.textContent = '';
    const fd = new FormData(addForm);
    const payload = {
      name: fd.get('name'), career_url: fd.get('career_url'), source_type: fd.get('source_type'),
      source_config: collectSourceConfig(addForm), enabled: fd.get('enabled') === 'on', verify: true,
    };
    try {
      const result = await api('/api/companies', { method: 'POST', body: JSON.stringify(payload) });
      if (result.run_id) location.href = `/companies/${result.company.id}?run=${result.run_id}`;
      else {
        if (result.conflict_run_id) toast('Company added. Verification will need to run after the active operation finishes.', 'warn');
        location.href = `/companies/${result.company.id}`;
      }
    } catch (err) { error.textContent = err.message; }
  });

  const editForm = $('#edit-company-form');
  if (editForm) editForm.addEventListener('submit', async e => {
    e.preventDefault();
    const error = $('[data-form-error]', editForm); error.textContent = '';
    const fd = new FormData(editForm);
    const payload = {
      name: fd.get('name'), career_url: fd.get('career_url'), source_type: fd.get('source_type'),
      source_config: collectSourceConfig(editForm),
      rank: fd.get('rank') ? Number(fd.get('rank')) : null,
    };
    try {
      await api(`/api/companies/${encodeURIComponent(editForm.dataset.companyId)}`, { method: 'PATCH', body: JSON.stringify(payload) });
      toast('Company configuration saved.', 'success');
      setTimeout(() => location.reload(), 300);
    } catch (err) { error.textContent = err.message; }
  });

  // Disable confirmation preserves data/history.
  let disableCompanyId = null;
  const disableDialog = $('#disable-company-dialog');
  $$('[data-confirm-disable]').forEach(btn => btn.addEventListener('click', () => {
    disableCompanyId = btn.dataset.companyId;
    $('[data-disable-name]', disableDialog).textContent = btn.dataset.companyName;
    disableDialog.showModal();
  }));
  $('[data-disable-confirm]', disableDialog || document.createElement('div'))?.addEventListener('click', async () => {
    if (!disableCompanyId) return;
    disableDialog.close();
    await setEnabled(disableCompanyId, false);
  });


  async function runRelevanceAnalysis(recomputeAll = false) {
    try {
      toast(recomputeAll ? 'Recomputing relevance scores…' : 'Analyzing new / changed jobs…', 'info');
      const result = await api('/api/relevance/analyze', {
        method: 'POST', body: JSON.stringify({ all: recomputeAll })
      });
      toast(`Relevance analysis complete: ${result.analyzed_this_run} analyzed, ${result.relevant_jobs} relevant jobs.`, 'success');
      setTimeout(() => location.reload(), 650);
    } catch (err) { toast(err.message, 'error'); }
  }
  $$('[data-analyze-relevance]').forEach(btn => btn.addEventListener('click', () => runRelevanceAnalysis(false)));
  $$('[data-recompute-relevance]').forEach(btn => btn.addEventListener('click', () => runRelevanceAnalysis(true)));

  // Settings
  const settingsForm = $('#settings-form');
  if (settingsForm) settingsForm.addEventListener('submit', async e => {
    e.preventDefault();
    const fd = new FormData(settingsForm);
    const error = $('[data-form-error]', settingsForm); error.textContent = '';
    const payload = {
      fetch_workers: Number(fd.get('fetch_workers')),
      http_timeout: Number(fd.get('http_timeout')),
      retries: Number(fd.get('retries')),
      browser_fallback: fd.get('browser_fallback') === 'on',
      browser_workers: Number(fd.get('browser_workers')),
      verification_drop_threshold: Number(fd.get('verification_drop_threshold_pct')) / 100,
      verify_sample_detail: fd.get('verify_sample_detail') === 'on',
      detail_timeout: Number(fd.get('detail_timeout')),
    };
    try {
      await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
      toast('Settings saved and applied to future runs.', 'success');
    } catch (err) { error.textContent = err.message; }
  });

  // Row navigation without stealing clicks from controls.
  $$('.clickable[data-href]').forEach(row => row.addEventListener('click', e => {
    if (e.target.closest('a,button,details,summary,input,select')) return;
    location.href = row.dataset.href;
  }));

  // Render timestamps in the browser's local timezone.
  $$('.local-time').forEach(el => {
    const raw = el.getAttribute('datetime');
    if (!raw) { el.textContent = '—'; return; }
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) { el.textContent = raw; return; }
    el.textContent = new Intl.DateTimeFormat(undefined, {
      year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit'
    }).format(d);
    el.title = d.toLocaleString();
  });

  $$('[data-duration-start]').forEach(el => {
    const start = new Date(el.dataset.durationStart || '');
    const end = new Date(el.dataset.durationEnd || Date.now());
    if (Number.isNaN(start.getTime())) return;
    const sec = Math.max(0, Math.floor((end - start) / 1000));
    if (sec < 60) el.textContent = `${sec}s`;
    else if (sec < 3600) el.textContent = `${Math.floor(sec/60)}m ${sec%60}s`;
    else el.textContent = `${Math.floor(sec/3600)}h ${Math.floor((sec%3600)/60)}m`;
  });

  if (location.hash === '#edit') $('#edit-company-dialog')?.showModal();
})();
