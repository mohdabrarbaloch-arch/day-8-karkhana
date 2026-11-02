/* Karkhana — frontend.
   One file, no build step. All text from the API goes through textContent or
   is escaped, never innerHTML, so a job named "<img onerror=...>" is just a
   job with a funny name. */

'use strict';

const TOKEN_KEY = 'karkhana.token';
const USER_KEY = 'karkhana.user';

const state = {
  token: localStorage.getItem(TOKEN_KEY) || null,
  user: JSON.parse(localStorage.getItem(USER_KEY) || 'null'),
  view: 'cluster',
  jobs: [],
};

const $ = (id) => document.getElementById(id);

/* ─────────────────────────── api ─────────────────────────── */

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function api(path, { method = 'GET', body = null, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth && state.token) headers.Authorization = `Bearer ${state.token}`;

  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === null ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError('Cannot reach the server. Is it still running?', 0);
  }

  if (response.status === 401 && auth) {
    signOut(true);
    throw new ApiError('Your session expired. Please sign in again.', 401);
  }

  let payload = null;
  const type = response.headers.get('content-type') || '';
  if (type.includes('application/json')) {
    payload = await response.json().catch(() => null);
  }

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    if (payload && payload.detail) {
      message = Array.isArray(payload.detail)
        ? payload.detail.map((d) => d.msg || String(d)).join('; ')
        : String(payload.detail);
    }
    throw new ApiError(message, response.status);
  }
  return payload;
}

/* ─────────────────────────── helpers ─────────────────────────── */

function toast(message, isError = false) {
  const el = $('toast');
  el.textContent = message;
  el.classList.toggle('is-error', isError);
  el.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => {
    el.hidden = true;
  }, 4200);
}

