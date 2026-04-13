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
const titleEl = document.getElementById('current-title');

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

async function savePage(tab, apiUrl, token) {
  const html = await getPageHtml(tab.id);
  const formData = new FormData();
  formData.append('document', new Blob([html], { type: 'text/html' }), 'document.html');

  const response = await fetch(`${apiUrl}/document?url=${encodeURIComponent(tab.url)}`, {
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

function setSearchStatus(message, type = '') {
  searchStatus.textContent = message;
  searchStatus.className = `search-status ${type}`;
}

function renderResults(results) {
  resultsEl.innerHTML = '';

  if (results.length === 0) {
    setSearchStatus('No results found.');
    return;
  }

  setSearchStatus('');
  results.forEach(({ url }) => {
    const item = document.createElement('div');
    item.className = 'result-item';

    let displayTitle = url;
    try {
      const parsed = new URL(url);
      displayTitle = (parsed.hostname + parsed.pathname).replace(/\/$/, '');
    } catch (_) {}

    item.innerHTML = `
      <div class="result-title">${escapeHtml(displayTitle)}</div>
      <div class="result-url">${escapeHtml(url)}</div>
    `;
    item.addEventListener('click', () => chrome.tabs.create({ url }));
    resultsEl.appendChild(item);
  });
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

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

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const { results } = await response.json();
    renderResults(results);
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
  const { apiUrl, bearerToken } = await chrome.storage.local.get(['apiUrl', 'bearerToken']);

  if (!apiUrl || !bearerToken) {
    document.getElementById('not-configured').style.display = 'block';
    return;
  }

  document.getElementById('configured').style.display = 'block';

  // Save tab setup
  const tab = await getCurrentTab();
  urlEl.textContent = tab.url;
  titleEl.textContent = tab.title || '';

  saveBtn.addEventListener('click', async () => {
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
  searchBtn.addEventListener('click', () => doSearch(apiUrl, bearerToken));
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doSearch(apiUrl, bearerToken);
  });
}

init();
