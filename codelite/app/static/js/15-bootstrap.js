/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

function startClock() {
  setInterval(() => {
    for (const node of el.messages.querySelectorAll(".msg-time[data-at]")) {
      node.textContent = relativeTime(node.dataset.at);
    }
  }, 60_000);
}

async function loadAuthenticatedApp() {
  await loadModels();
  if (state.appLoaded) return;
  state.appLoaded = true;
  loadPlanUsage();
  await loadConversations();

  if (state.conversations.length) {
    // Resume the newest conversation, but do not block startup on it: the
    // shell renders first, the transcript (newest page only) follows.
    openConversation(state.conversations[0].id).catch(() => {});
  } else {
    // First run: show the new-session screen in the current directory. The
    // conversation itself is created when the first prompt is sent.
    startNewSession();
  }
}

async function init() {
  state.meta = await get("/api/meta");
  el.toolCount.textContent = `${state.meta.tools.length} tools`;

  // The details rail is on by default when there is room for it. Stored
  // choices win, so both panels stay how the user left them.
  let storedSidebar = null;
  let storedDetails = null;
  try {
    storedSidebar = localStorage.getItem("codelite.sidebar-open");
    storedDetails = localStorage.getItem("codelite.details-open");
  } catch {
    // Private mode: use the width defaults instead.
  }
  setSidebarOpen(storedSidebar === null ? true : storedSidebar === "1", false);
  setDetailsOpen(
    storedDetails === null ? window.innerWidth >= 1100 : storedDetails === "1",
    false,
  );
  renderDetails();

  initCustomSelects();
  fillModes(el.modeSelect, state.meta.default_mode);
  wireEvents();
  checkForUpdate();
  loadRemote().catch(() => {});
  startClock();
  const auth = await refreshAuth();
  if (auth.authenticated || auth.free_mode || auth.opencode_go) {
    await loadAuthenticatedApp();
  } else {
    setBusy(false);
  }
}

init()
  .catch((error) => toast(`Startup failed: ${error.message}`, true))
  // Read by `--screenshot` mode: the interface has settled enough to capture,
  // even when startup failed and all it can show is an error toast.
  .finally(() => {
    document.body.dataset.ready = "true";
  });
