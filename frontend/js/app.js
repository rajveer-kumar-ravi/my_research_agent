/* ==========================================================================
   Web Research Agent — app.js
   Handles: submitting a research question, polling for progress, and
   rendering the live "Evidence Trail" and final report. Every piece of
   research data rendered here comes directly from the FastAPI backend —
   nothing is fabricated client-side.
   ========================================================================== */

(function () {
  "use strict";

  // --- Single source of truth for the API base URL. -----------------------
  // FIX: Changed to "/api" so it automatically uses the live Railway URL!
  const API_BASE = "/api";

  const POLL_INTERVAL_MS = 1800;
  const MAX_POLL_NETWORK_RETRIES = 5;
  const MAX_POLL_DURATION_MS = 5 * 60 * 1000; // give up after 5 minutes, matches backend budget + buffer

  const EXAMPLE_QUESTIONS = [
    "Compare the latest RAG evaluation methods, their advantages and disadvantages, and recommend which approach is most suitable for production.",
    "What are the tradeoffs between vector databases like FAISS, Pinecone, and Weaviate for a production RAG system?",
    "How do current approaches to LLM agent memory compare, and which is best for a long-running assistant?",
  ];

  // --- DOM references -------------------------------------------------------
  const els = {
    form: document.getElementById("researchForm"),
    queryInput: document.getElementById("queryInput"),
    charCount: document.getElementById("charCount"),
    validationHint: document.getElementById("validationHint"),
    submitBtn: document.getElementById("submitBtn"),
    submitBtnText: document.getElementById("submitBtnText"),
    exampleChips: document.getElementById("exampleChips"),
    researchFormCard: document.getElementById("researchFormCard"),

    errorBanner: document.getElementById("errorBanner"),
    errorMessage: document.getElementById("errorMessage"),

    evidenceTrail: document.getElementById("evidenceTrail"),
    trailQueryTitle: document.getElementById("trailQueryTitle"),
    trailStatusText: document.getElementById("trailStatusText"),
    trailSpinner: document.getElementById("trailSpinner"),
    trailLog: document.getElementById("trailLog"),
    newResearchBtn: document.getElementById("newResearchBtn"),

    reportSection: document.getElementById("reportSection"),
    statSources: document.getElementById("statSources"),
    statClaims: document.getElementById("statClaims"),
    statConflicts: document.getElementById("statConflicts"),
    insufficientNote: document.getElementById("insufficientNote"),
    insufficientText: document.getElementById("insufficientText"),
    executiveSummary: document.getElementById("executiveSummary"),
    findingsCard: document.getElementById("findingsCard"),
    findingsList: document.getElementById("findingsList"),
    analysisCard: document.getElementById("analysisCard"),
    detailedAnalysis: document.getElementById("detailedAnalysis"),
    comparisonCard: document.getElementById("comparisonCard"),
    comparisonTableBody: document.getElementById("comparisonTableBody"),
    conflictsCard: document.getElementById("conflictsCard"),
    conflictsList: document.getElementById("conflictsList"),
    claimsCard: document.getElementById("claimsCard"),
    claimsList: document.getElementById("claimsList"),
    sourcesCard: document.getElementById("sourcesCard"),
    sourcesGrid: document.getElementById("sourcesGrid"),

    apiStatusDot: document.getElementById("apiStatusDot"),
    apiStatusText: document.getElementById("apiStatusText"),
    userEmail: document.getElementById("userEmail"),
    logoutBtn: document.getElementById("logoutBtn"),
  };

  let pollTimer = null;
  let pollStartedAt = 0;
  let networkRetryCount = 0;
  let currentResearchId = null;

  // --- Small utilities -------------------------------------------------------

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function show(el) { el.classList.add("is-visible"); }
  function hide(el) { el.classList.remove("is-visible"); }

  function showError(message) {
    els.errorMessage.textContent = message;
    show(els.errorBanner);
  }

  function clearError() {
    hide(els.errorBanner);
  }

  // --- API status indicator ---------------------------------------------------

  async function checkApiHealth() {
    try {
      const res = await fetch(`${API_BASE}/health`, { credentials: "include" });
      if (!res.ok) throw new Error("bad status");
      const data = await res.json();
      els.apiStatusDot.style.background = "var(--color-trust)";
      const missing = [];
      if (!data.gemini_configured) missing.push("Gemini");
      if (!data.search_configured) missing.push("Search");
      els.apiStatusText.textContent = missing.length
        ? `API online (${missing.join(" & ")} key not set)`
        : "API online";
    } catch (e) {
      els.apiStatusDot.style.background = "var(--color-danger)";
      els.apiStatusText.textContent = "API unreachable";
    }
  }

  // --- Example chips -----------------------------------------------------------

  function renderExampleChips() {
    els.exampleChips.innerHTML = "";
    EXAMPLE_QUESTIONS.forEach((question) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "example-chip";
      const short = question.length > 46 ? question.slice(0, 46).trim() + "…" : question;
      chip.textContent = short;
      chip.title = question;
      chip.addEventListener("click", () => {
        els.queryInput.value = question;
        els.queryInput.dispatchEvent(new Event("input"));
        els.queryInput.focus();
      });
      els.exampleChips.appendChild(chip);
    });
  }

  // --- Form validation -----------------------------------------------------------

  function updateCharCount() {
    const len = els.queryInput.value.length;
    els.charCount.textContent = `${len} / 2000`;
    if (len > 0 && len < 8) {
      els.validationHint.textContent = "Question is too short (min 8 characters)";
      els.validationHint.parentElement.classList.add("is-error");
    } else {
      els.validationHint.textContent = "";
      els.validationHint.parentElement.classList.remove("is-error");
    }
  }

  // --- Submitting a new research request -----------------------------------------

  async function handleSubmit(event) {
    event.preventDefault();
    const query = els.queryInput.value.trim();

    if (query.length < 8) {
      els.validationHint.textContent = "Please enter at least 8 characters.";
      els.validationHint.parentElement.classList.add("is-error");
      els.queryInput.focus();
      return;
    }

    clearError();
    setSubmitting(true);

    try {
      const res = await fetch(`${API_BASE}/research`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ query }),
      });

      if (!res.ok) {
        const detail = await safeParseErrorDetail(res);
        throw new Error(detail || `Request failed (HTTP ${res.status}).`);
      }

      const data = await res.json();
      if (!data || !data.id) {
        throw new Error("The server response was missing a research ID.");
      }

      currentResearchId = data.id;
      history.replaceState(null, "", `index.html?id=${encodeURIComponent(data.id)}`);
      beginResearchView(data.query || query);
      startPolling(data.id);
    } catch (err) {
      showError(networkAwareMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function safeParseErrorDetail(res) {
    try {
      const data = await res.json();
      if (typeof data.detail === "string") return data.detail;
      if (Array.isArray(data.detail) && data.detail.length) {
        return data.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
      }
    } catch (e) {
      /* malformed/non-JSON error body — fall through to generic message */
    }
    return null;
  }

  function networkAwareMessage(err) {
    if (err instanceof TypeError) {
      return `Could not reach the research API at ${API_BASE}. Is the backend running?`;
    }
    return err.message || "An unexpected error occurred.";
  }

  function setSubmitting(isSubmitting) {
    els.submitBtn.disabled = isSubmitting;
    els.submitBtnText.textContent = isSubmitting ? "Starting…" : "Start research";
  }

  // --- Evidence Trail view state ---------------------------------------------

  function beginResearchView(query) {
    els.researchFormCard.style.display = "none";
    els.reportSection.classList.remove("is-visible");
    els.trailLog.innerHTML = "";
    els.trailQueryTitle.textContent = query;
    els.trailStatusText.textContent = "pending";
    els.trailSpinner.style.display = "inline-block";
    show(els.evidenceTrail);
  }

  function resetToForm() {
    stopPolling();
    currentResearchId = null;
    history.replaceState(null, "", "index.html");
    els.researchFormCard.style.display = "";
    hide(els.evidenceTrail);
    els.reportSection.classList.remove("is-visible");
    clearError();
    els.queryInput.value = "";
    updateCharCount();
    els.queryInput.focus();
  }

  const STAGE_GLYPHS = {
    pending: "○",
    in_progress: "●",
    completed: "✓",
    failed: "✕",
  };

  function renderTrail(progress, status) {
    els.trailStatusText.textContent = status;
    els.trailSpinner.style.display = status === "in_progress" || status === "pending" ? "inline-block" : "none";

    els.trailLog.innerHTML = "";
    if (!progress || progress.length === 0) {
      const empty = document.createElement("div");
      empty.className = "trail-entry";
      empty.setAttribute("data-status", "pending");
      empty.innerHTML = '<span class="trail-glyph">○</span><span class="trail-stage">queued</span><span class="trail-detail">Waiting for the agent to begin…</span>';
      els.trailLog.appendChild(empty);
      return;
    }

    progress.forEach((entry) => {
      const row = document.createElement("div");
      row.className = "trail-entry";
      row.setAttribute("data-status", entry.status || "pending");
      const glyph = STAGE_GLYPHS[entry.status] || "○";
      row.innerHTML =
        '<span class="trail-glyph">' + glyph + '</span>' +
        '<span class="trail-stage">' + escapeHtml(entry.stage) + '</span>' +
        '<span class="trail-detail">' + escapeHtml(entry.detail || "") + '</span>';
      els.trailLog.appendChild(row);
    });

    els.trailLog.scrollTop = els.trailLog.scrollHeight;
  }

  // --- Polling ------------------------------------------------------------------

  function startPolling(researchId) {
    stopPolling();
    pollStartedAt = Date.now();
    networkRetryCount = 0;
    pollOnce(researchId);
  }

  function stopPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  async function pollOnce(researchId) {
    if (Date.now() - pollStartedAt > MAX_POLL_DURATION_MS) {
      showError(
        "This research is taking longer than expected and the browser stopped waiting. " +
        "It may still finish on the server — check the History page shortly."
      );
      els.trailSpinner.style.display = "none";
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/research/${encodeURIComponent(researchId)}`, {
        credentials: "include",
      });

      if (res.status === 401) {
        redirectToLogin();
        return;
      }
      if (res.status === 404) {
        showError("This research request could not be found. It may have been deleted.");
        els.trailSpinner.style.display = "none";
        return;
      }
      if (!res.ok) {
        throw new Error("Server returned HTTP " + res.status + " while checking progress.");
      }

      const data = await res.json();
      networkRetryCount = 0; // reset backoff counter on any success

      renderTrail(data.progress, data.status);

      if (data.status === "completed") {
        els.trailSpinner.style.display = "none";
        if (data.report) {
          renderReport(data.report);
          els.reportSection.classList.add("is-visible");
        }
        return; // stop polling
      }

      if (data.status === "failed") {
        els.trailSpinner.style.display = "none";
        showError(data.error_message || "The research agent reported a failure with no further detail.");
        return; // stop polling
      }

      pollTimer = setTimeout(function () { pollOnce(researchId); }, POLL_INTERVAL_MS);
    } catch (err) {
      networkRetryCount += 1;
      if (networkRetryCount > MAX_POLL_NETWORK_RETRIES) {
        showError(networkAwareMessage(err) + " Gave up after several retries.");
        els.trailSpinner.style.display = "none";
        return;
      }
      // Back off and retry — a single dropped request shouldn't kill the run.
      pollTimer = setTimeout(function () { pollOnce(researchId); }, POLL_INTERVAL_MS * 1.5);
    }
  }

  // --- Report rendering -----------------------------------------------------------

  function renderReport(report) {
    els.statSources.textContent = (report.sources || []).length;
    els.statClaims.textContent = (report.claims || []).length;
    els.statConflicts.textContent = (report.conflicts || []).length;

    if (report.evidence_sufficient === false) {
      els.insufficientNote.style.display = "";
      els.insufficientText.textContent =
        report.insufficient_evidence_note || "The agent could not gather enough evidence to answer confidently.";
    } else {
      els.insufficientNote.style.display = "none";
    }

    els.executiveSummary.textContent = report.executive_summary || "No summary was generated.";

    // Key findings
    els.findingsList.innerHTML = "";
    const findings = report.key_findings || [];
    if (findings.length === 0) {
      els.findingsCard.style.display = "none";
    } else {
      els.findingsCard.style.display = "";
      findings.forEach(function (finding) {
        const li = document.createElement("li");
        li.textContent = finding;
        els.findingsList.appendChild(li);
      });
    }

    // Detailed analysis
    if (report.detailed_analysis && report.detailed_analysis.trim()) {
      els.analysisCard.style.display = "";
      els.detailedAnalysis.textContent = report.detailed_analysis;
    } else {
      els.analysisCard.style.display = "none";
    }

    // Comparison table
    const rows = report.comparison_table || [];
    els.comparisonTableBody.innerHTML = "";
    if (rows.length === 0) {
      els.comparisonCard.style.display = "none";
    } else {
      els.comparisonCard.style.display = "";
      rows.forEach(function (row) {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + escapeHtml(row.method) + "</td>" +
          "<td>" + escapeHtml(row.advantages) + "</td>" +
          "<td>" + escapeHtml(row.disadvantages) + "</td>" +
          "<td>" + escapeHtml(row.best_use_case) + "</td>";
        els.comparisonTableBody.appendChild(tr);
      });
    }

    // Conflicts
    const conflicts = report.conflicts || [];
    els.conflictsList.innerHTML = "";
    if (conflicts.length === 0) {
      els.conflictsCard.style.display = "none";
    } else {
      els.conflictsCard.style.display = "";
      conflicts.forEach(function (conflict) {
        const div = document.createElement("div");
        div.className = "conflict-item";
        div.innerHTML =
          '<div class="conflict-topic">' + escapeHtml(conflict.topic) + '</div>' +
          '<p>' + escapeHtml(conflict.description) + '</p>';
        els.conflictsList.appendChild(div);
      });
    }

    // Claims
    const claims = report.claims || [];
    els.claimsList.innerHTML = "";
    if (claims.length === 0) {
      els.claimsCard.style.display = "none";
    } else {
      els.claimsCard.style.display = "";
      claims.forEach(function (claim) {
        const div = document.createElement("div");
        div.className = "claim-item";
        const urls = claim.supporting_source_urls || [];
        const pills = urls
          .map(function (url) { return '<span class="source-pill">' + escapeHtml(domainFromUrl(url)) + '</span>'; })
          .join(" ");
        const metaHtml = urls.length
          ? '<span class="claim-confidence">confidence ' + Math.round((claim.confidence || 0) * 100) + '%</span> ' + pills
          : '<span class="claim-unsupported">unsupported by retrieved evidence</span>';
        div.innerHTML =
          '<div class="claim-text">' + escapeHtml(claim.text) + '</div>' +
          '<div class="claim-meta">' + metaHtml + '</div>';
        els.claimsList.appendChild(div);
      });
    }

    // Sources
    const sources = report.sources || [];
    els.sourcesGrid.innerHTML = "";
    if (sources.length === 0) {
      els.sourcesCard.style.display = "none";
    } else {
      els.sourcesCard.style.display = "";
      sources.forEach(function (source) {
        const a = document.createElement("a");
        a.href = source.source_url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.className = "source-card";
        a.style.display = "block";
        a.style.textDecoration = "none";
        const quality = (source.quality || "medium").toLowerCase();
        a.innerHTML =
          '<div class="source-title">' + escapeHtml(source.title || source.source_url) + '</div>' +
          '<div class="source-domain">' + escapeHtml(source.source_domain) + '</div>' +
          '<span class="quality-badge"><span class="quality-dot ' + escapeHtml(quality) + '"></span>' + escapeHtml(quality) + ' quality</span>';
        els.sourcesGrid.appendChild(a);
      });
    }
  }

  function domainFromUrl(url) {
    try {
      return new URL(url).hostname;
    } catch (e) {
      return url;
    }
  }

  // --- Loading an existing research from a URL param (?id=...) -------------------

  async function loadFromUrlParam() {
    const params = new URLSearchParams(window.location.search);
    const id = params.get("id");
    if (!id) return;

    currentResearchId = id;
    els.researchFormCard.style.display = "none";
    show(els.evidenceTrail);
    els.trailQueryTitle.textContent = "Loading research…";
    els.trailSpinner.style.display = "inline-block";

    try {
      const res = await fetch(`${API_BASE}/research/${encodeURIComponent(id)}`, {
        credentials: "include",
      });
      if (res.status === 401) {
        redirectToLogin();
        return;
      }
      if (res.status === 404) {
        showError("This research request could not be found. It may have been deleted.");
        hide(els.evidenceTrail);
        els.researchFormCard.style.display = "";
        return;
      }
      if (!res.ok) throw new Error("Server returned HTTP " + res.status + ".");

      const data = await res.json();
      els.trailQueryTitle.textContent = data.query;
      renderTrail(data.progress, data.status);

      if (data.status === "completed") {
        els.trailSpinner.style.display = "none";
        if (data.report) {
          renderReport(data.report);
          els.reportSection.classList.add("is-visible");
        }
      } else if (data.status === "failed") {
        els.trailSpinner.style.display = "none";
        showError(data.error_message || "The research agent reported a failure with no further detail.");
      } else {
        startPolling(id);
      }
    } catch (err) {
      showError(networkAwareMessage(err));
    }
  }

  // --- Session guard / logout ----------------------------------------------------

  function redirectToLogin() {
    window.location.href = "login.html";
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
      // Can't reach the API at all — show the form anyway rather than
      // trapping the user on a blank page; their first real request will
      // surface a clear network error.
      startApp();
    }
  }

  async function handleLogout() {
    try {
      await fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include" });
    } finally {
      redirectToLogin();
    }
  }

  // --- Wire up ------------------------------------------------------------------

  function startApp() {
    renderExampleChips();
    checkApiHealth();

    els.form.addEventListener("submit", handleSubmit);
    els.queryInput.addEventListener("input", updateCharCount);
    els.newResearchBtn.addEventListener("click", resetToForm);
    if (els.logoutBtn) els.logoutBtn.addEventListener("click", handleLogout);

    loadFromUrlParam();
  }

  document.addEventListener("DOMContentLoaded", requireSessionThenInit);
})();