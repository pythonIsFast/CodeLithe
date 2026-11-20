/* Part of the Code Lite UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

async function loadProjectSettings() {
  el.projectSettingsTab.hidden = !state.active;
  if (!state.active) return;
  const settings = await get(`/api/conversations/${state.active.id}/project-settings`);
  el.projectSettingsPath.textContent = settings.project_dir;
  el.projectMemory.value = settings.memory || "";
  el.projectMcpConfig.value = settings.mcp || "";
  el.projectLspConfig.value = settings.lsp || "";
  const skills = settings.skills || [];
  el.projectSkills.textContent = skills.length
    ? `Skills: ${skills.map((skill) => skill.name).join(", ")}`
    : "Skills: none. Add Markdown skills under .codelite/skills/.";
}

function renderRemote(remote) {
  state.remote = remote;
  el.remoteUnsupported.hidden = remote.supported;
  el.remoteDownload.hidden = !remote.supported || remote.installed;
  el.remoteDownloadHint.hidden = !remote.supported || remote.installed;
  el.remoteReady.hidden = !remote.supported || !remote.installed || remote.active;
  el.remoteActive.hidden = !remote.active;
  el.sidebarRemote.hidden = !remote.active;
  if (remote.active) {
    el.remoteUrl.value = remote.url || "Starting…";
    el.remotePassword.value = state.remotePassword || "Shown only when the tunnel starts";
  }
  renderDetails();
}

async function loadRemote() {
  renderRemote(await get("/api/remote"));
}

async function downloadRemote() {
  el.remoteDownloadButton.disabled = true;
  el.remoteStatus.textContent = "Downloading and verifying cloudflared…";
  try {
    renderRemote(await post("/api/remote/download"));
    el.remoteStatus.textContent = "cloudflared is ready.";
  } catch (error) {
    el.remoteStatus.textContent = error.message;
  } finally {
    el.remoteDownloadButton.disabled = false;
  }
}

async function startRemote() {
  el.remoteStart.disabled = true;
  el.remoteStatus.textContent = "Starting secure tunnel…";
  try {
    const remote = await post("/api/remote/start");
    state.remotePassword = remote.password || "";
    renderRemote(remote);
    el.remoteStatus.textContent = "Remote Control is active.";
  } catch (error) {
    el.remoteStatus.textContent = error.message;
  } finally {
    el.remoteStart.disabled = false;
  }
}

async function stopRemote() {
  el.remoteStop.disabled = true;
  try {
    state.remotePassword = "";
    renderRemote(await post("/api/remote/stop"));
    el.remoteStatus.textContent = "Remote Control stopped.";
  } catch (error) {
    el.remoteStatus.textContent = error.message;
  } finally {
    el.remoteStop.disabled = false;
  }
}

async function removeRemote() {
  el.remoteRemove.disabled = true;
  try {
    state.remotePassword = "";
    renderRemote(await post("/api/remote/remove"));
    el.remoteStatus.textContent = "cloudflared removed.";
  } catch (error) {
    el.remoteStatus.textContent = error.message;
  } finally {
    el.remoteRemove.disabled = false;
  }
}

async function copyRemoteValue(input, label) {
  try {
    await navigator.clipboard.writeText(input.value);
    toast(`${label} copied.`);
  } catch {
    input.select();
    toast(`Select and copy the ${label.toLowerCase()}.`);
  }
}

async function loadBankedResets() {
  el.bankedResetsRedeem.disabled = true;
  el.bankedResetsStatus.textContent = "Checking…";
  try {
    const data = await get("/api/usage/banked-resets");
    state.bankedResets = data.credits || [];
    el.bankedResets.hidden = false;
    if (data.error) {
      el.bankedResetsStatus.textContent = "Could not check for banked resets.";
      el.bankedResetsRedeem.hidden = true;
      return;
    }
    const available = state.bankedResets.filter((c) => c.status === "available");
    if (!available.length) {
      el.bankedResetsStatus.textContent = "No banked resets available right now.";
      el.bankedResetsRedeem.hidden = true;
      return;
    }
    el.bankedResetsStatus.textContent =
      available.length === 1
        ? "1 banked reset available."
        : `${available.length} banked resets available.`;
    el.bankedResetsRedeem.hidden = false;
  } catch (error) {
    // This is an undocumented, best-effort feature -- a failure here (e.g.
    // an account with no such entitlement) should not look like a real error.
    el.bankedResets.hidden = true;
  } finally {
    el.bankedResetsRedeem.disabled = false;
  }
}

async function redeemBankedReset() {
  const available = (state.bankedResets || []).filter((c) => c.status === "available");
  if (!available.length) return;
  el.bankedResetsRedeem.disabled = true;
  el.bankedResetsRedeem.textContent = "Activating…";
  try {
    await post(`/api/usage/banked-resets/${encodeURIComponent(available[0].id)}/redeem`, {});
    toast("Banked reset activated. The weekly ring updates with your next message.");
    await loadBankedResets();
  } catch (error) {
    toast(error.message, true);
  } finally {
    el.bankedResetsRedeem.textContent = "Activate a banked reset";
    el.bankedResetsRedeem.disabled = false;
  }
}

async function checkForUpdate() {
  el.updateButton.disabled = true;
  el.updateStatus.textContent = "Checking for updates…";
  try {
    const update = await get("/api/update");
    state.update = update;
    el.settingsUpdate.hidden = false;
    el.sidebarUpdate.hidden = !(update.supported && update.available && update.asset_available);
    if (!update.supported) {
      el.updateStatus.textContent = "Automatic updates are unavailable for this installation.";
      el.updateButton.hidden = true;
      return;
    }
    el.updateButton.hidden = false;
    if (!el.sidebarUpdate.hidden) {
      el.sidebarUpdate.textContent = `Update ${update.latest_version} available`;
    }
    if (!update.available) {
      el.updateStatus.textContent = `Code Lite ${update.current_version} is up to date.`;
      el.updateButton.textContent = "Check again";
    } else if (!update.asset_available) {
      el.updateStatus.textContent = `Version ${update.latest_version} is available, but its installer is missing.`;
      el.updateButton.textContent = "Check again";
    } else {
      el.updateStatus.textContent = `Version ${update.latest_version} is available (installed: ${update.current_version}).`;
      el.updateButton.textContent = "Install update";
    }
  } catch (error) {
    el.sidebarUpdate.hidden = true;
    el.settingsUpdate.hidden = false;
    el.updateStatus.textContent = error.message;
    el.updateButton.hidden = false;
    el.updateButton.textContent = "Try again";
  } finally {
    el.updateButton.disabled = false;
  }
}

async function runUpdate() {
  if (!state.update?.available || !state.update?.asset_available) {
    await checkForUpdate();
    return;
  }
  el.updateButton.disabled = true;
  el.updateStatus.textContent = "Downloading and verifying the update…";
  try {
    const result = await post("/api/update");
    el.updateStatus.textContent = `Preparing Code Lite ${result.version} for installation…`;
    el.updateButton.textContent = "Restarting for update";
    if (window.pywebview?.api?.quit_for_update) {
      await window.pywebview.api.quit_for_update();
    }
  } catch (error) {
    el.updateStatus.textContent = error.message;
    el.updateButton.textContent = "Try again";
    el.updateButton.disabled = false;
  }
}

async function loadGlobalSettings() {
  const settings = await get("/api/settings");
  el.globalMemory.value = settings.memory || "";
  el.globalMemoryPath.textContent = settings.memory_path || "";
}

async function saveGlobalMemory() {
  const data = await api("/api/settings/memory", {
    method: "PUT",
    body: JSON.stringify({ content: el.globalMemory.value }),
  });
  el.globalMemory.value = data.content ?? el.globalMemory.value;
  toast("Global memory saved.");
}

function settingsPages() {
  return [...el.settingsOverlay.querySelectorAll("[data-settings-tab]")].filter((page) => !page.hidden);
}

function selectSettingsTab(name, { clearSearch = true } = {}) {
  const pages = settingsPages();
  const selected = pages.find((page) => page.dataset.settingsTab === name) || pages[0];
  const category = selected.closest("[data-settings-pages]").dataset.settingsPages;
  state.settingsTab = selected.dataset.settingsTab;
  for (const button of el.settingsOverlay.querySelectorAll("[data-settings-category]")) {
    if (button.dataset.settingsCategory === category) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
  for (const group of el.settingsOverlay.querySelectorAll("[data-settings-pages]")) {
    group.hidden = group.dataset.settingsPages !== category;
  }
  for (const page of pages) {
    if (page === selected) page.setAttribute("aria-current", "page");
    else page.removeAttribute("aria-current");
  }
  $("settings-page-category").textContent =
    el.settingsOverlay.querySelector("[data-settings-category][aria-current]").textContent;
  $("settings-page-title").textContent = selected.textContent.trim();
  $("settings-page-description").textContent = selected.dataset.description;
  for (const panel of el.settingsContent.querySelectorAll("[data-settings-panel]")) {
    panel.hidden = panel.dataset.settingsPanel !== state.settingsTab;
  }
  if (clearSearch) el.settingsSearch.value = "";
  renderSettingsSearch();
  el.settingsContent.scrollTop = 0;
  if (state.settingsTab === "opencode-go") {
    loadGoSettings().catch((error) => toast(error.message, true));
    refreshGoUsage();
  }
  if (state.settingsTab === "model-picker") renderModelVisibility();
}

function renderSettingsSearch() {
  const results = $("settings-search-results");
  const query = el.settingsSearch.value.trim().toLowerCase();
  el.settingsContent.classList.toggle("settings-searching", Boolean(query));
  results.hidden = !query;
  results.replaceChildren();
  if (!query) return;

  const heading = document.createElement("h3");
  heading.textContent = "Search results";
  results.appendChild(heading);

  const words = query.split(/\s+/);
  let count = 0;
  const panels = new Map([...el.settingsContent.querySelectorAll("[data-settings-panel]")]
    .map((panel) => [panel.dataset.settingsPanel, panel]));
  for (const page of settingsPages()) {
    const name = page.dataset.settingsTab;
    const panel = panels.get(name);
    const fields = panel ? [...panel.querySelectorAll(".settings-row-title, .behaviour-label")]
      .map((field) => field.textContent.trim()) : [];
    // Search authored labels and help rather than the values in credentials and editors.
    const searchable = [page.textContent, page.dataset.description, ...fields].join(" ").toLowerCase();
    if (!words.every((word) => searchable.includes(word))) continue;
    count += 1;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "settings-result";
    button.dataset.settingsTarget = name;
    const title = document.createElement("strong");
    title.textContent = page.textContent.trim();
    const detail = document.createElement("span");
    const matchingField = fields.find((field) => words.every((word) => field.toLowerCase().includes(word)));
    detail.textContent = matchingField || page.dataset.description;
    button.append(title, detail);
    results.appendChild(button);
  }
  if (!count) {
    const empty = document.createElement("p");
    empty.className = "account-detail";
    empty.textContent = "No settings found.";
    results.appendChild(empty);
  }
}

function plural(count, singular, suffix = "s") {
  return `${count} ${singular}${count === 1 ? "" : suffix}`;
}


async function openSettings(tab = state.settingsTab || "account") {
  if (el.settingsOverlay.hidden) state.settingsOpener = document.activeElement;
  el.settingsOverlay.classList.toggle("provider-setup", !el.authOverlay.hidden);
  el.settingsOverlay.hidden = false;
  el.projectSettingsTab.hidden = !state.active;
  selectSettingsTab(tab);
  el.settingsClose.focus();

  const initialPage = state.settingsTab;
  const behaviourReady = loadBehaviour().then(() => {
    if (!el.settingsOverlay.hidden && state.settingsTab === initialPage &&
        initialPage !== tab && tab.startsWith("group:") && !el.settingsSearch.value) {
      selectSettingsTab(tab);
    }
  });
  await Promise.allSettled([
    behaviourReady,
    refreshAuth(),
    loadGlobalSettings(),
    loadProjectSettings(),
    loadCodexImport(),
    checkForUpdate(),
    loadRemote(),
    loadBankedResets(),
  ]).then((results) => {
    for (const result of results) {
      if (result.status === "rejected") toast(result.reason.message, true);
    }
  });
}

function closeSettings() {
  el.settingsOverlay.hidden = true;
  $("go-keys").reset();
  state.settingsOpener?.focus();
}

async function loadGoSettings() {
  const status = await get("/api/settings/opencode-go");
  $("go-api-key-status").textContent = status.api_key_configured ? "Key saved." : "No key saved.";
  $("go-usage-key-status").textContent = status.usage_key_configured
    ? "Key saved." : "Requires Usage: Read.";
  $("go-remove-api-key").disabled = !status.api_key_configured;
  $("go-remove-usage-key").disabled = !status.usage_key_configured;
}

async function refreshGoUsage() {
  const target = $("go-usage");
  const button = $("go-refresh-usage");
  if (button.disabled) return;
  button.disabled = true;
  target.textContent = "Loading…";
  try {
    const { usage } = await get("/api/usage/opencode-go");
    target.replaceChildren();
    for (const [key, label] of [["rolling", "5-hour"], ["weekly", "Weekly"], ["monthly", "Monthly"]]) {
      const period = usage[key];
      if (!period) continue;
      const row = document.createElement("div");
      row.className = "settings-meter";
      const title = document.createElement("strong");
      title.textContent = label;
      const value = document.createElement("span");
      value.className = "settings-meter-value";
      value.textContent = `${period.percent}% used`;
      const meter = document.createElement("progress");
      meter.max = 100;
      meter.value = period.percent;
      meter.setAttribute("aria-label", `${label} allowance used`);
      row.append(title, value, meter);
      const reset = period.resets_at ? new Date(period.resets_at) : null;
      if (reset && Number.isFinite(reset.getTime())) {
        const note = document.createElement("small");
        note.textContent = `Resets ${reset.toLocaleString()}`;
        row.appendChild(note);
      }
      target.appendChild(row);
    }
    if (!target.children.length) target.textContent = "Add a service account key to view usage.";
  } catch (error) {
    target.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function saveGoKeys(changes) {
  const form = $("go-keys");
  const controls = [...form.elements];
  const status = $("go-key-status");
  controls.forEach((control) => { control.disabled = true; });
  try {
    await api("/api/settings/opencode-go", { method: "PUT", body: JSON.stringify(changes) });
    form.reset();
    status.textContent = "Keys saved.";
    const auth = await refreshAuth();
    if (auth.authenticated || auth.free_mode || auth.opencode_go) {
      await loadAuthenticatedApp();
    } else if (state.appLoaded) {
      await loadModels();
    }
    refreshGoUsage();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    controls.forEach((control) => { control.disabled = false; });
    loadGoSettings().catch((error) => toast(error.message, true));
  }
}

function renderModelVisibility() {
  const list = $("model-visibility-list");
  list.replaceChildren();
  for (const model of state.modelCatalog || []) {
    const row = document.createElement("label");
    row.className = "settings-row";
    const title = document.createElement("span");
    title.className = "settings-row-text";
    title.textContent = state.modelLabels[model] || model;
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.value = model;
    toggle.checked = !state.hiddenModels.includes(model);
    row.append(title, toggle);
    list.appendChild(row);
  }
  if (!list.children.length) list.textContent = "No models available.";
}

async function saveModelVisibility(event) {
  event.preventDefault();
  const status = $("model-visibility-status");
  const inputs = [...$("model-visibility-list").querySelectorAll("input")];
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  const hidden = new Set(state.hiddenModels || []);
  for (const input of inputs) {
    if (input.checked) hidden.delete(input.value);
    else hidden.add(input.value);
  }
  try {
    await api("/api/settings/model-picker", {
      method: "PUT", body: JSON.stringify({ hidden_models: [...hidden] }),
    });
    state.hiddenModels = [...hidden];
    if (state.modelCatalog) applyModelVisibility();
    status.textContent = "Model visibility saved.";
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