function money(value) {
  return '$' + Number(value || 0).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function runtime(minutes, status) {
  if (!minutes) return status === 'queued' ? '—' : '<1 min';
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function pill(status) {
  return el('span', `pill pill-${status}`, status);
}

/* ─────────────────────────── auth ─────────────────────────── */

function setSession(token, user) {
  state.token = token;
  state.user = user;
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

function signOut(silent = false) {
  state.token = null;
  state.user = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  $('app').hidden = true;
  $('view-login').hidden = false;
  if (!silent) toast('Signed out.');
}

async function handleLogin(event) {
  event.preventDefault();
  const button = $('login-btn');
  const error = $('login-error');
  error.hidden = true;
  button.disabled = true;
  button.textContent = 'Signing in…';

  try {
    const result = await api('/api/auth/login', {
      method: 'POST',
      auth: false,
      body: { email: $('login-email').value.trim(), password: $('login-password').value },
    });
    setSession(result.access_token, result.user);
    await enterApp();
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = 'Sign in';
  }
}

async function enterApp() {
  $('view-login').hidden = true;
  $('app').hidden = false;
  $('who').textContent = `${state.user.email} · ${state.user.role} · ${state.user.team}`;
  await Promise.all([loadDashboard(), loadJobs()]);
}

/* ─────────────────────────── views ─────────────────────────── */

function switchView(name) {
  state.view = name;
  document.querySelectorAll('.tab').forEach((tab) => {
    const active = tab.dataset.view === name;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('.view').forEach((view) => {
    const active = view.id === `view-${name}`;
    view.classList.toggle('is-active', active);
    view.hidden = !active;
  });
  if (name === 'jobs') loadJobs();
  if (name === 'billing') loadBilling();
  if (name === 'cluster') loadDashboard();
}

/* ─────────────────────────── dashboard ─────────────────────────── */

async function loadDashboard() {
  let data;
  try {
    data = await api('/api/dashboard');
  } catch (err) {
    toast(err.message, true);
    return;
  }

  $('s-util').textContent = `${data.utilisation_percent}%`;
  $('s-util-bar').style.width = `${Math.min(100, data.utilisation_percent)}%`;

  $('s-alloc').textContent = `${data.allocated_gpus} / ${data.total_gpus}`;
  $('s-alloc-foot').textContent =
    `${data.free_gpus} free · ${data.healthy_nodes}/${data.nodes} nodes healthy`;

  $('s-queued').textContent = data.jobs_queued;
  $('s-queued-foot').textContent = `${data.jobs_running} running · ${data.jobs_completed} done`;

  $('s-cost').textContent = money(data.cost_today);
  $('s-cost-foot').textContent = `${data.gpu_hours_today} GPU-hours today`;

  const list = $('node-list');
  list.replaceChildren();

  if (!data.per_node.length) {
    list.append(el('p', 'empty', 'No nodes registered yet.'));
  }

  for (const node of data.per_node) {
    const row = el('div', `node-row${node.healthy ? '' : ' is-dead'}`);

    const identity = el('div');
    identity.append(el('div', 'node-name', node.name));
    identity.append(
      el('div', 'node-meta', `${node.gpu_model} · ${node.region}${node.healthy ? '' : ' · OFFLINE'}`)
    );

    const bar = el('div', 'gpu-bar');
    for (let i = 0; i < node.total_gpus; i += 1) {
      bar.append(el('span', `gpu-cell${i < node.allocated_gpus ? ' is-used' : ''}`));
    }

    const count = el(
      'div',
      'node-count',
      `${node.allocated_gpus}/${node.total_gpus} · ${node.utilisation_percent}%`
    );

    row.append(identity, bar, count);
    list.append(row);
  }

  await loadQueue();
}

async function loadQueue() {
  const container = $('queue-list');
  let queue;
  try {
    queue = await api('/api/scheduler/queue');
  } catch {
    return;
  }

  container.replaceChildren();
  if (!queue.length) {
    container.append(el('p', 'empty', 'Queue is empty — everything submitted is running or finished.'));
    return;
  }

  const byId = new Map(state.jobs.map((job) => [job.id, job]));
  queue.forEach((entry, index) => {
    const job = byId.get(entry.job_id);
    const title = job ? job.name : `Job #${entry.job_id}`;

    const row = el('div', 'queue-row');
    // Show the real job id, not the loop index. The scheduler log refers to
    // "Job #8", so a positional "#1" here read as a contradiction.
    row.append(el('div', 'queue-pos', `#${entry.job_id}`));

    const middle = el('div');
    middle.append(el('div', null, title));
    middle.append(el('div', 'queue-reason', `position ${index + 1} · ${entry.reason}`));
    row.append(middle);

    row.append(pill('queued'));
    container.append(row);
  });
}

/* ─────────────────────────── jobs ─────────────────────────── */

async function loadJobs() {
  const filter = $('job-filter').value;
  const query = filter ? `?status=${encodeURIComponent(filter)}` : '';
  let jobs;
  try {
    jobs = await api(`/api/jobs${query}`);
  } catch (err) {
    toast(err.message, true);
    return;
  }
  state.jobs = jobs;
  renderJobs(jobs);
}

function renderJobs(jobs) {
  const tbody = $('job-rows');
  tbody.replaceChildren();
  $('jobs-empty').hidden = jobs.length > 0;

  for (const job of jobs) {
    const row = document.createElement('tr');

    const nameCell = document.createElement('td');
    nameCell.append(el('div', 'cell-name', job.name));
    nameCell.append(
      el('div', 'cell-sub', `${job.project} · priority ${job.priority} · ${job.span}`)
    );

    const gpuCell = el('td', 'cell-mono', `${job.gpus}× ${job.gpu_model || 'any'}`);
    const statusCell = document.createElement('td');
    statusCell.append(pill(job.status));
    const runtimeCell = el('td', 'cell-mono', runtime(job.runtime_minutes, job.status));
    const whyCell = el('td', 'cell-why', job.last_reason || '—');

    const actionCell = el('td', 'cell-action');
    if (job.status === 'queued' || job.status === 'running') {
      const button = el('button', 'btn btn-ghost btn-sm', 'Cancel');
      button.type = 'button';
      button.addEventListener('click', () => cancelJob(job));
      actionCell.append(button);
    }

    if (job.status === 'running') {
      const finish = el('button', 'btn btn-ghost btn-sm', 'Finish');
      finish.type = 'button';
      finish.style.marginLeft = '6px';
      finish.addEventListener('click', () => finishJob(job));
      actionCell.append(finish);
    }

    row.append(nameCell, gpuCell, statusCell, runtimeCell, whyCell, actionCell);
    tbody.append(row);
  }
}

async function cancelJob(job) {
  try {
    await api(`/api/jobs/${job.id}/cancel`, { method: 'POST' });
    toast(`Cancelled "${job.name}".`);
    await Promise.all([loadJobs(), loadDashboard()]);
  } catch (err) {
    toast(err.message, true);
  }
}

async function finishJob(job) {
  try {
    await api(`/api/jobs/${job.id}/complete?exit_code=0`, { method: 'POST' });
    toast(`"${job.name}" finished.`);
    await Promise.all([loadJobs(), loadDashboard()]);
  } catch (err) {
    toast(err.message, true);
  }
}

async function submitJob(event) {
  event.preventDefault();
  const error = $('job-error');
  const button = $('job-submit');
  error.hidden = true;
  button.disabled = true;

  const payload = {
    name: $('j-name').value.trim(),
    gpus: Number($('j-gpus').value),
    gpu_model: $('j-model').value || null,
    span: $('j-span').value,
    priority: Number($('j-priority').value),
    duration_minutes: Number($('j-duration').value),
    command: $('j-command').value.trim(),
  };

  try {
    const job = await api('/api/jobs', { method: 'POST', body: payload });
    toast(`Queued "${job.name}" for ${job.gpus} GPU(s). Run a scheduler pass on the Cluster tab.`);
    $('job-form').reset();
    $('j-gpus').value = 2;
    $('j-priority').value = 5;
    $('j-duration').value = 60;
    await Promise.all([loadJobs(), loadDashboard()]);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
}

/* ─────────────────────────── scheduler ─────────────────────────── */

async function runScheduler() {
  const button = $('run-scheduler');
  const output = $('sched-result');
  button.disabled = true;
  button.textContent = 'Running…';

  try {
    const result = await api('/api/scheduler/run', { method: 'POST' });
    output.replaceChildren();
    output.hidden = false;

    output.append(el('h3', null, result.message));

    for (const decision of result.scheduled) {
      const line = el('div', 'sched-line');
      line.append(el('span', 'tag tag-scheduled', 'started'));
      line.append(
        el('span', null, `Job #${decision.job_id} → ${decision.node} (${decision.gpus} GPU, ${decision.reason})`)
      );
      output.append(line);
    }

    for (const deferral of result.deferred) {
      const line = el('div', 'sched-line');
      line.append(el('span', 'tag tag-deferred', 'waiting'));
      line.append(el('span', null, `Job #${deferral.job_id}: ${deferral.reason}`));
      output.append(line);
    }

    for (const blocked of result.unplaceable) {
      const line = el('div', 'sched-line');
      line.append(el('span', 'tag tag-unplaceable', 'cannot fit'));
      line.append(el('span', null, `Job #${blocked.job_id}: ${blocked.reason}`));
      output.append(line);
    }

    if (result.reservation_minutes !== null && result.reservation_minutes !== undefined) {
      const line = el('div', 'sched-line');
      line.append(el('span', 'tag tag-deferred', 'reserved'));
      line.append(
        el(
          'span',
          null,
          `Head job #${result.head_job_id} holds a reservation ${result.reservation_minutes} min out — backfill may only run inside that window.`
        )
      );
      output.append(line);
    }

    toast(result.message);
    await Promise.all([loadDashboard(), loadJobs()]);
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = 'Run pass';
  }
}

async function resetScheduler() {
  try {
    const result = await api('/api/scheduler/reset', { method: 'POST' });
    toast(result.message);
    await Promise.all([loadDashboard(), loadJobs()]);
  } catch (err) {
    toast(err.message, true);
  }
}

/* ─────────────────────────── billing ─────────────────────────── */

async function loadBilling() {
  const days = $('billing-days').value;
  let report;
  try {
    report = await api(`/api/dashboard/chargeback?days=${days}`);
  } catch (err) {
    toast(err.message, true);
    return;
  }

  const tbody = $('billing-rows');
  tbody.replaceChildren();
  $('billing-empty').hidden = report.teams.length > 0;

  for (const team of report.teams) {
    const row = document.createElement('tr');
    row.append(el('td', 'cell-name', team.team));
    row.append(el('td', 'cell-mono', team.jobs));
    row.append(el('td', 'cell-mono', team.gpu_hours.toFixed(2)));
    row.append(el('td', 'cell-mono', money(team.cost)));
    tbody.append(row);
  }

  $('billing-total').replaceChildren(
    el('span', null, `${report.total_gpu_hours.toFixed(2)} GPU-hours`),
    el('strong', null, money(report.total_cost))
  );
}

/* ─────────────────────────── boot ─────────────────────────── */

function bind() {
  $('login-form').addEventListener('submit', handleLogin);
  $('logout-btn').addEventListener('click', () => signOut());
  $('job-form').addEventListener('submit', submitJob);
  $('job-filter').addEventListener('change', loadJobs);
  $('billing-days').addEventListener('change', loadBilling);
  $('run-scheduler').addEventListener('click', runScheduler);
  $('reset-scheduler').addEventListener('click', resetScheduler);

  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => switchView(tab.dataset.view));
  });
}

async function boot() {
  bind();
  if (!state.token) return;
  try {
    const user = await api('/api/auth/me');
    state.user = user;
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    await enterApp();
  } catch {
    signOut(true);
  }
}

document.addEventListener('DOMContentLoaded', boot);
