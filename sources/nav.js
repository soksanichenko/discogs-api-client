// Shared header for the Discogs proxy pages (Swagger API, Inventory, Collection):
// fetches OAuth status and renders either a sign-in button or the username +
// page-switcher dropdown + sign-out link. Page links only appear once
// authorized, since Inventory/Collection are only useful for the signed-in
// Discogs account.
(function () {
  const BASE = window.location.pathname.replace(/\/(docs|inventory|collection)\/?$/, '').replace(/\/$/, '');
  const PAGES = [
    { path: '/docs', label: 'API' },
    { path: '/inventory', label: 'Inventory' },
    { path: '/collection', label: 'Collection' },
  ];

  function currentPath() {
    const rest = window.location.pathname.slice(BASE.length);
    return rest === '' ? '/' : rest;
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function render(container, status) {
    if (status.authorized) {
      const cur = currentPath();
      const options = PAGES.map(p => `<option value="${p.path}" ${p.path === cur ? 'selected' : ''}>${p.label}</option>`).join('');
      container.innerHTML =
        `<span class="nav-user">Signed in as <strong>${escapeHtml(status.username)}</strong></span>` +
        `<select class="nav-select" aria-label="Switch page" onchange="if(this.value) window.location.href='${BASE}'+this.value">${options}</select>` +
        `<a class="nav-link" href="${BASE}/oauth/revoke">Sign out</a>`;
    } else if (status.configured) {
      container.innerHTML = `<a class="nav-btn" href="${BASE}/oauth/start">Sign in with Discogs &rarr;</a>`;
    } else {
      container.innerHTML = `<span class="nav-hint">Set <code>DISCOGS_CONSUMER_KEY</code> + <code>DISCOGS_CONSUMER_SECRET</code> to enable sign-in</span>`;
    }
  }

// Optional links panel (used on the home page): a vertical list of API/
  // Inventory/Collection buttons, shown only once authorized.
  function renderLinksPanel(container, status) {
    if (!container) return;
    container.innerHTML = status.authorized
      ? PAGES.map(p => `<a class="nav-btn" style="justify-content:center" href="${BASE}${p.path}">${p.label} &rarr;</a>`).join('')
      : '';
  }

  window.initDiscogsNav = function (containerId, linksContainerId) {
    const container = document.getElementById(containerId);
    const linksContainer = linksContainerId ? document.getElementById(linksContainerId) : null;
    if (!container && !linksContainer) return;
    fetch(`${BASE}/oauth/status`)
      .then(r => r.json())
      .then(status => {
        if (container) render(container, status);
        renderLinksPanel(linksContainer, status);
      })
      .catch(() => { if (container) container.innerHTML = ''; });
  };
})();
