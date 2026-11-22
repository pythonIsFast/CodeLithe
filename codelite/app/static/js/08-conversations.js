/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

const KEBAB_ICON =
  `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">` +
  `<circle cx="8" cy="3.5" r="1.3" fill="currentColor"/>` +
  `<circle cx="8" cy="8" r="1.3" fill="currentColor"/>` +
  `<circle cx="8" cy="12.5" r="1.3" fill="currentColor"/></svg>`;

/** Entries rendered per transcript page; older pages load on scroll. */
const TRANSCRIPT_PAGE_SIZE = 60;

const SIDEBAR_ACTIVITY_LABELS = {
  waiting: "Needs your input",
  failed: "Run failed",
  running: "Working",
  finished: "New response",
};

function sidebarActivityDot(extraClass = "") {
  const dot = document.createElement("span");
  dot.className = `conv-activity ${extraClass}`.trim();
  dot.setAttribute("aria-hidden", "true");
  return dot;
}

function setSidebarActivity(button, dot, label, status) {
  if (dot.dataset.status === status) return;
  dot.dataset.status = status;
  if (status) {
    dot.title = SIDEBAR_ACTIVITY_LABELS[status];
    button.setAttribute("aria-label", `${label}, ${dot.title}`);
  } else {
    dot.removeAttribute("title");
    button.removeAttribute("aria-label");
  }
}

function refreshSidebarActivity() {
  const active = state.active?.id;
  const selected = state.sidebarActivity[active];
  if (selected?.status === "finished" || selected?.status === "failed") {
    state.seenSidebarActivity.set(active, selected.run_id);
  }

  const statuses = Object.keys(SIDEBAR_ACTIVITY_LABELS);
  for (const group of el.conversations.querySelectorAll(".conv-group")) {
    const unread = new Set();
    for (const button of group.querySelectorAll(".conv-open")) {
      const id = button.dataset.conversationId;
      const activity = state.sidebarActivity[id];
      const status = activity && state.seenSidebarActivity.get(id) !== activity.run_id
        ? activity.status : "";
      const title = button.querySelector(".conv-title").textContent;
      setSidebarActivity(button, button.querySelector(".conv-activity"), title, status);
      if (status) unread.add(status);
    }
    const head = group.querySelector(".conv-group-head");
    const groupName = head.querySelector(".conv-group-name").textContent;
    setSidebarActivity(head, head.querySelector(".conv-activity"), groupName,
      statuses.find((status) => unread.has(status)) || "");
  }
}

function connectSidebarActivity() {
  const stream = new EventSource("/api/conversation-activity");
  stream.addEventListener("activity", (event) => {
    state.sidebarActivity = JSON.parse(event.data).conversations;
    for (const [id, runId] of state.seenSidebarActivity) {
      if (state.sidebarActivity[id]?.run_id !== runId) state.seenSidebarActivity.delete(id);
    }
    refreshSidebarActivity();
  });
}

/** Close any open conversation menu. */
function closeConversationMenu() {
  const open = el.conversations.querySelector(".conv.menu-open");
  if (open) open.classList.remove("menu-open");
  el.titleButton.classList.remove("menu-open");
  // The menu lives on <body>, not in the row, so it has to be found globally.
  const menu = document.querySelector(".conv-menu");
  if (menu) menu.remove();
}

/** The session title doubles as the rename/delete menu anchor. */
function toggleTitleMenu() {
  if (!state.active) return;
  if (document.querySelector(".conv-menu")) {
    closeConversationMenu();
    return;
  }
  closeConversationMenu();
  el.titleButton.classList.add("menu-open");
  placeConversationMenu(conversationMenu(state.active), el.titleButton);
}

/**
 * Place the menu next to its button using fixed coordinates.
 *
 * It is appended to <body> rather than to the row on purpose: the sidebar's
 * conversation list scrolls, and an absolutely positioned child is clipped by
 * that overflow, so the menu of the bottom-most chat would be cut off.
 */
function placeConversationMenu(menu, anchor) {
  const box = anchor.getBoundingClientRect();
  menu.style.visibility = "hidden";
  document.body.appendChild(menu);
  const height = menu.offsetHeight;
  const width = menu.offsetWidth;
  const margin = 6;
  // Flip above the button when there is no room below it.
  const below = box.bottom + margin;
  const top = below + height > window.innerHeight ? box.top - height - margin : below;
  menu.style.top = `${Math.max(margin, top)}px`;
  menu.style.left = `${Math.max(margin, Math.min(box.right - width, window.innerWidth - width - margin))}px`;
  menu.style.visibility = "";
}

