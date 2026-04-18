// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

function openOptions() {
  chrome.runtime.openOptionsPage();
}

document.getElementById('settings-link').addEventListener('click', (e) => {
  e.preventDefault();
  openOptions();
});

document.getElementById('open-options')?.addEventListener('click', (e) => {
  e.preventDefault();
  openOptions();
});

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`panel-${btn.dataset.tab}`).classList.add('active');
    if (btn.dataset.tab === 'search') {
      document.getElementById('search-input').focus();
    }
  });
});

// ---------------------------------------------------------------------------
// Save tab
// ---------------------------------------------------------------------------

const saveBtn = document.getElementById('save-btn');
const statusEl = document.getElementById('status');
const urlEl = document.getElementById('current-url');
const titleEl = document.getElementById('doc-title');

function setSaveStatus(message, type) {
  statusEl.textContent = message;
  statusEl.className = `status ${type}`;
}

async function getCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function getPageHtml(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => document.documentElement.outerHTML,
  });
  return results[0]?.result ?? '';
}

async function isPdfPage(tab) {
  // Check URL for .pdf extension (with optional query string / fragment)
  if (/\.pdf(\?|#|$)/i.test(tab.url)) return true;
  // Check if Chrome rendered its built-in PDF viewer
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => !!document.querySelector('embed[type="application/pdf"]'),
    });
    return results[0]?.result ?? false;
  } catch {
    return false;
  }
}

async function getPageContent(tab) {
  if (await isPdfPage(tab)) {
    const response = await fetch(tab.url);
    if (!response.ok) throw new Error(`Failed to fetch PDF: HTTP ${response.status}`);
    const blob = await response.blob();
    return { blob, filename: 'document.pdf' };
  }
  const html = await getPageHtml(tab.id);
  return { blob: new Blob([html], { type: 'text/html' }), filename: 'document.html' };
}

async function savePage(tab, apiUrl, token) {
  const { blob, filename } = await getPageContent(tab);
  const formData = new FormData();
  formData.append('document', blob, filename);

  const title = titleEl.value.trim();
  const params = new URLSearchParams({ url: tab.url });
  if (title) params.set('title', title);

  const response = await fetch(`${apiUrl}/document?${params}`, {
    method: 'PUT',
    headers: { 'Authorization': `Bearer ${token}` },
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Search tab
// ---------------------------------------------------------------------------

const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');
const searchStatus = document.getElementById('search-status');
const resultsEl = document.getElementById('results');
const statsStripe = document.getElementById('stats-stripe');

const SECTION_LABELS = { vector: 'Semantic', title: 'Title Match', tags: 'Tags' };
const STATS_KEY = 'jml-search-stats';

function getStats() {
  try { return JSON.parse(localStorage.getItem(STATS_KEY)) || {}; }
  catch { return {}; }
}

function recordImpression(section) {
  const s = getStats();
  if (!s[section]) s[section] = { clicks: 0, impressions: 0 };
  s[section].impressions++;
  localStorage.setItem(STATS_KEY, JSON.stringify(s));
}

function recordClick(section) {
  const s = getStats();
  if (!s[section]) s[section] = { clicks: 0, impressions: 0 };
  s[section].clicks++;
  localStorage.setItem(STATS_KEY, JSON.stringify(s));
}

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function setSearchStatus(message, type = '') {
  searchStatus.textContent = message;
  searchStatus.className = `search-status ${type}`;
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function resolveTitle(url, title) {
  if (title) return title;
  try {
    const p = new URL(url);
    return (p.hostname + p.pathname).replace(/\/$/, '');
  } catch { return url; }
}

function renderResults(sections) {
  resultsEl.innerHTML = '';
  statsStripe.className = '';    // re-hide on each new search
  statsStripe.textContent = '';

  const keys = Object.keys(sections || {}).filter(k => sections[k]?.length);
  if (keys.length === 0) {
    setSearchStatus('No results found.');
    return;
  }

  setSearchStatus('');
  keys.forEach(k => recordImpression(k));

  shuffle(keys).forEach(section => {
    const items = sections[section];

    const header = document.createElement('div');
    header.className = 'section-header';
    header.textContent = SECTION_LABELS[section] || section;
    resultsEl.appendChild(header);

    items.forEach(({ url, title, matched_tags }) => {
      const item = document.createElement('div');
      item.className = 'result-item';

      const tagsHtml = matched_tags?.length
        ? `<div class="result-tags">${matched_tags.map(t => `<span class="tag">#${escapeHtml(t)}</span>`).join('')}</div>`
        : '';

      item.innerHTML = `
        <div class="result-title">${escapeHtml(resolveTitle(url, title))}</div>
        <div class="result-url">${escapeHtml(url)}</div>
        ${tagsHtml}
      `;

      item.addEventListener('click', (e) => {
        recordClick(section);
        if (e.ctrlKey) {
          chrome.tabs.create({ url, active: false });
          // popup stays open — new tab opened in background
        } else {
          chrome.tabs.create({ url });
        }
      });

      resultsEl.appendChild(item);
    });
  });
}

statsStripe.addEventListener('click', () => {
  if (statsStripe.classList.contains('revealed')) {
    statsStripe.classList.remove('revealed');
    statsStripe.textContent = '';
    return;
  }
  const stats = getStats();
  const text = Object.entries(stats)
    .map(([k, v]) => `${SECTION_LABELS[k] || k}: ${v.clicks}c / ${v.impressions}i`)
    .join('   ');
  statsStripe.textContent = text || 'No data yet';
  statsStripe.classList.add('revealed');
});

async function doSearch(apiUrl, token) {
  const query = searchInput.value.trim();
  if (!query) return;

  searchBtn.disabled = true;
  resultsEl.innerHTML = '';
  setSearchStatus('Searching…');

  try {
    const response = await fetch(`${apiUrl}/search?q=${encodeURIComponent(query)}&top=8`, {
      headers: { 'Authorization': `Bearer ${token}` },
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const { sections } = await response.json();
    renderResults(sections);
  } catch (err) {
    setSearchStatus(`Error: ${err.message}`, 'error');
  } finally {
    searchBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  console.log('[JML] Search stats:', getStats());

  const { apiUrl, bearerToken } = await chrome.storage.local.get(['apiUrl', 'bearerToken']);

  if (!apiUrl || !bearerToken) {
    document.getElementById('not-configured').style.display = 'block';
    return;
  }

  document.getElementById('configured').style.display = 'block';

  // Save tab setup
  const tab = await getCurrentTab();
  urlEl.textContent = tab.url;
  titleEl.value = tab.title || '';

  document.getElementById('save-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    saveBtn.disabled = true;
    setSaveStatus('Saving…', 'saving');
    try {
      await savePage(tab, apiUrl, bearerToken);
      setSaveStatus('Saved!', 'success');
    } catch (err) {
      console.error('Save failed:', err);
      setSaveStatus(`Error: ${err.message}`, 'error');
      saveBtn.disabled = false;
    }
  });

  // Search tab setup
  document.getElementById('search-form').addEventListener('submit', (e) => {
    e.preventDefault();
    doSearch(apiUrl, bearerToken);
  });
}

init();
