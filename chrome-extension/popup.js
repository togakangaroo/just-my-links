const saveBtn = document.getElementById('save-btn');
const statusEl = document.getElementById('status');
const urlEl = document.getElementById('current-url');
const titleEl = document.getElementById('current-title');
const mainEl = document.getElementById('main');
const notConfiguredEl = document.getElementById('not-configured');

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

function setStatus(message, type) {
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
  const blob = new Blob([html], { type: 'text/html' });
  formData.append('document', blob, 'document.html');

  const encodedUrl = encodeURIComponent(tab.url);
  const response = await fetch(`${apiUrl}/document?url=${encodedUrl}`, {
    method: 'PUT',
    headers: { 'Authorization': `Bearer ${token}` },
    body: formData,
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`HTTP ${response.status}: ${body}`);
  }

  return response.json();
}

async function init() {
  const { apiUrl, bearerToken } = await chrome.storage.local.get(['apiUrl', 'bearerToken']);

  if (!apiUrl || !bearerToken) {
    mainEl.style.display = 'none';
    notConfiguredEl.style.display = 'block';
    return;
  }

  const tab = await getCurrentTab();
  urlEl.textContent = tab.url;
  titleEl.textContent = tab.title || '';

  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    setStatus('Saving…', 'saving');

    try {
      await savePage(tab, apiUrl, bearerToken);
      setStatus('Saved!', 'success');
    } catch (err) {
      console.error('Save failed:', err);
      setStatus(`Error: ${err.message}`, 'error');
      saveBtn.disabled = false;
    }
  });
}

init();
