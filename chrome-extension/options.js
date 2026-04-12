const apiUrlInput = document.getElementById('api-url');
const tokenInput = document.getElementById('bearer-token');
const saveBtn = document.getElementById('save-btn');
const statusEl = document.getElementById('status');

async function load() {
  const { apiUrl, bearerToken } = await chrome.storage.local.get(['apiUrl', 'bearerToken']);
  if (apiUrl) apiUrlInput.value = apiUrl;
  if (bearerToken) tokenInput.value = bearerToken;
}

saveBtn.addEventListener('click', async () => {
  const apiUrl = apiUrlInput.value.trim().replace(/\/$/, '');
  const bearerToken = tokenInput.value.trim();

  await chrome.storage.local.set({ apiUrl, bearerToken });

  statusEl.textContent = 'Saved!';
  setTimeout(() => { statusEl.textContent = ''; }, 2000);
});

load();
