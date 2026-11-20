/*
 * Copyright 2026 Code Lite contributors
 * Licensed under the Apache License, Version 2.0.
 *
 * Vanilla JS, no framework, no build step. The SSE stream for the active
 * conversation stays open for as long as it is selected, so a run's events
 * can never arrive before the UI is listening.
 */

"use strict";

const $ = (id) => document.getElementById(id);

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

const el = {
  conversations: $("conversations"),
  messages: $("messages"),
  emptyState: $("empty-state"),
  title: $("chat-title"),
  titleButton: $("title-button"),
  app: $("app"),
  sidebarToggle: $("sidebar-toggle"),
  sideSearch: $("side-search"),
  sideSearchField: $("side-search-field"),
  conversationFilter: $("conversation-filter"),
  sideCustomize: $("side-customize"),
  detailsToggle: $("details-toggle"),
  topbarSettings: $("topbar-settings"),
  detailWorkspaceRow: $("detail-workspace-row"),
  detailWorkspaceName: $("detail-workspace-name"),
  detailWorkspacePath: $("detail-workspace-path"),
  detailMode: $("detail-mode"),
  detailRemote: $("detail-remote"),
  detailRemoteState: $("detail-remote-state"),
  detailContextPct: $("detail-context-pct"),
  detailContextBar: $("detail-context-bar"),
  detailContextNote: $("detail-context-note"),
  detailPlanCard: $("detail-plan-card"),
  detailPlanChatGPT: $("detail-plan-chatgpt"),
  detailPlanChatGPTWindows: $("detail-plan-chatgpt-windows"),
  detailPlanGo: $("detail-plan-go"),
  detailPlanGoWindows: $("detail-plan-go-windows"),
  modelSelect: $("model-select"),
  effortSelect: $("effort-select"),
  fastToggle: $("fast-toggle"),
  modeSelect: $("mode-select"),
  compactContext: $("compact-context"),
  attachmentPicker: $("attachment-picker"),
  attachFiles: $("attach-files"),
  composer: $("composer"),
  prompt: $("prompt"),
  attachments: $("composer-attachments"),
  send: $("send"),
  statusBar: $("status-bar"),
  statusText: $("status-text"),
  stopRun: $("stop-run"),
  usage: $("usage"),
  usageRingFill: $("usage-ring-fill"),
  usageLabel: $("usage-label"),
  newChat: $("new-chat"),
  toolCount: $("tool-count"),
  sidebarUpdate: $("sidebar-update"),
  sidebarRemote: $("sidebar-remote"),
  toast: $("toast"),
  permOverlay: $("permission-overlay"),
  permKind: $("perm-kind"),
  permDetail: $("perm-detail"),
  permScope: $("perm-scope"),
  permDiff: $("perm-diff"),
  permJudge: $("perm-judge"),
  permJudgeReason: $("perm-judge-reason"),
  permFeedback: $("perm-feedback"),
  permOnce: $("perm-once"),
  permSession: $("perm-session"),
  permDeny: $("perm-deny"),
  questionOverlay: $("question-overlay"),
  questionTitle: $("question-title"),
  questionText: $("question-text"),
  questionOptions: $("question-options"),
  questionAnswer: $("question-answer"),
  questionCancel: $("question-cancel"),
  questionSubmit: $("question-submit"),
  composerContext: $("composer-context"),
  draftEnvironment: $("draft-environment"),
  environmentMenu: $("environment-menu"),
  environmentRemote: $("environment-remote"),
  draftFolder: $("draft-folder"),
  draftFolderName: $("draft-folder-name"),
  renameOverlay: $("rename-overlay"),
  renameInput: $("rename-input"),
  renameSave: $("rename-save"),
  renameCancel: $("rename-cancel"),
  confirmOverlay: $("confirm-overlay"),
  confirmText: $("confirm-text"),
  confirmOk: $("confirm-ok"),
  confirmCancel: $("confirm-cancel"),
  settingsButton: $("settings-button"),
  settingsOverlay: $("settings-overlay"),
  settingsClose: $("settings-close"),
  settingsSearch: $("settings-search"),
  settingsContent: document.querySelector(".settings-content"),
  settingsLogin: $("settings-login"),
  accountName: $("account-name"),
  accountDetail: $("account-detail"),
  settingsAuthStatus: $("settings-auth-status"),
  settingsAuthManual: $("settings-auth-manual"),
  settingsAuthOpenLink: $("settings-auth-open-link"),
  settingsAuthCopyLink: $("settings-auth-copy-link"),
  settingsUpdate: $("settings-update"),
  bankedResets: $("banked-resets"),
  bankedResetsStatus: $("banked-resets-status"),
  bankedResetsRedeem: $("banked-resets-redeem"),
  remoteUnsupported: $("remote-unsupported"),
  remoteDownload: $("remote-download"),
  remoteDownloadHint: $("remote-download-hint"),
  remoteDownloadButton: $("remote-download-button"),
  remoteReady: $("remote-ready"),
  remoteStart: $("remote-start"),
  remoteRemove: $("remote-remove"),
  remoteActive: $("remote-active"),
  remoteUrl: $("remote-url"),
  remotePassword: $("remote-password"),
  remoteCopyUrl: $("remote-copy-url"),
  remoteCopyPassword: $("remote-copy-password"),
  remoteStop: $("remote-stop"),
  remoteStatus: $("remote-status"),
  updateStatus: $("update-status"),
  updateButton: $("update-button"),
  globalMemory: $("global-memory"),
  globalMemoryPath: $("global-memory-path"),
  globalMemorySave: $("global-memory-save"),
  importSummary: $("import-summary"),
  importStatus: $("import-status"),
  codexImport: $("codex-import"),
  projectSettingsTab: $("project-settings-tab"),
  projectSettings: $("project-settings"),
  projectSettingsPath: $("project-settings-path"),
  projectMemory: $("project-memory"),
  projectMemorySave: $("project-memory-save"),
  projectSkills: $("project-skills"),
  projectMcpConfig: $("project-mcp-config"),
  projectMcpSave: $("project-mcp-save"),
  projectLspConfig: $("project-lsp-config"),
  projectLspSave: $("project-lsp-save"),
  authOverlay: $("auth-overlay"),
  authLogin: $("auth-login"),
  authFree: $("auth-free"),
  authStatus: $("auth-status"),
  authManual: $("auth-manual"),
  authOpenLink: $("auth-open-link"),
  authCopyLink: $("auth-copy-link"),
};

