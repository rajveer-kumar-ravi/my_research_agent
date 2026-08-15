/* ==========================================================================
   Web Research Agent — history.js
   Lists past research runs from GET /api/research, links to view each full
   report on index.html?id=..., and supports deleting a run. No data here
   is fabricated — everything rendered comes from the FastAPI response.
   ========================================================================== */

(function () {
  "use strict";

  // FIX: Changed to "/api" so it automatically uses the live Railway URL!
  const API_BASE = "/api";

  const els = {
    loadingState: document.getElementById("loadingState"),
    emptyState: document.getElementById("emptyState"),
    historyList: document.getElementById("historyList"),
    errorBanner: document.getElementById("errorBanner"),
    errorMessage: document.getElementById("errorMessage"),
    apiStatusDot: document.getElementById("apiStatusDot"),
    apiStatusText: document.getElementById("apiStatusText"),
    userEmail: document.getElementById("userEmail"),
    logoutBtn: document.getElementById("logoutBtn"),
  };

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function formatDate(isoString) {
    if (!isoString) return "—";
    try {
      const date = new Date(isoString);
      return date.toLocaleString(undefined, {
        month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit",
      });
    } catch (e) {
      return isoString;
    }
  }

  function showError(message) {
    els.errorMessage.textContent = message;
    els.errorBanner.classList.add("is-visible");
  }

  function clearError() {
    els.errorBanner.classList.remove("is-visible");
  }

  function networkAwareMessage(err) {
    if (err instanceof TypeError) {
      return `Could not reach the research API at ${API_BASE}. Is the backend running?`;
    }
    return err.message || "An unexpected error occurred.";
  }

  async function checkApiHealth() {
    try {
      const res = await fetch(`${API_BASE}/health`, { credentials: "include" });
      if (!res.ok) throw new Error("bad status");
      els.apiStatusDot.style.background = "var(--color-trust)";
      els.apiStatusText.textContent = "API online";
    } catch (e) {
      els.apiStatusDot.style.background = "var(--color-danger)";
      els.apiStatusText.textContent = "API unreachable";
    }
  }

  function statusLabel(status) {
    return (status || "unknown").replace("_", " ");
  }

  function renderHistory(items) {
    els.historyList.innerHTML = "";

    if (!items || items.length === 0) {
      els.emptyState.style.display = "flex";
      return;
    }
    els.emptyState.style.display = "none";

    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = "history-card";
      card.setAttribute("data-research-id", item.id);

      const summaryHtml = item.summary
        ? `<p class="history-summary">${escapeHtml(item.summary)}</p>`
        : "";

      card.innerHTML = `
        <div class="history-card-top">
          <div>
            <p class="history-query">${escapeHtml(item.query)}</p>
            ${summaryHtml}
          </div>
          <span class="status-badge status-${escapeHtml(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
        </div>
        <div class="history-meta">
          <span>Created ${escapeHtml(formatDate(item.created_at))}</span>
          <span>${item.sources_count != null ? item.sources_count : 0} source(s)</span>
        </div>
        <div class="history-actions">
          <a class="btn btn-secondary btn-sm" href="index.html?id=${encodeURIComponent(item.id)}">View</a>
          <button type="button" class="btn btn-danger btn-sm" data-action="delete" data-id="${escapeHtml(item.id)}">Delete</button>
        </div>
      `;

      els.historyList.appendChild(card);
    });

    els.historyList.querySelectorAll('[data-action="delete"]').forEach((btn) => {
      btn.addEventListener("click", handleDelete);
    });
  }

  async function loadHistory() {
    clearError();
    els.loadingState.style.display = "flex";
    els.historyList.innerHTML = "";
    els.emptyState.style.display = "none";

    try {
      const res = await fetch(`${API_BASE}/research?limit=50`, { credentials: "include" });
      if (res.status === 401) {
        redirectToLogin();
        return;
      }
      if (!res.ok) {
        throw new Error(`Server returned HTTP ${res.status} while loading history.`);
      }
      const data = await res.json();
      renderHistory(data.items || []);
    } catch (err) {
      showError(networkAwareMessage(err));
      els.emptyState.style.display = "none";
    } finally {
      els.loadingState.style.display = "none";
    }
  }

  async function handleDelete(event) {
    const id = event.currentTarget.getAttribute("data-id");
    if (!id) return;

    const confirmed = window.confirm(
      "Delete this research request? This cannot be undone."
    );
    if (!confirmed) return;

    const card = els.historyList.querySelector(`[data-research-id="${cssEscape(id)}"]`);
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "Deleting…";

    try {
      const res = await fetch(`${API_BASE}/research/${encodeURIComponent(id)}`, {
        method: "DELETE",
        credentials: "include",
      });

      if (res.status === 404) {
        // Already gone — just remove it from the view.
        if (card) card.remove();
        maybeShowEmptyState();
        return;
      }
      if (!res.ok && res.status !== 204) {
        throw new Error(`Server returned HTTP ${res.status} while deleting.`);
      }

      if (card) card.remove();
      maybeShowEmptyState();
    } catch (err) {
      showError(networkAwareMessage(err));
      button.disabled = false;
      button.textContent = "Delete";
    }
  }

  function maybeShowEmptyState() {
    if (els.historyList.children.length === 0) {
      els.emptyState.style.display = "flex";
    }
  }

  function cssEscape(value) {
    if (window.CSS && window.CSS.escape) return window.CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  function redirectToLogin() {
    window.location.href = "login.html";
  }

  async function handleLogout() {
    try {
      await fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include" });
    } finally {
      redirectToLogin();
    }
  }

  async function requireSessionThenInit() {
    try {
      const res = await fetch(`${API_BASE}/auth/session`, { credentials: "include" });
      if (!res.ok) {
        redirectToLogin();
        return;
      }
      const data = await res.json();
      if (!data.authenticated) {
        redirectToLogin();
        return;
      }
      if (els.userEmail && data.user) {
        els.userEmail.textContent = data.user.email;
      }
      startApp();
    } catch (err) {
      startApp(); // can't reach the API at all — let loadHistory() surface a clear error
    }
  }

  function startApp() {
    checkApiHealth();
    loadHistory();
    if (els.logoutBtn) els.logoutBtn.addEventListener("click", handleLogout);
  }

  document.addEventListener("DOMContentLoaded", requireSessionThenInit);
})();