/* Part of the Code Lite UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

/* -- Setup ---------------------------------------------------------------- */

function ensureModelOption(model) {
  if (!model || [...el.modelSelect.options].some((option) => option.value === model)) return;
  const option = document.createElement("option");
  option.value = model;
  option.textContent = state.models.includes(model)
    ? (state.modelLabels?.[model] || (model === "auto" ? "Auto" : model))
    : (state.hiddenModels?.includes(model) ? "Hidden model" : "Unavailable model");
  // Existing chats can display a hidden or unavailable model as their current value.
  option.hidden = !state.models.includes(model);
  option.disabled = option.hidden;
  el.modelSelect.appendChild(option);
}

function fillModes(select, selected) {
  select.replaceChildren();
  for (const mode of state.meta.modes) {
    const option = document.createElement("option");
    option.value = mode.value;
    option.textContent = mode.label;
    select.appendChild(option);
  }
  if (selected) select.value = selected;
  renderCustomSelect(select);
  syncModeChip();
}

/** the reference desktop agent paints the manual-ish mode in gold and leaves Auto plain. */
function syncModeChip() {
  const picker = el.modeSelect.closest(".pill-picker");
  if (picker) picker.classList.toggle("mode-gold", el.modeSelect.value === "ask");
}

/**
 * Which reasoning levels a model accepts, from the catalog.
 *
 * Not a constant: Sol and Terra reach "ultra", Luna stops at "max", GPT-5.5 at
 * "xhigh". Offering a level the model rejects earns an HTTP 400, so the list
 * follows the model. Auto has no single answer, so it gets the full set.
 */
function effortsFor(model) {
  if (model?.startsWith("opencode-go/")) return [];
  if (model && model !== "auto") {
    const capability = state.capabilities[model];
    if (capability && Array.isArray(capability.efforts)) return capability.efforts;
  }
  return state.efforts;
}

function supportsFast(model) {
  if (!model) return false;
  // Auto may route to any model, and every routable one offers Fast today.
  if (model === "auto") return Object.values(state.capabilities).some((c) => c.fast);
  const capability = state.capabilities[model];
  return Boolean(capability && capability.fast);
}

/**
 * Fill an effort picker. The empty value comes first and means "use the
 * model's own default", which Codex reports per model -- Sol defaults to low
 * while Terra and Luna default to medium, so hard-coding one here would
 * silently override the model's own tuning.
 */
function fillEffortSelect(select, selected = "", model = "") {
  const efforts = effortsFor(model);
  select.replaceChildren();
  // The composer shows the level alone ("High"), like the reference desktop agent model
  // row; the dialog keeps the explicit "Effort:" prefix because it has no
  // neighbouring model picker to give it context.
  const compact = select.id === "effort-select";
  for (const value of ["", ...efforts]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = compact
      ? (value ? value.charAt(0).toUpperCase() + value.slice(1) : "Auto")
      : (value ? `Effort: ${value}` : "Effort: auto");
    select.appendChild(option);
  }
  select.value = efforts.includes(selected) ? selected : "";
  renderCustomSelect(select);
}

/**
 * Reflect whether this conversation asks for the Fast tier.
 *
 * There is deliberately no "was it granted" state. The response echoes
 * `service_tier: "default"` for every request -- even for a model that has no
 * fast tier at all -- so any such indicator would be invented, and would have
 * reported a working Fast mode as denied.
 */
function renderFastToggle(requested, model) {
  el.fastToggle.hidden = !supportsFast(model);
  const capability = state.capabilities[model];
  el.fastToggle.title = capability?.fast_unavailable_reason || "Fast: 1.5x speed, increased usage";
  el.fastToggle.setAttribute("aria-pressed", String(Boolean(requested)));
}

async function loadModels() {
  const data = await get("/api/models");
  state.modelCatalog = data.models || [];
  state.modelLabels = data.labels || {};
  state.hiddenModels = data.hidden_models || [];
  state.efforts = data.efforts || ["low", "medium", "high"];
  state.capabilities = data.capabilities || {};
  applyModelVisibility();
  refreshBehaviourModels();
  for (const error of data.errors || []) toast(error, true);
}

function applyModelVisibility() {
  state.models = state.modelCatalog.filter((model) => !state.hiddenModels.includes(model));
  const active = state.active;
  const draft = state.draft;
  const preferred = draft?.model || el.modelSelect.value;
  const selected = active?.model || (state.models.includes(preferred) ? preferred : state.models[0]) || "";
  el.modelSelect.replaceChildren();
  for (const model of state.models) ensureModelOption(model);
  ensureModelOption(selected);
  el.modelSelect.value = selected;
  renderCustomSelect(el.modelSelect);
  fillEffortSelect(el.effortSelect, active?.reasoning_effort || draft?.effort || "", selected);
  renderFastToggle(active?.fast_mode ?? draft?.fast, selected);
  if (draft) {
    draft.model = selected;
    draft.effort = el.effortSelect.value;
  }
}
