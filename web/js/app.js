'use strict';

const API_URL = '/ptevents/api/events';
const REFRESH_INTERVAL_MS = 60_000;

const SEV_COLOR = {
  LOW:      '#3fb950',
  MEDIUM:   '#d29922',
  HIGH:     '#f78166',
  CRITICAL: '#ff0000',
};

const TYPE_ICON = {
  FIRE:           '🔥',
  STORM:          '🌧',
  EARTHQUAKE:     '🌊',
  CONGESTION:     '🚗',
  ROAD_CLOSURE:   '⛔',
  AIR_QUALITY:    '🏭',
  STRIKE:         '✊',
  PLANNED_WORKS:  '🚧',
  EVENT_CLOSURE:  '🎉',
  WIND:           '💨',
  RAIN:           '🌧',
  HEAT:           '🌡',
  COLD:           '❄️',
  FLOOD:          '💧',
  DROUGHT:        '☀️',
  TSUNAMI:        '🌊',
  LANDSLIDE:      '⛰',
  ACCIDENT:       '🚨',
  ROADWORK:       '🚧',
  POWER_OUTAGE:   '⚡',
  WATER_OUTAGE:   '🚿',
  GAS_LEAK:       '⚠️',
  TELECOM:        '📡',
  CIVIL_PROTECTION: '🆘',
  EVACUATION:     '🏃',
  DELAY:          '⏱',
  FIRE_RISK:      '🔥',
  UV_ALERT:       '☀️',
  SCHEDULED_MAINTENANCE: '🔧',
  SERVICE_DISRUPTION: '⚠️',
};

// State
let allEvents = [];
let markers = {};
let activeFilter = 'ALL';
let map;
let homeCircle;
let locationData = null;

// ── Init ──────────────────────────────────────────────────────

function initMap(lat, lon, radiusKm) {
  map = L.map('map', { zoomControl: true }).setView([lat, lon], 12);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors',
    maxZoom: 19,
  }).addTo(map);

  // Home location marker
  L.circleMarker([lat, lon], {
    radius: 8,
    color: '#58a6ff',
    fillColor: '#58a6ff',
    fillOpacity: 0.9,
    weight: 2,
  }).addTo(map).bindTooltip('Casa', { permanent: false });

  // Monitoring radius circle
  homeCircle = L.circle([lat, lon], {
    radius: radiusKm * 1000,
    color: '#58a6ff',
    fillColor: '#58a6ff',
    fillOpacity: 0.04,
    weight: 1,
    dashArray: '4 6',
  }).addTo(map);
}

// ── Data ──────────────────────────────────────────────────────

async function fetchEvents() {
  const res = await fetch(API_URL);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function dismissEvent(id) {
  await fetch(`/ptevents/api/events/${encodeURIComponent(id)}`, { method: 'DELETE' });
  allEvents = allEvents.filter(e => e.id !== id);
  render();
}

// ── Render ────────────────────────────────────────────────────

function filteredEvents() {
  if (activeFilter === 'ALL') return allEvents;
  // Match by exact type or prefix (e.g. FIRE matches FIRE and FIRE_RISK)
  return allEvents.filter(e => e.type === activeFilter || e.type.startsWith(activeFilter + '_'));
}

function render() {
  const events = filteredEvents();
  renderMarkers(events);
  renderSidebar(events);
}

function renderMarkers(events) {
  // Remove old markers not in current set
  const currentIds = new Set(events.map(e => e.id));
  for (const [id, m] of Object.entries(markers)) {
    if (!currentIds.has(id)) {
      map.removeLayer(m);
      delete markers[id];
    }
  }

  for (const ev of events) {
    if (ev.lat == null || ev.lon == null) continue;
    if (markers[ev.id]) continue; // already on map

    const color = SEV_COLOR[ev.severity] || '#8b949e';
    const marker = L.circleMarker([ev.lat, ev.lon], {
      radius: severityRadius(ev.severity),
      color: color,
      fillColor: color,
      fillOpacity: 0.7,
      weight: 2,
    });

    const icon = TYPE_ICON[ev.type] || '⚠️';
    marker.bindTooltip(`${icon} ${ev.title || ev.type}`, { sticky: true });
    marker.on('click', () => openPopup(ev));
    marker.addTo(map);
    markers[ev.id] = marker;
  }
}

function severityRadius(sev) {
  return { LOW: 8, MEDIUM: 11, HIGH: 14, CRITICAL: 18 }[sev] || 10;
}

function renderSidebar(events) {
  const list = document.getElementById('event-list');
  if (!events.length) {
    list.innerHTML = '<div class="empty-state">Sem eventos activos neste filtro.</div>';
    return;
  }

  list.innerHTML = events.map(ev => {
    const icon = TYPE_ICON[ev.type] || '⚠️';
    const title = ev.title || ev.type;
    const started = formatDate(ev.started_at);
    const expires = formatDate(ev.expires_at);
    return `
      <div class="event-card sev-${ev.severity}" data-id="${escHtml(ev.id)}">
        <div class="event-card-header">
          <div class="event-title">${icon} ${escHtml(title)}</div>
          <span class="event-badge badge-${ev.severity}">${ev.severity}</span>
        </div>
        <div class="event-meta">
          <span>${escHtml(ev.source)}</span>
          <span>${escHtml(ev.type)}</span>
          <span>${started}</span>
          <span>exp: ${expires}</span>
        </div>
        <div class="event-dismiss">
          <button class="btn-dismiss" data-id="${escHtml(ev.id)}">Dispensar</button>
        </div>
      </div>`;
  }).join('');

  // Card click → pan map + open popup
  list.querySelectorAll('.event-card').forEach(card => {
    card.addEventListener('click', e => {
      if (e.target.classList.contains('btn-dismiss')) return;
      const ev = allEvents.find(x => x.id === card.dataset.id);
      if (ev) openPopup(ev);
    });
  });

  // Dismiss buttons
  list.querySelectorAll('.btn-dismiss').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      if (confirm('Dispensar este alerta?')) dismissEvent(btn.dataset.id);
    });
  });
}

