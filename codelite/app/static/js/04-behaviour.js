/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

/* -- Behaviour settings --------------------------------------------------- */

function behaviourRow(setting, value, models, modelLabels) {
  const row = document.createElement("div");
  row.className = "behaviour-row";

  const label = document.createElement("label");
  label.className = "behaviour-label";
  const name = document.createElement("span");
  name.textContent = setting.label;
  const help = document.createElement("span");
  help.className = "behaviour-help";
  help.textContent = setting.help;
  label.append(name, help);

  const control = document.createElement("div");
  control.className = "behaviour-control";

  if (setting.kind === "bool") {
    const toggle = document.createElement("label");
    toggle.className = "setting-toggle";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = Boolean(value);
    box.dataset.settingKey = setting.key;
    const track = document.createElement("span");
    track.className = "setting-toggle-track";
    toggle.append(box, track);
    control.appendChild(toggle);
  } else if (setting.kind === "models") {
    const list = document.createElement("div");
    list.className = "behaviour-models";
    const selected = new Set(value || []);
    list.dataset.unlistedModels = JSON.stringify([...selected].filter((model) => !models.includes(model)));
    for (const model of models) {
      const pill = document.createElement("label");
      pill.className = "behaviour-model" + (selected.has(model) ? " on" : "");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = selected.has(model);
      box.dataset.model = model;
      box.addEventListener("change", () => pill.classList.toggle("on", box.checked));
      const text = document.createElement("span");
      text.textContent = modelLabels[model] || model;
      pill.append(box, text);
      list.appendChild(pill);
    }
    list.dataset.settingKey = setting.key;
    if (!models.length) list.textContent = "No visible models";
    control.appendChild(list);
  } else if (setting.kind === "model") {
    const picker = document.createElement("div");
    picker.className = "custom-select field-picker";
    picker.setAttribute("data-select-picker", "");
    const select = document.createElement("select");
    select.className = "custom-select-source";
    select.tabIndex = -1;
    select.dataset.settingKey = setting.key;
    select.dataset.configuredModel = value || "";
    if (!models.includes(value)) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = models.length ? "Select a visible model" : "No visible models";
      placeholder.disabled = true;
      select.appendChild(placeholder);
    }
    for (const model of models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = modelLabels[model] || model;
      select.appendChild(option);
    }
    select.value = models.includes(value) ? value : "";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.disabled = !models.length;
    trigger.className = "custom-select-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    const menu = document.createElement("div");
    menu.className = "custom-select-menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    picker.append(select, trigger, menu);
    control.appendChild(picker);
    // A fresh picker every render, so it must be registered with the global
    // click-to-open wiring here -- renderCustomSelect alone only repaints a
    // picker that initCustomSelect already knows about, which is why these
    // dropdowns opened to nothing before.
    initCustomSelect(picker);
  } else {
    const input = document.createElement("input");
    input.type = "number";
    input.dataset.settingKey = setting.key;
    input.dataset.kind = setting.kind;
    if (setting.minimum !== null) input.min = setting.minimum;
    if (setting.maximum !== null) input.max = setting.maximum;
    input.step = setting.step ?? (setting.kind === "float" ? 0.01 : 1);
    input.value = value;
    // The server clamps too, but flagging it here means the user sees which
    // field is out of range rather than a corrected number appearing later.
    input.addEventListener("input", () => {
      const number = Number(input.value);
      const low = setting.minimum !== null && number < setting.minimum;
      const high = setting.maximum !== null && number > setting.maximum;
      input.classList.toggle("out-of-range", input.value !== "" && (low || high));
    });
    control.appendChild(input);
    if (setting.unit) {
      const unit = document.createElement("span");
      unit.className = "behaviour-unit";
      unit.textContent = setting.unit;
      control.appendChild(unit);
    }
  }

  row.append(label, control);
  return row;
}

