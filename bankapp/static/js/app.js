/* BluePass frontend. Talks to the Flask REST API; renders with DOM APIs only (no innerHTML),
   so user-provided text can never become markup. */
(() => {
  'use strict';

  // ------------------------------------------------------------------ helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function h(tag, props = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  function icon(name) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(SVG_NS, 'use');
    use.setAttribute('href', '#i-' + name);
    svg.append(use);
    return svg;
  }

  const meta = (name) => $(`meta[name="${name}"]`).content;
  let csrf = meta('csrf-token');
  const tzOffset = () => -new Date().getTimezoneOffset();

  async function api(path, { method = 'GET', body } = {}) {
    const res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON response */ }
    if (res.status === 401 && !path.startsWith('/api/auth/login')) {
      location.href = '/login?expired=1';
      throw new Error('Signed out');
    }
    if (!res.ok) {
      const err = new Error(data.error || 'Something went wrong. Please try again.');
      err.field = data.field; err.status = res.status;
      throw err;
    }
    return data;
  }

  const qs = (obj) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(obj)) if (v !== '' && v != null) p.set(k, v);
    return p.toString();
  };

  const inr = (paise) => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(paise / 100);
  const when = (s) => new Date(s.replace(' ', 'T') + 'Z');
  const fmtDate = (s) => when(s).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  const fmtTime = (s) => when(s).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  const fmtStamp = (s) => `${fmtDate(s)}, ${fmtTime(s)}`;
  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

  function toast(message, kind = '') {
    const t = h('div', { class: 'toast ' + kind, text: message });
    $('#toasts').append(t);
    setTimeout(() => t.remove(), 4500);
  }

  function clearErrors(form) {
    $$('.field-error', form).forEach((e) => (e.textContent = ''));
    $$('[aria-invalid]', form).forEach((e) => e.removeAttribute('aria-invalid'));
    const fe = $('.form-error', form);
    if (fe) fe.textContent = '';
  }

  function showError(form, err) {
    const target = err.field && $(`.field-error[data-for="${err.field}"]`, form);
    if (target) {
      target.textContent = err.message;
      const input = $(`[name="${err.field}"]`, form);
      if (input) { input.setAttribute('aria-invalid', 'true'); input.focus(); }
    } else {
      const fe = $('.form-error', form);
      if (fe) fe.textContent = err.message; else toast(err.message, 'bad');
    }
  }

  /** Wire a form: collects named inputs, disables the submit button while sending, shows errors. */
  function bindForm(form, handler) {
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      clearErrors(form);
      const data = Object.fromEntries(new FormData(form).entries());
      const btn = $('button[type="submit"]', form);
      if (btn) btn.disabled = true;
      try { await handler(data, form); } catch (err) { if (err.message !== 'Signed out') showError(form, err); }
      finally { if (btn) btn.disabled = false; }
    });
  }

  function tabs(loaders) {
    const buttons = $$('#tabs [data-tab]');
    const loaded = new Set();
    const show = (name) => {
      buttons.forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
      $$('[data-pane]').forEach((p) => (p.hidden = p.dataset.pane !== name));
      if (!loaded.has(name) && loaders[name]) { loaded.add(name); loaders[name](); }
    };
    buttons.forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));
    show(buttons[0].dataset.tab);
  }

  const entryTitle = (t) => t.category === 'deposit' ? 'Cash deposit'
    : t.category === 'withdrawal' ? 'Cash withdrawal'
    : (t.type === 'credit' ? 'From ' : 'To ') + (t.counterparty_name || 'Unknown');

  function ledger(items, emptyText = 'No transactions yet.') {
    if (!items.length) return h('p', { class: 'empty', text: emptyText });
    return h('div', { class: 'ledger', role: 'list' }, items.map((t) => {
      const credit = t.type === 'credit';
      return h('div', { class: 'entry', role: 'listitem' },
        h('div', { class: 'entry-date' }, fmtDate(t.created_at), h('span', { class: 'time', text: fmtTime(t.created_at) })),
        h('div', { class: 'entry-main' },
          h('strong', { text: entryTitle(t) }),
          t.note ? h('span', { class: 'sub', text: t.note }) : null,
          h('span', { class: 'ref', text: t.ref })),
        h('div', { class: 'entry-amt ' + (credit ? 'credit' : 'debit') },
          (credit ? '+' : '\u2212') + inr(t.amount), h('abbr', { title: credit ? 'Credit' : 'Debit', text: credit ? 'Cr' : 'Dr' })),
        h('div', { class: 'entry-bal' }, h('span', { class: 'lbl', text: 'Balance' }), inr(t.balance_after)));
    }));
  }

  function pager(root, page, pages, go) {
    root.replaceChildren();
    if (pages <= 1) return;
    root.append(
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', disabled: page <= 1, onclick: () => go(page - 1), text: 'Previous' }),
      h('span', { text: `Page ${page} of ${pages}` }),
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', disabled: page >= pages, onclick: () => go(page + 1), text: 'Next' }));
  }

  /** Inline "reason" prompt used for reject / freeze. */
  function askReason(row, label, onSubmit) {
    if ($('.reason-form', row)) return;
    const input = h('input', { type: 'text', maxlength: '120', 'aria-label': 'Reason', placeholder: 'Reason the customer will see' });
    const form = h('form', { class: 'reason-form filters', novalidate: true },
      h('div', { class: 'field grow' }, input),
      h('button', { class: 'btn btn-primary btn-sm', type: 'submit', text: label }),
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', text: 'Cancel', onclick: () => form.remove() }));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      try { await onSubmit(input.value.trim()); } catch (err) { toast(err.message, 'bad'); }
    });
    row.append(form);
    input.focus();
  }

  // -------------------------------------------------------------- shared setup
  async function refreshBell() {
    const bell = $('#bell-count');
    if (!bell) return;
    try {
      const { unread } = await api('/api/notifications');
      bell.hidden = unread === 0;
      bell.textContent = unread > 9 ? '9+' : String(unread);
    } catch (_) { /* ignore */ }
  }

  async function signOut(reason) {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) { /* already out */ }
    location.href = '/login' + (reason ? `?${reason}=1` : '');
  }

  function startIdleTimer() {
    if (!document.body.dataset.role) return;
    const ms = parseInt(meta('idle-minutes'), 10) * 60 * 1000;
    let timer;
    const reset = () => { clearTimeout(timer); timer = setTimeout(() => signOut('expired'), ms); };
    ['click', 'keydown', 'touchstart', 'pointerdown'].forEach((e) => document.addEventListener(e, reset, { passive: true }));
    reset();
  }

  // -------------------------------------------------------------------- pages
  const pages = {};

  pages.login = () => {
    const params = new URLSearchParams(location.search);
    const notice = $('#notice');
    if (params.get('expired')) { notice.textContent = 'You were signed out to keep your account safe. Sign in again to continue.'; notice.classList.add('warn'); notice.hidden = false; }
    if (params.get('registered')) { notice.textContent = 'Account created. Sign in to see its status.'; notice.hidden = false; }
    bindForm($('#login-form'), async (data) => {
      const res = await api('/api/auth/login', { method: 'POST', body: data });
      csrf = res.csrf;
      location.href = res.redirect;
    });
  };

  pages.register = () => {
    bindForm($('#register-form'), async (data) => {
      await api('/api/auth/register', { method: 'POST', body: data });
      location.href = '/login?registered=1';
    });
  };

  pages.dashboard = async () => {
    const hidden = () => localStorage.getItem('bp-hide-balance') === '1';
    let acct;
    const paint = () => {
      const pct = Math.min(100, Math.round((acct.daily_used / acct.daily_limit) * 100));
      const num = h('span', { class: 'num', text: hidden() ? '\u20b9 \u2022\u2022\u2022\u2022\u2022\u2022' : inr(acct.balance) });
      const toggle = h('button', { class: 'icon-btn', type: 'button', 'aria-label': hidden() ? 'Show balance' : 'Hide balance',
        onclick: () => { localStorage.setItem('bp-hide-balance', hidden() ? '0' : '1'); paint(); } }, icon(hidden() ? 'eye' : 'eye-off'));
      const copy = h('button', { class: 'pb-acct', type: 'button', 'aria-label': 'Copy account number', onclick: async () => {
        try { await navigator.clipboard.writeText(acct.account_number); toast('Account number copied'); } catch (_) { toast('Copy failed. Select the number manually.', 'bad'); }
      } }, acct.account_number, icon('copy'));
      $('#passbook').replaceChildren(
        h('div', { class: 'pb-top' }, h('span', { class: 'pb-name', text: acct.full_name }), copy),
        h('div', { class: 'pb-label', text: 'Available balance' }),
        h('div', { class: 'balance' }, num, toggle),
        h('div', { class: 'pb-limit' },
          h('span', { class: 'num', text: `${inr(acct.daily_used)} of ${inr(acct.daily_limit)} sent in the last 24 hours` }),
          h('div', { class: 'meter', role: 'presentation' }, h('span', { 'data-pct': pct }))));
      $('.meter > span').style.width = pct + '%';
    };
    try {
      acct = await api('/api/account');
      paint();
      const banner = $('#status-banner');
      const msgs = {
        pending: ['Your account is waiting for verification by bank staff. Sending money opens once it is verified.', ''],
        frozen: ['Your account is frozen. ' + (acct.status_reason || '') + ' Contact the bank to resolve this.', 'bad'],
        rejected: ['We could not verify your account. ' + (acct.status_reason || '') + ' Contact the bank for help.', 'bad'],
      };
      if (msgs[acct.status]) banner.append(h('div', { class: 'banner ' + msgs[acct.status][1], role: 'status', text: msgs[acct.status][0] }));
      const recent = await api('/api/transactions?per_page=5');
      $('#recent').replaceChildren(ledger(recent.items, 'No activity yet. Money you send or receive will show up here.'));
    } catch (err) { if (err.message !== 'Signed out') toast(err.message, 'bad'); }
  };

  pages.activity = () => {
    let page = 1;
    const load = async () => {
      const q = qs({ q: $('#q').value, type: $('#type').value, from: $('#from').value, to: $('#to').value, page, tz: tzOffset() });
      try {
        const r = await api('/api/transactions?' + q);
        $('#ledger').replaceChildren(ledger(r.items, 'Nothing matches these filters.'));
        pager($('#pager'), r.page, r.pages, (p) => { page = p; load(); window.scrollTo({ top: 0 }); });
      } catch (err) { if (err.message !== 'Signed out') toast(err.message, 'bad'); }
    };
    $('#filters').addEventListener('submit', (e) => { e.preventDefault(); page = 1; load(); });
    load();
  };

  pages.transfer = async () => {
    const form = $('#transfer-form'), confirm = $('#confirm-form'), receipt = $('#receipt');
    const acctInput = $('#to_account'), nameEl = $('#recipient-name');
    let recipient = null, limits = null, saved = [];

    async function lookup() {
      recipient = null; nameEl.hidden = true;
      const num = acctInput.value.trim();
      if (!/^\d{12}$/.test(num)) return;
      try {
        const r = await api('/api/lookup/' + num);
        recipient = { number: num, name: r.name };
        nameEl.textContent = r.name; nameEl.hidden = false;
        $('.field-error[data-for="to_account"]', form).textContent = '';
      } catch (err) { if (err.message !== 'Signed out') $('.field-error[data-for="to_account"]', form).textContent = err.message; }
    }
    acctInput.addEventListener('input', lookup);

    async function loadBeneficiaries() {
      const r = await api('/api/beneficiaries');
      saved = r.items;
      $('#saved-chips').replaceChildren(...saved.map((b) => h('button', { class: 'chip', type: 'button', text: b.nickname || b.name,
        onclick: () => { acctInput.value = b.account_number; lookup(); $('#amount').focus(); } })));
      $('#beneficiaries').replaceChildren(saved.length ? h('div', {}, saved.map((b) =>
        h('div', { class: 'list-row' },
          h('div', { class: 'meta' }, h('strong', { text: b.nickname ? `${b.nickname} (${b.name})` : b.name }), h('span', { class: 'sub num', text: b.account_number })),
          h('div', { class: 'list-actions' },
            h('button', { class: 'btn btn-ghost btn-sm', type: 'button', text: 'Send', onclick: () => { acctInput.value = b.account_number; lookup(); window.scrollTo({ top: 0 }); } }),
            h('button', { class: 'btn btn-danger btn-sm', type: 'button', 'aria-label': 'Remove ' + b.name, onclick: async () => {
              try { await api('/api/beneficiaries/' + b.id, { method: 'DELETE' }); loadBeneficiaries(); } catch (e) { toast(e.message, 'bad'); }
            } }, icon('trash'))))))
        : h('p', { class: 'empty', text: 'No saved recipients yet. Save one below to send faster next time.' }));
    }

    try {
      const a = await api('/api/account');
      limits = a;
      $('#limit-hint').textContent = `Up to ${inr(a.single_limit)} per transfer. You've sent ${inr(a.daily_used)} of ${inr(a.daily_limit)} today.`;
      if (a.status !== 'active') {
        form.replaceChildren(h('div', { class: 'banner', text: 'Sending money opens once bank staff have verified your account.' }));
      }
      await loadBeneficiaries();
    } catch (err) { if (err.message !== 'Signed out') toast(err.message, 'bad'); }

    bindForm(form, async (data) => {
      const num = (data.to_account || '').trim();
      if (!/^\d{12}$/.test(num)) throw Object.assign(new Error('Enter the 12-digit account number.'), { field: 'to_account' });
      if (!recipient || recipient.number !== num) await lookup();
      if (!recipient) throw Object.assign(new Error('We couldn\'t find an active account with that number.'), { field: 'to_account' });
      if (!/^\d{1,9}(\.\d{1,2})?$/.test((data.amount || '').trim()) || Number(data.amount) <= 0)
        throw Object.assign(new Error('Enter an amount like 250 or 250.50.'), { field: 'amount' });
      const paise = Math.round(Number(data.amount) * 100);
      $('#confirm-summary').replaceChildren(
        h('dt', { text: 'To' }), h('dd', { text: recipient.name }),
        h('dt', { text: 'Account' }), h('dd', { class: 'num', text: recipient.number }),
        h('dt', { text: 'Amount' }), h('dd', { class: 'num', text: inr(paise) }),
        ...(data.note ? [h('dt', { text: 'Note' }), h('dd', { text: data.note })] : []));
      form.hidden = true; confirm.hidden = false; $('#pin').focus();
    });

    $('#back-btn').addEventListener('click', () => { confirm.hidden = true; form.hidden = false; clearErrors(confirm); });

    bindForm(confirm, async (data) => {
      const res = await api('/api/transfer', { method: 'POST', body: {
        to_account: recipient.number, amount: $('#amount').value.trim(), note: $('#note').value.trim(), pin: data.pin } });
      confirm.hidden = true; confirm.reset(); form.reset(); nameEl.hidden = true;
      const recipientNumber = recipient.number;
      const alreadySaved = saved.some((b) => b.account_number === recipientNumber);
      receipt.replaceChildren(
        h('h2', { text: 'Money sent' }),
        h('p', { class: 'num', text: `${inr(res.amount)} to ${res.to_name}` }),
        h('p', { class: 'sub', text: `Reference ${res.ref}. New balance ${inr(res.balance)}.` }),
        h('div', { class: 'btn-row' },
          h('button', { class: 'btn btn-primary btn-sm', type: 'button', text: 'Send another', onclick: () => { receipt.hidden = true; form.hidden = false; } }),
          alreadySaved ? null : h('button', { class: 'btn btn-ghost btn-sm', type: 'button', text: 'Save recipient', onclick: async (e) => {
            try { await api('/api/beneficiaries', { method: 'POST', body: { account_number: recipientNumber } }); e.target.remove(); loadBeneficiaries(); toast('Recipient saved'); } catch (err) { toast(err.message, 'bad'); }
          } })));
      receipt.hidden = false;
      $('#limit-hint').textContent = '';
      toast('Money sent');
    });

    bindForm($('#bene-form'), async (data, f) => {
      await api('/api/beneficiaries', { method: 'POST', body: data });
      f.reset(); toast('Recipient saved'); loadBeneficiaries();
    });
  };

  pages.reports = () => {
    const preset = $('#preset');
    const range = () => {
      const now = new Date();
      if (preset.value === 'custom') return { from: $('#r-from').value, to: $('#r-to').value };
      const monthsBack = preset.value === 'month' ? 0 : parseInt(preset.value, 10) - 1;
      return { from: isoDay(new Date(now.getFullYear(), now.getMonth() - monthsBack, 1)), to: isoDay(now) };
    };
    preset.addEventListener('change', () => $$('[data-custom]').forEach((f) => (f.hidden = preset.value !== 'custom')));
    $('#print-btn').addEventListener('click', () => window.print());

    const catName = { transfer: 'Transfers', deposit: 'Cash deposits', withdrawal: 'Cash withdrawals' };
    const monthLabel = (m) => { const [y, mo] = m.split('-'); return new Date(+y, +mo - 1, 1).toLocaleDateString('en-IN', { month: 'short', year: '2-digit' }); };

    async function load() {
      const r = range();
      const query = qs({ ...r, tz: tzOffset() });
      $('#export-link').href = '/api/reports/statement.csv?' + query;
      try {
        const d = await api('/api/reports/summary?' + query);
        const max = Math.max(1, ...d.months.flatMap((m) => [m.credits, m.debits]));
        const bars = d.months.map((m) => {
          const c = h('div', { class: 'bar credit', title: `Money in ${inr(m.credits)}` });
          const dd = h('div', { class: 'bar debit', title: `Money out ${inr(m.debits)}` });
          c.style.height = Math.round((m.credits / max) * 100) + '%';
          dd.style.height = Math.round((m.debits / max) * 100) + '%';
          return h('div', { class: 'bar-group' }, h('div', { class: 'bar-pair' }, c, dd), h('span', { class: 'bar-label', text: monthLabel(m.month) }));
        });
        $('#report').replaceChildren(
          h('div', { class: 'stats' },
            h('div', { class: 'stat credit' }, h('div', { class: 'v', text: inr(d.credits) }), h('div', { class: 'k', text: 'Money in' })),
            h('div', { class: 'stat debit' }, h('div', { class: 'v', text: inr(d.debits) }), h('div', { class: 'k', text: 'Money out' })),
            h('div', { class: 'stat' }, h('div', { class: 'v', text: inr(d.credits - d.debits) }), h('div', { class: 'k', text: 'Net change' })),
            h('div', { class: 'stat' }, h('div', { class: 'v', text: String(d.count) }), h('div', { class: 'k', text: 'Entries' }))),
          h('section', { class: 'panel' }, h('h2', { text: 'Month by month' }),
            d.months.length ? h('div', {}, h('div', { class: 'bars', role: 'img', 'aria-label': 'Monthly money in and money out' }, bars),
              h('div', { class: 'legend' }, h('span', {}, h('i', { class: 'c' }), 'Money in'), h('span', {}, h('i', { class: 'd' }), 'Money out')))
              : h('p', { class: 'empty', text: 'No activity in this period.' })),
          h('section', { class: 'panel' }, h('h2', { text: 'By type' }),
            d.categories.length ? h('table', { class: 'data' },
              h('thead', {}, h('tr', {}, h('th', { text: 'Type' }), h('th', { text: 'Direction' }), h('th', { class: 'r', text: 'Entries' }), h('th', { class: 'r', text: 'Total' }))),
              h('tbody', {}, d.categories.map((c) => h('tr', {}, h('td', { text: catName[c.category] }), h('td', { text: c.type === 'credit' ? 'In' : 'Out' }),
                h('td', { class: 'r', text: String(c.n) }), h('td', { class: 'r', text: inr(c.total) })))))
              : h('p', { class: 'empty', text: 'Nothing to show.' })),
          h('section', { class: 'panel' }, h('h2', { text: 'Where you sent the most' }),
            d.top_recipients.length ? h('table', { class: 'data' },
              h('thead', {}, h('tr', {}, h('th', { text: 'Recipient' }), h('th', { class: 'r', text: 'Transfers' }), h('th', { class: 'r', text: 'Total' }))),
              h('tbody', {}, d.top_recipients.map((t) => h('tr', {}, h('td', { text: t.name }), h('td', { class: 'r', text: String(t.n) }), h('td', { class: 'r', text: inr(t.total) })))))
              : h('p', { class: 'empty', text: 'No transfers in this period.' })));
      } catch (err) { if (err.message !== 'Signed out') toast(err.message, 'bad'); }
    }
    $('#range-form').addEventListener('submit', (e) => { e.preventDefault(); load(); });
    load();
  };

  pages.notifications = async () => {
    const load = async () => {
      const r = await api('/api/notifications');
      $('#notes').replaceChildren(r.items.length ? h('div', {}, r.items.map((n) =>
        h('div', { class: 'note-item' + (n.is_read ? '' : ' unread') },
          h('strong', { text: n.title }), h('span', { text: n.body }), h('div', { class: 'sub', text: fmtStamp(n.created_at) }))))
        : h('p', { class: 'empty', text: 'No notifications yet.' }));
      return r;
    };
    try { await load(); } catch (err) { if (err.message !== 'Signed out') toast(err.message, 'bad'); }
    $('#mark-read').addEventListener('click', async () => {
      await api('/api/notifications/read', { method: 'POST' });
      await load(); refreshBell();
    });
  };

  pages.profile = async () => {
    try {
      const me = await api('/api/auth/me');
      const rows = [['Name', me.full_name], ['Email', me.email], ['Role', me.role]];
      if (me.phone) rows.push(['Mobile', me.phone]);
      if (me.account_number) rows.push(['Account number', me.account_number], ['Account status', me.account_status]);
      if (me.last_login) rows.push(['Last sign-in', fmtStamp(me.last_login)]);
      $('#me').replaceChildren(...rows.flatMap(([k, v]) => [h('dt', { text: k }), h('dd', { text: v })]));
    } catch (err) { if (err.message !== 'Signed out') toast(err.message, 'bad'); }
    $('#logout-btn').addEventListener('click', () => signOut());
    bindForm($('#pw-form'), async (data, f) => { await api('/api/auth/change-password', { method: 'POST', body: data }); f.reset(); toast('Password updated'); });
    const pinForm = $('#pin-form');
    if (pinForm) bindForm(pinForm, async (data, f) => { await api('/api/auth/change-pin', { method: 'POST', body: data }); f.reset(); toast('PIN updated'); });
  };

  const statusTag = (s) => h('span', { class: 'tag ' + s, text: s });

  pages.staff = () => {
    async function loadPending() {
      const r = await api('/api/staff/pending');
      $('#pending').replaceChildren(r.items.length ? h('div', {}, r.items.map((p) => {
        const row = h('div', { class: 'list-row' },
          h('div', { class: 'meta' }, h('strong', { text: p.full_name }),
            h('span', { class: 'sub', text: `${p.email}, ${p.phone || 'no phone'}` }),
            h('span', { class: 'sub', text: `Applied ${fmtStamp(p.created_at)}. Account ${p.account_number}` })),
          h('div', { class: 'list-actions' },
            h('button', { class: 'btn btn-primary btn-sm', type: 'button', text: 'Verify', onclick: async () => {
              try { await api(`/api/staff/accounts/${p.id}/review`, { method: 'POST', body: { action: 'approve' } }); toast('Account verified'); loadPending(); } catch (e) { toast(e.message, 'bad'); }
            } }),
            h('button', { class: 'btn btn-danger btn-sm', type: 'button', text: 'Reject', onclick: () => askReason(row, 'Reject account', async (reason) => {
              await api(`/api/staff/accounts/${p.id}/review`, { method: 'POST', body: { action: 'reject', reason } }); toast('Account rejected'); loadPending();
            }) })));
        return row;
      })) : h('p', { class: 'empty', text: 'No accounts are waiting for verification.' }));
    }

    async function searchCustomers(q = '') {
      const r = await api('/api/staff/customers?' + qs({ q }));
      $('#customers').replaceChildren(r.items.length ? h('div', {}, r.items.map((c) => {
        const row = h('div', { class: 'list-row' },
          h('div', { class: 'meta' }, h('strong', {}, c.full_name, statusTag(c.status)),
            h('span', { class: 'sub', text: c.email }), h('span', { class: 'sub num', text: `${c.account_number}, balance ${inr(c.balance)}` })),
          h('div', { class: 'list-actions' },
            c.status === 'active' ? h('button', { class: 'btn btn-danger btn-sm', type: 'button', text: 'Freeze', onclick: () => askReason(row, 'Freeze account', async (reason) => {
              await api(`/api/staff/accounts/${c.id}/review`, { method: 'POST', body: { action: 'freeze', reason } }); toast('Account frozen'); searchCustomers(q);
            }) }) : null,
            c.status === 'frozen' ? h('button', { class: 'btn btn-ghost btn-sm', type: 'button', text: 'Unfreeze', onclick: async () => {
              try { await api(`/api/staff/accounts/${c.id}/review`, { method: 'POST', body: { action: 'unfreeze' } }); toast('Account active again'); searchCustomers(q); } catch (e) { toast(e.message, 'bad'); }
            } }) : null,
            c.status === 'active' ? h('button', { class: 'btn btn-ghost btn-sm', type: 'button', text: 'Use at counter', onclick: () => {
              $('#t-acct').value = c.account_number; $('[data-tab="teller"]').click(); $('#t-amt').focus();
            } }) : null));
        return row;
      })) : h('p', { class: 'empty', text: 'No customers match that search.' }));
    }

    bindForm($('#teller-form'), async (data, f) => {
      const r = await api('/api/staff/teller', { method: 'POST', body: data });
      toast(`Posted for ${r.name}. New balance ${inr(r.balance)}. Ref ${r.ref}`);
      $('#t-amt').value = ''; $('#t-note').value = '';
    });
    $('#cust-search').addEventListener('submit', (e) => { e.preventDefault(); searchCustomers($('#c-q').value); });
    tabs({ verify: loadPending, customers: () => searchCustomers('') });
  };

  pages.admin = () => {
    async function loadStats() {
      const s = await api('/api/admin/stats');
      const card = (k, v, cls = '') => h('div', { class: 'stat ' + cls }, h('div', { class: 'v', text: v }), h('div', { class: 'k', text: k }));
      $('#stats').replaceChildren(card('Customers', String(s.customers)), card('Bank staff', String(s.staff)), card('Admins', String(s.admins)),
        card('Waiting for verification', String(s.pending)), card('Frozen accounts', String(s.frozen)),
        card('Total deposits', inr(s.deposits)), card('Transactions in 24 hours', String(s.tx_24h)), card('Failed sign-ins in 24 hours', String(s.failed_logins_24h), s.failed_logins_24h > 10 ? 'debit' : ''));
    }

    async function loadUsers() {
      const r = await api('/api/admin/users?' + qs({ q: $('#u-q').value, role: $('#u-role').value }));
      $('#users').replaceChildren(r.items.length ? h('div', {}, r.items.map((u) => {
        const locked = (u.locked_until && when(u.locked_until) > new Date()) || (u.pin_locked_until && when(u.pin_locked_until) > new Date());
        return h('div', { class: 'list-row' },
          h('div', { class: 'meta' }, h('strong', {}, u.full_name, h('span', { class: 'tag', text: u.role }), u.is_active ? null : h('span', { class: 'tag off', text: 'deactivated' }), locked ? h('span', { class: 'tag pending', text: 'locked' }) : null),
            h('span', { class: 'sub', text: u.email }), u.last_login ? h('span', { class: 'sub', text: 'Last sign-in ' + fmtStamp(u.last_login) }) : null),
          h('div', { class: 'list-actions' },
            locked ? h('button', { class: 'btn btn-ghost btn-sm', type: 'button', text: 'Unlock', onclick: async () => { await api(`/api/admin/users/${u.id}/unlock`, { method: 'POST' }); toast('Unlocked'); loadUsers(); } }) : null,
            h('button', { class: 'btn ' + (u.is_active ? 'btn-danger' : 'btn-ghost') + ' btn-sm', type: 'button', text: u.is_active ? 'Deactivate' : 'Activate', onclick: async () => {
              try { await api(`/api/admin/users/${u.id}/status`, { method: 'POST', body: { active: !u.is_active } }); loadUsers(); } catch (e) { toast(e.message, 'bad'); }
            } })));
      })) : h('p', { class: 'empty', text: 'No users match.' }));
    }

    let logPage = 1;
    async function loadLogs() {
      const r = await api('/api/admin/logs?' + qs({ q: $('#l-q').value, page: logPage }));
      $('#logs').replaceChildren(r.items.length ? h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, h('th', { text: 'Time' }), h('th', { text: 'Who' }), h('th', { text: 'Action' }), h('th', { text: 'Detail' }), h('th', { text: 'IP' }))),
        h('tbody', {}, r.items.map((l) => h('tr', {}, h('td', { text: fmtStamp(l.created_at) }), h('td', { text: l.actor || '-' }), h('td', { text: l.action }), h('td', { text: l.detail || '' }), h('td', { text: l.ip || '' })))))
        : h('p', { class: 'empty', text: 'No log entries match.' }));
      pager($('#log-pager'), r.page, r.pages, (p) => { logPage = p; loadLogs(); });
    }

    bindForm($('#user-form'), async (data, f) => { await api('/api/admin/users', { method: 'POST', body: data }); f.reset(); toast('User created'); loadUsers(); loadStats(); });
    $('#user-search').addEventListener('submit', (e) => { e.preventDefault(); loadUsers(); });
    $('#log-search').addEventListener('submit', (e) => { e.preventDefault(); logPage = 1; loadLogs(); });
    tabs({ overview: loadStats, users: loadUsers, logs: loadLogs });
  };

  // --------------------------------------------------------------------- boot
  document.addEventListener('DOMContentLoaded', () => {
    startIdleTimer();
    refreshBell();
    const page = document.body.dataset.page;
    if (pages[page]) pages[page]();
  });
})();