function openPopup(ev) {
  const icon = TYPE_ICON[ev.type] || '⚠️';
  const title = ev.title || ev.type;
  const started = formatDate(ev.started_at);
  const expires = formatDate(ev.expires_at);
  const safeUrl = /^https?:\/\//i.test(ev.url ?? '') ? ev.url : null;
  const urlHtml = safeUrl
    ? `<a href="${escHtml(safeUrl)}" target="_blank" rel="noopener">Abrir fonte ↗</a>`
    : '';

  document.getElementById('popup-content').innerHTML = `
    <h2>${icon} ${escHtml(title)}</h2>
    <div class="popup-meta">
      <span class="popup-chip">${escHtml(ev.source)}</span>
      <span class="popup-chip">${escHtml(ev.type)}</span>
      <span class="popup-chip badge-${ev.severity}">${ev.severity}</span>
      <span class="popup-chip">Início: ${started}</span>
      <span class="popup-chip">Expira: ${expires}</span>
    </div>
    <p>${escHtml(ev.description || '')}</p>
    ${urlHtml}
    <br/>
    <button class="popup-dismiss-btn" data-id="${escHtml(ev.id)}">Dispensar alerta</button>
  `;

  document.getElementById('popup-content')
    .querySelector('.popup-dismiss-btn')
    .addEventListener('click', () => {
      closePopup();
      dismissEvent(ev.id);
    });

  document.getElementById('popup-overlay').classList.remove('hidden');

  // Pan map to event
  if (ev.lat != null && ev.lon != null) {
    map.panTo([ev.lat, ev.lon]);
  }
}

function closePopup() {
  document.getElementById('popup-overlay').classList.add('hidden');
}

// ── Load & refresh ────────────────────────────────────────────

async function load() {
  try {
    const data = await fetchEvents();
    locationData = data.location;

    if (!map) {
      initMap(data.location.lat, data.location.lon, data.location.radius_km);
      document.getElementById('location-name').textContent = `— ${data.location.name}`;
    }

    allEvents = data.events;
    render();
  } catch (err) {
    console.error('Failed to load events', err);
  }
}

// ── Helpers ───────────────────────────────────────────────────

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('pt-PT', {
      day: '2-digit', month: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return iso; }
}

// ── Wire up ───────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Filter buttons
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeFilter = btn.dataset.type;
      render();
    });
  });

  // Refresh
  document.getElementById('btn-refresh').addEventListener('click', load);

  // Clear expired
  document.getElementById('btn-clear-expired').addEventListener('click', async () => {
    if (!confirm('Limpar todos os eventos expirados?')) return;
    // Server auto-cleans on cleanup_expired(); here we just reload
    await load();
  });

  // Close popup
  document.getElementById('popup-close').addEventListener('click', closePopup);
  document.getElementById('popup-overlay').addEventListener('click', e => {
    if (e.target === document.getElementById('popup-overlay')) closePopup();
  });

  // Initial load
  load();

  // Auto-refresh
  setInterval(load, REFRESH_INTERVAL_MS);
});