/** One glyph per Behaviour section, matching the nav's icon style. */
const behaviourPages = {
  provider: ["models", "Pollinations"],
  agent: ["agent", "Defaults"],
  auto: ["agent", "Auto routing"],
  tools: ["agent", "Tool limits"],
  context: ["agent", "Project context"],
  integrations: ["workspace", "Integration limits"],
  danger: ["agent", "Context limits"],
};

function behaviourPanelName(key) {
  return `group:${key}`;
}

/** Keep every group mounted so Save and Restore defaults still cover the full schema. */
function renderBehaviour(schema, values) {
  const activeTab = state.settingsTab;

  for (const old of document.querySelectorAll("[data-behaviour-panel], [data-behaviour-tab]")) {
    for (const select of old.querySelectorAll("select")) customSelects.delete(select);
    old.remove();
  }
  const models = state.modelCatalog ? state.models.filter((model) => model !== "auto") : schema.models || [];
  const modelLabels = state.modelLabels || schema.model_labels || {};

  for (const group of schema.groups) {
    const settings = schema.settings.filter((setting) => setting.group === group.key);
    if (!settings.length) continue;
    const tab = behaviourPanelName(group.key);

    const [category, label] = behaviourPages[group.key] || ["agent", group.label];
    const item = document.createElement("button");
    item.type = "button";
    item.className = "settings-nav-item";
    item.dataset.settingsTab = tab;
    item.dataset.behaviourTab = "";
    item.dataset.description = group.help;
    item.textContent = label;
    document.querySelector(`[data-settings-pages="${category}"]`).appendChild(item);

    const panel = document.createElement("section");
    panel.className = "settings-panel" + (group.key === "danger" ? " danger" : "");
    panel.dataset.settingsPanel = tab;
    panel.dataset.behaviourPanel = group.key;
    panel.hidden = true;

    const card = document.createElement("div");
    card.className = "settings-card";
    for (const setting of settings) {
      card.appendChild(behaviourRow(setting, values[setting.key], models, modelLabels));
    }
    panel.appendChild(card);

    const actions = document.createElement("div");
    actions.className = "behaviour-actions";
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "btn";
    reset.textContent = "Restore all defaults";
    reset.dataset.behaviourReset = "";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "btn primary";
    save.textContent = "Save agent settings";
    save.dataset.behaviourSave = "";
    actions.append(reset, save);
    panel.appendChild(actions);

    const status = document.createElement("p");
    status.className = "auth-status behaviour-status";
    status.setAttribute("role", "status");
    panel.appendChild(status);

    el.settingsContent.appendChild(panel);
  }

  // A Save re-renders the panels; keep the section the user was on visible.
  if (activeTab?.startsWith("group:")) selectSettingsTab(activeTab, { clearSearch: false });
  renderSettingsSearch();
}

async function loadBehaviour() {
  const data = await get("/api/settings/behaviour");
  state.behaviour = data;
  renderBehaviour(data.schema, data.values);
}

function collectBehaviour() {
  const values = {};
  const scope = "[data-behaviour-panel]";
  for (const input of document.querySelectorAll(`${scope} input[type=checkbox][data-setting-key]`)) {
    values[input.dataset.settingKey] = input.checked;
  }
  for (const input of document.querySelectorAll(`${scope} input[type=number]`)) {
    const raw = Number(input.value);
    if (input.value === "" || Number.isNaN(raw)) continue;
    values[input.dataset.settingKey] = input.dataset.kind === "int" ? Math.round(raw) : raw;
  }
  for (const select of document.querySelectorAll(`${scope} select[data-setting-key]`)) {
    values[select.dataset.settingKey] = select.value || select.dataset.configuredModel;
  }
  for (const list of document.querySelectorAll(`${scope} [data-setting-key].behaviour-models`)) {
    // An unavailable choice stays stored so editing other settings cannot drop it.
    const checked = [...list.querySelectorAll("input:checked")].map((box) => box.dataset.model);
    values[list.dataset.settingKey] = [...JSON.parse(list.dataset.unlistedModels), ...checked];
  }
  return values;
}