function conversationMenu(conversation) {
  const menu = document.createElement("div");
  menu.className = "conv-menu";
  menu.setAttribute("role", "menu");

  const item = (label, onClick, destructive = false) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "menuitem");
    if (destructive) button.className = "destructive";
    button.textContent = label;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      closeConversationMenu();
      onClick();
    });
    return button;
  };

  menu.append(
    item("Rename", () => askRename(conversation)),
    item("Delete", () => askDelete(conversation), true),
  );
  return menu;
}

/**
 * One sidebar row: a button to open it plus a menu button.
 *
 * A div rather than a button, because it contains two buttons and nesting
 * interactive elements is invalid HTML -- which is why the old delete control
 * was a span pretending to be a button.
 */
function conversationRow(conversation) {
  const row = document.createElement("div");
  row.className = "conv" + (conversation.id === state.active?.id ? " active" : "");

  const open = document.createElement("button");
  open.type = "button";
  open.className = "conv-open";
  open.dataset.conversationId = conversation.id;
  open.title = conversation.workspace;
  const title = document.createElement("span");
  title.className = "conv-title";
  title.textContent = conversation.title || "Untitled";
  open.append(sidebarActivityDot(), title);
  open.addEventListener("click", () => openConversation(conversation.id));

  const menuButton = document.createElement("button");
  menuButton.type = "button";
  menuButton.className = "conv-menu-btn";
  menuButton.innerHTML = KEBAB_ICON;
  menuButton.title = "Conversation options";
  menuButton.setAttribute("aria-label", "Conversation options");
  menuButton.setAttribute("aria-haspopup", "menu");
  menuButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const wasOpen = row.classList.contains("menu-open");
    closeConversationMenu();
    if (wasOpen) return;
    row.classList.add("menu-open");
    placeConversationMenu(conversationMenu(conversation), menuButton);
  });

  row.append(open, menuButton);
  return row;
}

function askRename(conversation) {
  state.pendingRename = conversation;
  el.renameInput.value = conversation.title || "";
  el.renameOverlay.hidden = false;
  el.renameInput.focus();
  el.renameInput.select();
}

async function commitRename() {
  const conversation = state.pendingRename;
  if (!conversation) return;
  const title = el.renameInput.value.trim();
  el.renameOverlay.hidden = true;
  state.pendingRename = null;
  // An empty title is a no-op rather than a way to blank the name: the list
  // would then show "Untitled" and the original would be unrecoverable.
  if (!title || title === conversation.title) return;
  const updated = await patch(`/api/conversations/${conversation.id}`, { title })
    .catch((error) => { toast(error.message, true); return null; });
  if (!updated) return;
  if (state.active?.id === conversation.id) {
    state.active.title = updated.title ?? title;
    el.title.textContent = state.active.title || "Untitled";
  }
  await loadConversations();
}

function askDelete(conversation) {
  state.pendingDelete = conversation;
  el.confirmText.textContent =
    `"${conversation.title || "Untitled"}" and its whole history will be deleted. ` +
    "This cannot be undone.";
  el.confirmOverlay.hidden = false;
  el.confirmCancel.focus();
}

async function commitDelete() {
  const conversation = state.pendingDelete;
  el.confirmOverlay.hidden = true;
  state.pendingDelete = null;
  if (!conversation) return;
  await del(`/api/conversations/${conversation.id}`).catch((e) => toast(e.message, true));
  if (state.active?.id === conversation.id) startNewSession();
  await loadConversations();
}

function renderConversationList() {
  closeConversationMenu();
  el.conversations.replaceChildren();

  const query = state.filter.trim().toLowerCase();
  const visible = query
    ? state.conversations.filter((conversation) =>
        (conversation.title || "").toLowerCase().includes(query) ||
        (conversation.workspace || "").toLowerCase().includes(query))
    : state.conversations;

  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "conv-empty";
    empty.textContent = query ? "No sessions found." : "No sessions yet.";
    el.conversations.appendChild(empty);
    return;
  }

  const groups = new Map(); // label -> { workspace, conversations: [] }
  for (const conversation of visible) {
    const label = workspaceLabel(conversation.workspace);
    if (!groups.has(label)) groups.set(label, { workspace: conversation.workspace, conversations: [] });
    groups.get(label).conversations.push(conversation);
  }

  for (const [label, { workspace, conversations }] of groups) {
    const isActiveGroup = conversations.some((c) => c.id === state.active?.id);
    const wrap = document.createElement("div");
    wrap.className = "conv-group";

    const head = document.createElement("button");
    head.className = "conv-group-head";
    head.type = "button";
    head.innerHTML =
      `${FOLDER_ICON}<span class="conv-group-name"></span>` +
      `<span class="conv-group-add" title="New chat in ${escapeHtml(workspace)}">+</span>`;
    head.querySelector(".conv-group-name").textContent = label;
    head.insertBefore(sidebarActivityDot("conv-group-activity"), head.querySelector(".conv-group-add"));
    head.title = workspace;
    head.addEventListener("click", (event) => {
      if (event.target.closest(".conv-group-add")) return;
      wrap.classList.toggle("collapsed");
    });
    head.querySelector(".conv-group-add").addEventListener("click", (event) => {
      event.stopPropagation();
      startNewSession(workspace);
    });

    const body = document.createElement("div");
    body.className = "conv-group-body";

    for (const conversation of conversations) {
      body.appendChild(conversationRow(conversation));
    }

    if (!isActiveGroup) {
      wrap.classList.toggle("collapsed", !query && groups.size > 1 && conversations.length > 4);
    }
    wrap.append(head, body);
    el.conversations.appendChild(wrap);
  }
  refreshSidebarActivity();
}

