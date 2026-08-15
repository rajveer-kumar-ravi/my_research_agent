/* ==========================================================================
   Web Research Agent — auth.js (login.html only)
   Handles Sign In, Create Account, and the Google OAuth redirect. All
   authentication is validated server-side via an HttpOnly session cookie —
   this file never stores or trusts any auth state itself, it only reacts
   to what the API returns.
   ========================================================================== */

(function () {
  "use strict";

  const API_BASE =
    window.location.port === "8000"
      ? `${window.location.origin}/api`
      : "http://localhost:8000/api";

  const els = {
    authError: document.getElementById("authError"),
    tabSignIn: document.getElementById("tabSignIn"),
    tabCreateAccount: document.getElementById("tabCreateAccount"),
    panelSignIn: document.getElementById("panelSignIn"),
    panelCreateAccount: document.getElementById("panelCreateAccount"),
    signInForm: document.getElementById("signInForm"),
    signInSubmit: document.getElementById("signInSubmit"),
    signInSubmitText: document.getElementById("signInSubmitText"),
    createAccountForm: document.getElementById("createAccountForm"),
    createAccountSubmit: document.getElementById("createAccountSubmit"),
    createAccountSubmitText: document.getElementById("createAccountSubmitText"),
    googleSignInBtn: document.getElementById("googleSignInBtn"),
  };

  function showError(message) {
    els.authError.textContent = message;
    els.authError.classList.add("is-visible");
  }

  function clearError() {
    els.authError.classList.remove("is-visible");
  }

  function switchTab(tab) {
    clearError();
    const showingSignIn = tab === "signin";
    els.tabSignIn.classList.toggle("is-active", showingSignIn);
    els.tabCreateAccount.classList.toggle("is-active", !showingSignIn);
    els.tabSignIn.setAttribute("aria-selected", String(showingSignIn));
    els.tabCreateAccount.setAttribute("aria-selected", String(!showingSignIn));
    els.panelSignIn.classList.toggle("is-active", showingSignIn);
    els.panelCreateAccount.classList.toggle("is-active", !showingSignIn);
  }

  async function safeParseErrorDetail(res) {
    try {
      const data = await res.json();
      if (typeof data.detail === "string") return data.detail;
      if (Array.isArray(data.detail) && data.detail.length) {
        return data.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
      }
    } catch (e) {
      /* non-JSON error body */
    }
    return null;
  }

  function networkAwareMessage(err) {
    if (err instanceof TypeError) {
      return `Could not reach the research API at ${API_BASE}. Is the backend running?`;
    }
    return err.message || "An unexpected error occurred.";
  }

  function goToMainApp() {
    window.location.href = "index.html";
  }

  // --- Redirect away if already authenticated ---
  async function checkExistingSession() {
    try {
      const res = await fetch(`${API_BASE}/auth/session`, { credentials: "include" });
      if (!res.ok) return;
      const data = await res.json();
      if (data.authenticated) {
        goToMainApp();
      }
    } catch (e) {
      // API unreachable — just let the user see the login form; their
      // submit attempt will surface a clear network error anyway.
    }
  }

  // --- Sign In ---
  async function handleSignIn(event) {
    event.preventDefault();
    clearError();

    const email = document.getElementById("signInEmail").value.trim();
    const password = document.getElementById("signInPassword").value;

    els.signInSubmit.disabled = true;
    els.signInSubmitText.textContent = "Signing in…";

    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, password }),
      });

      if (!res.ok) {
        const detail = await safeParseErrorDetail(res);
        throw new Error(detail || "Invalid email or password.");
      }

      goToMainApp();
    } catch (err) {
      showError(networkAwareMessage(err));
    } finally {
      els.signInSubmit.disabled = false;
      els.signInSubmitText.textContent = "Sign In";
    }
  }

  // --- Create Account ---
  async function handleCreateAccount(event) {
    event.preventDefault();
    clearError();

    const fullName = document.getElementById("fullName").value.trim();
    const email = document.getElementById("registerEmail").value.trim();
    const password = document.getElementById("registerPassword").value;
    const confirmPassword = document.getElementById("confirmPassword").value;

    if (password !== confirmPassword) {
      showError("Passwords do not match.");
      return;
    }

    els.createAccountSubmit.disabled = true;
    els.createAccountSubmitText.textContent = "Creating account…";

    try {
      const res = await fetch(`${API_BASE}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          full_name: fullName,
          email: email,
          password: password,
          confirm_password: confirmPassword,
        }),
      });

      if (!res.ok) {
        const detail = await safeParseErrorDetail(res);
        throw new Error(detail || "Could not create account.");
      }

      // Registration signs the user in automatically — go straight in,
      // no separate login step required.
      goToMainApp();
    } catch (err) {
      showError(networkAwareMessage(err));
    } finally {
      els.createAccountSubmit.disabled = false;
      els.createAccountSubmitText.textContent = "Create Account";
    }
  }

  // --- Google ---
  function handleGoogleSignIn() {
    window.location.href = `${API_BASE}/auth/google`;
  }

  // --- URL error param (set by the backend's OAuth callback on failure) ---
  function showOAuthErrorIfPresent() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("error") === "google_oauth_failed") {
      showError("Google sign-in failed or was cancelled. Please try again.");
    }
  }

  function init() {
    els.tabSignIn.addEventListener("click", () => switchTab("signin"));
    els.tabCreateAccount.addEventListener("click", () => switchTab("create"));
    els.signInForm.addEventListener("submit", handleSignIn);
    els.createAccountForm.addEventListener("submit", handleCreateAccount);
    els.googleSignInBtn.addEventListener("click", handleGoogleSignIn);

    showOAuthErrorIfPresent();
    checkExistingSession();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
