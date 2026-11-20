/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

function startClock() {
  setInterval(() => {
    for (const node of el.messages.querySelectorAll(".msg-time[data-at]")) {
      node.textContent = relativeTime(node.dataset.at);
    }
    tickGoPlanUsage();
  }, 60_000);
}

async function loadAuthenticatedApp(startup = null) {
  loadPlanUsage();
  if (state.appLoaded) {
    await loadModels();
    return;
  }
  state.appLoaded = true;
  connectSidebarActivity();

  // The account status and visibility preferences are local. They give a
  // returning user a usable default while the full catalog fetches remotely.
  state.hiddenModels = state.meta.hidden_models || [];
  if (!state.modelCatalog) {
    const starterModels = [];
    if (state.auth.free_mode) starterModels.push(state.meta.default_model);
    else if (state.auth.authenticated) starterModels.push("auto");
    state.models = starterModels.filter((model) => !state.hiddenModels.includes(model));
    applyModelVisibility();
  }
  loadModels().catch((error) => toast(error.message, true));

  const request = state.conversationRequest;
  if (startup) {
    state.conversations = startup.conversations;
  } else {
    await loadConversations();
  }
  if (request !== state.conversationRequest) return;
  if (startup?.latest) {
    showConversation(startup.latest);
  } else if (state.conversations.length) {
    // Large transcripts are fetched after the sidebar is ready.
    if (startup) renderConversationList();
    openConversation(state.conversations[0].id);
  } else {
    startNewSession();
  }
}

async function init() {
  const node = $("startup-data");
  const startup = JSON.parse(node.textContent);
  node.remove();
  const { meta, auth } = startup;
  state.meta = meta;
  renderAuth(auth);
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
  if (auth.authenticated || auth.free_mode || auth.opencode_go || auth.custom_providers) {
    await loadAuthenticatedApp(startup);
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