function refreshBehaviourModels() {
  if (state.behaviour && !el.settingsOverlay.hidden) {
    renderBehaviour(state.behaviour.schema, collectBehaviour());
  }
}

async function saveBehaviour(values) {
  const previousFree = Boolean(state.behaviour?.values?.use_pollinations_free);
  const data = await api("/api/settings/behaviour", {
    method: "PUT",
    body: JSON.stringify({ values }),
  });
  // Re-render from what the server stored: it clamps, so the form must show
  // the value that is actually in effect, not the one that was typed.
  state.behaviour = { ...state.behaviour, values: data.values };
  renderBehaviour(state.behaviour.schema, data.values);
  state.meta = await get("/api/meta");
  await loadModels();
  const freeChanged = previousFree !== Boolean(data.values.use_pollinations_free);
  if (freeChanged && state.active && !/^(opencode-go|custom)\//.test(state.active.model)) {
    const model = data.values.use_pollinations_free ? "openai-fast" : state.meta.default_model;
    state.active = await patch(`/api/conversations/${state.active.id}`, { model });
    ensureModelOption(model);
    el.modelSelect.value = model;
    renderCustomSelect(el.modelSelect);
  }
  await refreshAuth();
  const clamped = Object.keys(data.values).filter(
    (key) => String(data.values[key]) !== String(values[key]) && key in values,
  );
  // The status line was replaced with the re-render; write into the panel
  // that is visible now.
  const status = document.querySelector(".settings-panel:not([hidden]) .behaviour-status");
  if (status) {
    status.textContent = clamped.length
      ? `Saved. ${clamped.length} value(s) were adjusted to their allowed range.`
      : "Saved.";
  }
  toast("Settings saved.");
}

async function loadCodexImport() {
  const data = await api("/api/import/codex");
  if (!data.found) {
    el.importSummary.textContent =
      `No Codex installation found at ${data.codex_home}.`;
    el.codexImport.disabled = true;
    return;
  }
  const parts = [`${plural(data.available, "session")} in ${data.codex_home}`];
  if (data.already_imported) parts.push(`${data.already_imported} already imported`);
  el.importSummary.textContent = parts.join(" · ");
  el.codexImport.disabled = data.new === 0;
  el.codexImport.textContent = data.new
    ? `Import ${plural(data.new, "session")}`
    : "Nothing new to import";
}

async function runCodexImport() {
  el.codexImport.disabled = true;
  // No progress channel exists for this, so say plainly that a long wait is
  // the import working rather than the window having frozen.
  el.importStatus.textContent = "Importing… this can take a while on a large history.";
  try {
    const report = await api("/api/import/codex", { method: "POST" });
    const parts = [`Imported ${plural(report.imported, "conversation")}`];
    if (report.skipped) parts.push(`${report.skipped} already present`);
    if (report.empty) parts.push(`${report.empty} empty`);
    if (report.failed) parts.push(`${report.failed} failed`);
    el.importStatus.textContent = `${parts.join(", ")}.`;
    // The first error is the useful one; the rest are usually the same cause.
    if (report.errors && report.errors.length) {
      el.importStatus.textContent += ` First error: ${report.errors[0]}`;
    }
    if (report.imported) {
      await loadConversations();
      toast(`Imported ${plural(report.imported, "conversation")} from Codex.`);
    }
  } finally {
    await loadCodexImport().catch(() => {});
  }
}

async function saveProjectSetting(kind, textarea) {
  if (!state.active) return;
  const data = await api(`/api/conversations/${state.active.id}/project-settings/${kind}`, {
    method: "PUT",
    body: JSON.stringify({ content: textarea.value }),
  });
  textarea.value = data.content ?? textarea.value;
  toast(`${kind.toUpperCase()} settings saved.`);
}