async function loadConversations() {
  const data = await get("/api/conversations");
  state.conversations = data.conversations || [];
  renderConversationList();
}


async function openConversation(conversationId) {
  const request = ++state.conversationRequest;
  // Only the newest page of the transcript comes with the open: a long
  // history must not block the conversation from being shown. Older pages
  // load while the user scrolls up (see `maybeLoadOlderEntries`).
  const data = await get(
    `/api/conversations/${conversationId}?limit=${TRANSCRIPT_PAGE_SIZE}`
  ).catch((error) => {
    if (request === state.conversationRequest) toast(error.message, true);
    return null;
  });
  if (!data || request !== state.conversationRequest) return;
  showConversation(data);
}

function showConversation(data) {
  const wasDraft = Boolean(state.draft);
  state.draft = null;
  state.active = data;
  renderChatAgent();
  renderSubagents([]);
  el.composerContext.hidden = true;
  el.titleButton.classList.remove("untitled");
  if (wasDraft) {
    el.prompt.value = "";
    el.prompt.style.height = "auto";
    clearAttachments();
  }
  resetLive();
  state.permissionQueue = [];
  state.currentPermission = null;
  el.permOverlay.hidden = true;
  state.questionQueue = [];
  state.currentQuestion = null;
  el.questionOverlay.hidden = true;

  el.title.textContent = data.title || "Untitled";
  renderDetails();
  el.modeSelect.value = data.permission_mode;
  syncModeChip();
  ensureModelOption(data.model);
  el.modelSelect.value = data.model;
  renderCustomSelect(el.modeSelect);
  renderCustomSelect(el.modelSelect);
  fillEffortSelect(el.effortSelect, data.reasoning_effort || "", data.model);
  renderFastToggle(data.fast_mode, data.model);

  // Pagination state belongs to one conversation only: reset it before the
  // (newest page of the) transcript renders.
  state.entryIds = new Set();
  state.entryCursor = null;
  state.entryHasMore = Boolean(data.has_more);
  renderEntries(data.entries || [], Boolean(data.has_more), false);
  renderConversationList();
  refreshUsageFromConversation();
  setBusy(Boolean(data.busy));
  connectStream(data.id);

  for (const pending of data.pending_permissions || []) state.permissionQueue.push(pending);
  showNextPermission();
  updateSendState();
  scheduleSessionLayout();
}

/**
 * Load the next older page of transcript entries when the user scrolls
 * close to the top. `prependEntries` restores the scroll position, so the
 * viewport stays on the same message instead of jumping. Bounded to one
 * in-flight request; the cursor is the oldest rendered item id.
 */
async function maybeLoadOlderEntries() {
  const conversation = state.active;
  if (!conversation || state.loadingOlder || !state.entryHasMore) return;
  if (state.entryCursor === null || el.messages.scrollTop > 400) return;
  state.loadingOlder = true;
  try {
    const data = await get(
      `/api/conversations/${conversation.id}/entries` +
      `?limit=${TRANSCRIPT_PAGE_SIZE}&before=${state.entryCursor}`
    );
    if (state.active?.id !== conversation.id) return;
    const fresh = (data.entries || []).filter((entry) => !state.entryIds.has(entry.id));
    trackEntryWindow(data.entries || [], Boolean(data.has_more));
    prependEntries(fresh);
    if (!fresh.length && !state.entryHasMore) el.messages.scrollTop = 0;
  } catch {
    // Transient failure: the scroll handler retries on the next trigger.
  } finally {
    state.loadingOlder = false;
  }
}