const state = {
  meta: null,
  models: [],
  loadingModels: false,
  conversationRequest: 0,
  efforts: [],
  capabilities: {},
  behaviour: null,
  pendingRename: null,
  pendingDelete: null,
  conversations: [],
  sidebarActivity: {},
  seenSidebarActivity: new Map(),
  active: null,
  // A new session that exists only in the interface until its first message:
  // no database row, no sidebar entry, no stream.
  draft: null,
  filter: "",
  sidebarOpen: true,
  detailsOpen: true,
  stream: null,
  busy: false,
  liveText: null,
  liveReasoning: null,
  liveTodos: null,
  lastTurnOutputTokens: null,
  toolCards: new Map(),
  permissionQueue: [],
  questionQueue: [],
  currentPermission: null,
  currentQuestion: null,
  auth: null,
  appLoaded: false,
  authPoll: null,
  authUrl: null,
  update: null,
  remote: null,
  remotePassword: "",
  attachments: [],
  uploadsInProgress: 0,
  busyWatchdog: null,
  lastStreamEventAt: 0,
  reconciling: false,
  pendingMessages: new Set(),
  // Transcript pagination: only the newest page of entries is rendered when
  // a conversation opens; older pages load while the user scrolls up.
  entryIds: new Set(),
  entryCursor: null,
  entryHasMore: false,
  loadingOlder: false,
};

/* -- API ------------------------------------------------------------------ */

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

const get = (path) => api(path);
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
const patch = (path, body) => api(path, { method: "PATCH", body: JSON.stringify(body) });
const del = (path) => api(path, { method: "DELETE" });


function toast(message, isError = false) {
  el.toast.textContent = message;
  el.toast.classList.toggle("error", isError);
  el.toast.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { el.toast.hidden = true; }, isError ? 7000 : 3500);
}
