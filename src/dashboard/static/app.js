'use strict';
const $ = (id) => document.getElementById(id);
let csrf = '', settings = null, busy = false;
const money = (value) => value == null || value === '' || !Number.isFinite(Number(value)) ? '—' : 'Rp ' + Number(value).toLocaleString('id-ID', {maximumFractionDigits:2});
const number = (value) => value == null ? '—' : Number(value).toLocaleString('id-ID', {maximumFractionDigits:8});
const date = (value) => { if (value == null) return '—'; const d = new Date(value); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('id-ID', {dateStyle:'medium', timeStyle:'short'}); };
async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (response.status === 401) { location.reload(); throw new Error('Sesi berakhir.'); }
  if (!response.ok) throw new Error(data.error || 'Permintaan gagal.');
  return data;
}
function showView(name) {
  document.querySelectorAll('.view').forEach(el => el.classList.toggle('hidden', el.id !== 'view-' + name));
  document.querySelectorAll('.nav-item').forEach(el => {
    const active = el.dataset.view === name;
    el.classList.toggle('active', active);
    if (active) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  });
  const labels = {home:'Ringkasan', positions:'Posisi', history:'Riwayat', settings:'Pengaturan', control:'Kontrol'};
  $('crumb-title').textContent = labels[name];
}
document.querySelectorAll('[data-view]').forEach(el => el.addEventListener('click', () => showView(el.dataset.view)));
document.querySelectorAll('[data-go]').forEach(el => el.addEventListener('click', () => showView(el.dataset.go)));
const statusNames = {active:'Service aktif', inactive:'Service berhenti', failed:'Service gagal', activating:'Service memulai', deactivating:'Service sedang berhenti', unknown:'Status tidak diketahui'};
function drawStatus(s) {
  $('equity').textContent = money(s.equity); $('cash').textContent = money(s.cash);
  $('last-updated').textContent = date(s.equity_updated_at) + (s.equity_updated_at ? ' · waktu lokal' : ' (belum ada data)');
  $('open-count').textContent = s.positions.length; $('nav-positions').textContent = s.positions.length;
  $('order-count').textContent = s.orders; $('positions-badge').textContent = s.positions.length + ' POSISI';
  $('service-status').textContent = statusNames[s.service] || statusNames.unknown; $('control-service').textContent = $('service-status').textContent;
  $('status-dot').classList.toggle('green', s.service === 'active');
  $('kill-status').textContent = s.killed ? 'AKTIF · review manual' : 'Tidak aktif';
  $('daily-status').textContent = s.daily_halted ? 'AKTIF · entry diblokir' : 'Tidak aktif';
  $('pause-status').textContent = s.stopping !== '0' ? 'Stop tertunda / aktif' : s.paused ? 'Entry dijeda' : 'Entry tidak dijeda';
  const tbody = $('positions-table'); tbody.replaceChildren();
  for (const p of s.positions) appendRow(tbody, [p.symbol, number(p.amount), money(p.entry_price), money(p.stop), money(p.target)]);
  $('positions-empty').classList.toggle('hidden', s.positions.length > 0);
  const list = $('activity'); list.replaceChildren();
  const events = s.events.slice().reverse();
  if (!events.length) { const e = document.createElement('div'); e.className = 'empty'; e.textContent = 'Belum ada aktivitas di log yang tersedia.'; list.append(e); }
  for (const item of events) {
    const row = document.createElement('div'); row.className = 'activity-row';
    const icon = document.createElement('span'); icon.className = 'activity-icon'; icon.textContent = item.event.includes('reject') || item.event.includes('error') || item.event === 'kill_switch' ? '!' : '•';
    const body = document.createElement('div'); const title = document.createElement('strong'); title.textContent = item.event.replaceAll('_', ' ');
    const detail = document.createElement('small'); detail.textContent = [item.symbol, item.reason, item.error_type, item.equity_idr != null ? money(item.equity_idr) : null].filter(Boolean).join(' · ') || 'Paper bot'; body.append(title, detail);
    const time = document.createElement('time'); time.textContent = date(item.time); row.append(icon, body, time); list.append(row);
  }

}
function appendRow(tbody, values) { const tr = document.createElement('tr'); for (const value of values) { const td = document.createElement('td'); td.textContent = value; tr.append(td); } tbody.append(tr); }
function drawHistory(orders) { const tbody = $('history-table'); tbody.replaceChildren(); for (const o of orders) appendRow(tbody, [date(o.timestamp_ms), o.symbol, o.side === 'buy' ? 'BELI' : 'JUAL', number(o.amount), money(o.price), money(o.fee_tax), money(o.pnl)]); $('history-empty').classList.toggle('hidden', orders.length > 0); }
const fields = {
 risk: [['risk_per_trade','Risiko per trade (%)',100],['daily_loss_limit','Batas rugi harian (%)',100],['max_drawdown','Max drawdown (%)',100]],
 strategy: [['ema_fast','EMA cepat',1],['ema_slow','EMA lambat',1],['ema_trend','EMA tren',1],['rsi_period','Periode RSI',1],['rsi_min','RSI minimum',1],['rsi_max','RSI maksimum',1],['atr_period','Periode ATR',1],['atr_min','ATR minimum',1],['atr_stop_multiplier','ATR stop multiplier',1],['risk_reward','Risk / reward',1]]
};
function drawSettings(data) {
  settings = data; $('pair').textContent = data.pair.join(', '); $('timeframe').textContent = data.timeframe;
  for (const group of ['risk','strategy']) {
    const container = $(group + '-fields'); container.replaceChildren();
    for (const [key, label, scale] of fields[group]) {
      const wrapper = document.createElement('label'); wrapper.textContent = label;
      const input = document.createElement('input'); input.type = 'number'; input.step = 'any'; input.required = true; input.name = group + '.' + key; input.value = String(Number((data[group][key] * scale).toPrecision(12))); wrapper.append(input); container.append(wrapper);
    }
  }
}
async function refresh(all = false) {
  if (busy) return; busy = true;
  try { const [s,h] = await Promise.all([api('/api/status'),api('/api/history')]); drawStatus(s); drawHistory(h.orders); if (all || !settings) drawSettings(await api('/api/settings')); $('global-error').classList.add('hidden'); }
  catch (error) { $('global-error').textContent = error.message || 'Gagal memperbarui data.'; $('global-error').classList.remove('hidden'); }
  finally { busy = false; }
}
$('settings-form').addEventListener('submit', async (e) => {
  e.preventDefault(); if (!settings) return;
  if (!confirm('Simpan pengaturan paper? Pastikan service bot berhenti dan posisi telah diperiksa.')) return;
  const payload = {risk:{},strategy:{}};
  for (const group of ['risk','strategy']) for (const [key,,scale] of fields[group]) payload[group][key] = Number(e.currentTarget.elements[group + '.' + key].value) / scale;
  try { await api('/api/settings',payload); $('settings-message').textContent = 'Tersimpan. Mulai ulang bot melalui Cockpit agar berlaku.'; await refresh(true); }
  catch (error) { $('settings-message').textContent = error.message; }
});
for (const [id, action, prompt] of [
  ['pause','pause','Jeda entry baru? Exit tetap dipantau selama bot berjalan.'],
  ['resume','resume','Lanjutkan izin entry baru? Batas risiko tetap berlaku.'],
  ['stop','stop','Minta bot menutup posisi virtual dan berhenti? Penutupan dapat gagal; periksa posisi dan log.'],
  ['reset-stop','reset_stop','Reset status stop? Pastikan penyebab stop sudah diperiksa. Bot harus berhenti dan tidak ada posisi atau kill aktif.']
]) $(id).addEventListener('click', async () => {
  if (!confirm(prompt)) return;
  try { await api('/api/control',{action}); $('control-message').textContent = 'Permintaan tercatat. Periksa status dan log untuk hasilnya.'; await refresh(); }
  catch (error) { $('control-message').textContent = error.message; }
});
$('refresh').addEventListener('click', () => refresh());
$('logout').addEventListener('click', async () => { try { await api('/api/logout',{}); } finally { location.reload(); } });
(async () => { try { csrf = (await api('/api/session')).csrf; await refresh(true); setInterval(() => refresh(), 30000); } catch (error) { $('global-error').textContent = error.message; $('global-error').classList.remove('hidden'); } })();
