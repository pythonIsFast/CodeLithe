/* Part of the Code Lite UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

/* -- Conversation list ---------------------------------------------------- */

const FOLDER_ICON =
  `<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">` +
  `<path d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3l1 1.3h6.5a1 1 0 0 1 1 1v7.2a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z"` +
  ` fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>`;

/** The last path segment of a workspace, used as its sidebar group label. */
function workspaceLabel(path) {
  if (!path) return "Workspace";
  const parts = String(path).replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/** Shorten a workspace path for the details rail ("~" for the home folder). */
function displayWorkspace(path) {
  if (!path) return "No folder";
  let value = String(path);
  const home = state.meta?.home;
  if (home && (value === home || value.startsWith(home + "/"))) {
    value = "~" + value.slice(home.length);
  }
  const parts = value.split("/").filter(Boolean);
  if (parts.length > 3) value = "…/" + parts.slice(-2).join("/");
  return value;
}

function permissionLabel(value) {
  if (!value) return "Ask";
  const mode = state.meta?.modes?.find((entry) => entry.value === value);
  return mode ? mode.label : value;
}

/** Paint the environment cards; the context/plan cards update from usage. */
function renderDetails() {
  const active = state.active;
  const draft = state.draft;
  const workspace = active?.workspace || draft?.workspace || state.meta?.cwd || "";
  el.detailWorkspaceName.textContent = workspaceLabel(workspace);
  const parent = workspace.replace(/[\\/][^\\/]+$/, "");
  el.detailWorkspacePath.textContent =
    parent && parent !== workspace ? displayWorkspace(parent) : "";
  el.detailWorkspaceRow.title = workspace;
  el.detailMode.textContent = permissionLabel(
    active?.permission_mode || draft?.mode || state.meta?.default_mode,
  );

  const remoteActive = Boolean(state.remote && state.remote.active);
  el.detailRemoteState.classList.toggle("on", remoteActive);
  el.detailRemote.title = remoteActive
    ? "Remote Control is active — open settings"
    : "Remote Control is off — open settings";

  if (!active) {
    el.detailContextPct.textContent = "—";
    el.detailContextBar.style.width = "0%";
    el.detailContextNote.textContent = draft
      ? "Draft — send a message to start"
      : "No session open";
  }
}

/**
 * Open a fresh session screen without touching the database.
 *
 * The conversation is created in :func:`sendMessage` once the first prompt
 * is actually sent, matching the reference desktop agent new-session flow.
 */
function startNewSession(workspace = null) {
  const previousWorkspace = state.active?.workspace || state.draft?.workspace;
  disconnectStream();
  state.active = null;
  const selected = el.modelSelect.value || state.meta?.default_model;
  state.draft = {
    workspace: workspace || previousWorkspace || state.meta?.cwd || "",
    mode: el.modeSelect.value || state.meta?.default_mode || "ask",
    model: state.models.includes(selected) ? selected : (state.models[0] || ""),
    effort: el.effortSelect.value || "",
    fast: el.fastToggle.getAttribute("aria-pressed") === "true",
    flex: false,
  };

  resetLive();
  state.permissionQueue = [];
  state.currentPermission = null;
  el.permOverlay.hidden = true;
  state.questionQueue = [];
  state.currentQuestion = null;
  el.questionOverlay.hidden = true;

  el.prompt.value = "";
  el.prompt.style.height = "auto";
  clearAttachments();
  el.title.textContent = "New session";
  el.titleButton.classList.add("untitled");
  el.composerContext.hidden = false;
  el.emptyState.hidden = false;
  renderEntries([]);
  renderConversationList();
  setBusy(false);
  resetUsage();
  renderDetails();
  updateDraftControls();
  updateSendState();
  el.prompt.focus();
}

/** Mirror the draft's choices into the composer controls and folder chip. */
function updateDraftControls() {
  const draft = state.draft;
  if (!draft) return;
  el.modeSelect.value = draft.mode;
  renderCustomSelect(el.modeSelect);
  syncModeChip();
  ensureModelOption(draft.model);
  el.modelSelect.value = draft.model;
  renderCustomSelect(el.modelSelect);
  fillEffortSelect(el.effortSelect, draft.effort, draft.model);
  renderFastToggle(draft.fast, draft.model);
  el.draftFolderName.textContent = workspaceLabel(draft.workspace);
  el.draftFolder.title = draft.workspace || "Choose workspace folder";
}

function closeEnvironmentMenu() {
  el.environmentMenu.hidden = true;
  el.draftEnvironment.setAttribute("aria-expanded", "false");
}

async function chooseDraftFolder() {
  if (!state.draft) return;
  if (window.pywebview?.api?.choose_directory) {
    try {
      const chosen = await window.pywebview.api.choose_directory(state.draft.workspace);
      if (!chosen) return;
      state.draft.workspace = chosen;
      updateDraftControls();
      renderDetails();
      return;
    } catch (error) {
      toast(error.message, true);
      return;
    }
  }
  // Headless/browser mode has no native picker; a prompt keeps the folder
  // chooser usable instead of leaving the chip inert.
  const chosen = window.prompt("Workspace folder", state.draft.workspace || "");
  if (!chosen || !chosen.trim()) return;
  state.draft.workspace = chosen.trim();
  updateDraftControls();
  renderDetails();
}

function setDetailsOpen(open, persist = true) {
  state.detailsOpen = open;
  el.app.classList.toggle("details-collapsed", !open);
  el.detailsToggle.setAttribute("aria-pressed", String(open));
  if (persist) {
    try {
      localStorage.setItem("codelite.details-open", open ? "1" : "0");
    } catch {
      // Private mode: the panel still works, it just will not be remembered.
    }
  }
}

function setSidebarOpen(open, persist = true) {
  state.sidebarOpen = open;
  el.app.classList.toggle("sidebar-collapsed", !open);
  el.sidebarToggle.setAttribute("aria-pressed", String(open));
  if (persist) {
    try {
      localStorage.setItem("codelite.sidebar-open", open ? "1" : "0");
    } catch {
      // Private mode: the layout still works, it just will not be remembered.
    }
  }
}
