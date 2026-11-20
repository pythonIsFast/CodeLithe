/* Part of the Code Lite UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

/* -- ChatGPT account ------------------------------------------------------ */

function renderAuth(auth) {
  state.auth = auth;
  const ready = auth.authenticated || auth.free_mode;
  const label = auth.free_mode ? "Pollinations free mode" : (auth.account_label || "ChatGPT account");
  el.accountName.textContent = ready ? label : "Not signed in";
  el.accountDetail.textContent = auth.free_mode
    ? "Anonymous requests are limited to one every 15 seconds."
    : (auth.authenticated
      ? (auth.email && auth.email !== label ? auth.email : "Connected with ChatGPT")
      : "Connect your ChatGPT subscription to use Code Lite.");
  el.authOverlay.hidden = Boolean(ready || auth.opencode_go);

  const waiting = auth.login_status === "waiting";
  el.authLogin.disabled = waiting;
  el.settingsLogin.disabled = waiting;
  el.authLogin.querySelector("span:last-child").textContent = waiting
    ? "Waiting for ChatGPT…"
    : "Sign in with ChatGPT";
  el.settingsLogin.textContent = waiting ? "Waiting for ChatGPT…" : "Sign in again";

  const showManual = waiting && Boolean(state.authUrl);
  el.authManual.hidden = !showManual;
  el.settingsAuthManual.hidden = !showManual;
  if (showManual) {
    el.authOpenLink.href = state.authUrl;
    el.settingsAuthOpenLink.href = state.authUrl;
  }

  const message = auth.error || (waiting ? "Finish signing in in your browser. If it did not open, use the link above." : "");
  el.authStatus.textContent = message;
  el.authStatus.classList.toggle("error", Boolean(auth.error));
  el.settingsAuthStatus.textContent = message;
  el.settingsAuthStatus.classList.toggle("error", Boolean(auth.error));
}

async function refreshAuth() {
  const auth = await get("/api/auth");
  renderAuth(auth);
  return auth;
}

async function enablePollinationsFree() {
  el.authFree.disabled = true;
  try {
    const data = await get("/api/settings/behaviour");
    state.behaviour = data;
    await saveBehaviour({ ...data.values, use_pollinations_free: true });
    await loadAuthenticatedApp();
  } finally {
    el.authFree.disabled = false;
  }
}


function stopAuthPolling() {
  if (state.authPoll) clearInterval(state.authPoll);
  state.authPoll = null;
}

async function pollAuth() {
  try {
    const auth = await refreshAuth();
    if (auth.login_status === "success" && auth.authenticated) {
      stopAuthPolling();
      if (!el.settingsOverlay.hidden) closeSettings();
      toast("Signed in with ChatGPT.");
      await loadAuthenticatedApp();
    } else if (auth.login_status === "error") {
      stopAuthPolling();
    }
  } catch (error) {
    stopAuthPolling();
    toast(error.message, true);
  }
}

async function startAuthLogin() {
  const hasNativeOpener = Boolean(window.pywebview?.api?.open_external);
  const popup = hasNativeOpener ? null : window.open("about:blank", "codelite-chatgpt-login");
  try {
    const data = await post("/api/auth/login");
    state.authUrl = data.authorization_url;
    await refreshAuth();
    if (hasNativeOpener) {
      try {
        await window.pywebview.api.open_external(data.authorization_url);
      } catch (error) {
        console.warn("Could not open the login page automatically", error);
      }
    } else if (popup) {
      popup.location.href = data.authorization_url;
    } else {
      window.open(data.authorization_url, "_blank", "noopener");
    }
    stopAuthPolling();
    state.authPoll = setInterval(pollAuth, 1000);
  } catch (error) {
    if (popup) popup.close();
    toast(error.message, true);
    await refreshAuth().catch(() => {});
  }
}

async function copyAuthLink() {
  if (!state.authUrl) return;
  try {
    await navigator.clipboard.writeText(state.authUrl);
    toast("Login link copied.");
  } catch (error) {
    toast("Could not copy the login link.", true);
  }
}
