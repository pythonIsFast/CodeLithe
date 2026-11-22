/* Agent profile editor and live delegation status. */

let agentProfiles = [{ id: "general", name: "General", builtin: true }];
let editingAgent = null;

function addAgentOption(select, value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  select.append(option);
}

function renderChatAgent() {
  const select = $("chat-agent");
  const desired = state.active?.agent_id || state.draft?.agentId || "general";
  select.replaceChildren();
  for (const profile of agentProfiles) addAgentOption(select, profile.id, profile.name);
  select.value = agentProfiles.some((profile) => profile.id === desired) ? desired : "general";
  select.disabled = !state.draft;
}

function editAgentProfile(profile = null, duplicate = false) {
  editingAgent = duplicate ? null : profile?.id || null;
  $("agent-profile-name").value = duplicate ? `${profile.name} copy`.slice(0, 60) : profile?.name || "";
  $("agent-profile-instructions").value = profile?.instructions || "";
  const model = $("agent-profile-model");
  model.replaceChildren();
  addAgentOption(model, "", "Use chat model");
  for (const id of state.models) addAgentOption(model, id, state.modelLabels?.[id] || id);
  if (profile?.model && !state.models.includes(profile.model)) {
    addAgentOption(model, profile.model, "Unavailable model");
  }
  model.value = profile?.model || "";
  fillAgentProfileEffort(profile?.effort || "");
  $("agent-profile-limit-tools").checked = Array.isArray(profile?.tools);
  const tools = $("agent-profile-tools");
  tools.replaceChildren();
  for (const name of state.meta.tools.filter((tool) => !["agent_message", "extensions"].includes(tool))) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = name;
    input.checked = profile?.tools?.includes(name) || false;
    label.append(input, document.createTextNode(name));
    tools.append(label);
  }
  tools.hidden = !$("agent-profile-limit-tools").checked;
}

function fillAgentProfileEffort(effort = "") {
  const picker = $("agent-profile-effort");
  picker.replaceChildren();
  addAgentOption(picker, "", "Auto");
  for (const value of effortsFor($("agent-profile-model").value)) addAgentOption(picker, value, value);
  picker.value = [...picker.options].some((option) => option.value === effort) ? effort : "";
}

async function loadAgentProfiles() {
  ({ agents: agentProfiles } = await get("/api/agents"));
  renderChatAgent();
  const list = $("agent-profile-list");
  list.replaceChildren();
  for (const profile of agentProfiles) {
    const row = document.createElement("div");
    row.className = "settings-row";
    const name = document.createElement("strong");
    name.textContent = profile.name;
    const actions = document.createElement("div");
    actions.className = "settings-row-control";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "btn";
    edit.textContent = profile.builtin ? "Duplicate" : "Edit";
    edit.addEventListener("click", () => editAgentProfile(profile, profile.builtin));
    actions.append(edit);
    if (!profile.builtin) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn";
      remove.textContent = "Remove";
      remove.addEventListener("click", async () => {
        if (!confirm(`Remove ${profile.name}?`)) return;
        try {
          await del(`/api/agents/${profile.id}`);
          editAgentProfile();
          await loadAgentProfiles();
        } catch (error) {
          toast(error.message, true);
        }
      });
      actions.append(remove);
    }
    row.append(name, actions);
    list.append(row);
  }
  editAgentProfile(agentProfiles.find((profile) => profile.id === editingAgent));
}

function wireAgentProfiles() {
  loadAgentProfiles().catch((error) => toast(error.message, true));
  $("chat-agent").addEventListener("change", ({ target }) => {
    const draft = state.draft;
    if (!draft) return;
    const profile = agentProfiles.find((item) => item.id === target.value);
    if (!profile) return;
    draft.agentId = profile.id;
    if (profile.model && state.models.includes(profile.model)) draft.model = profile.model;
    if (profile.model && !state.models.includes(profile.model)) toast("The agent's model is unavailable. Using the selected chat model.", true);
    draft.effort = effortsFor(draft.model).includes(profile.effort) ? profile.effort : "";
    updateDraftControls();
  });
  $("agent-profile-model").addEventListener("change", () => fillAgentProfileEffort());
  $("agent-profile-limit-tools").addEventListener("change", ({ target }) => {
    $("agent-profile-tools").hidden = !target.checked;
  });
  $("agent-profile-reset").addEventListener("click", () => editAgentProfile());
  $("agent-profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      name: $("agent-profile-name").value,
      instructions: $("agent-profile-instructions").value,
      model: $("agent-profile-model").value,
      effort: $("agent-profile-effort").value,
      tools: $("agent-profile-limit-tools").checked
        ? [...$("agent-profile-tools").querySelectorAll("input:checked")].map((field) => field.value)
        : null,
    };
    try {
      const saved = await api(editingAgent ? `/api/agents/${editingAgent}` : "/api/agents", {
        method: editingAgent ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      editingAgent = saved.id;
      await loadAgentProfiles();
      toast("Agent saved.");
    } catch (error) {
      toast(error.message, true);
    }
  });
}

const SUBAGENT_STATUS = {
  running: "Running", waiting: "Waiting", completed: "Done", failed: "Failed", cancelled: "Stopped",
};
const subagentRows = new Map();

function createSubagentRow(agent) {
  const card = document.createElement("article");
  card.className = "subagent-card";
  card.innerHTML = `
    <details class="subagent-details">
      <summary class="subagent-summary">
        <span class="subagent-indicator" aria-hidden="true"></span>
        <span class="subagent-label"><strong></strong><span></span></span>
        <span class="subagent-status"></span>
        <span class="subagent-chevron" aria-hidden="true"></span>
      </summary>
      <div class="subagent-information">
        <div class="subagent-meta"></div>
        <div class="subagent-messages"></div>
      </div>
    </details>
    <button type="button" class="stop subagent-stop">Stop</button>`;
  const stop = card.querySelector("button");
  const conversation = state.active?.id;
  stop.setAttribute("aria-label", `Stop ${agent.name}${agent.task ? `: ${agent.task}` : ""}`);
  stop.addEventListener("click", async () => {
    stop.disabled = true;
    try {
      await post(`/api/conversations/${conversation}/subagents/${agent.id}/stop`, {});
    } catch (error) {
      toast(error.message, true);
    } finally {
      stop.disabled = false;
    }
  });
  $("subagent-rows").append(card);
  return {
    card, stop,
    title: card.querySelector(".subagent-label strong"),
    profile: card.querySelector(".subagent-label span"),
    status: card.querySelector(".subagent-status"),
    meta: card.querySelector(".subagent-meta"),
    messages: card.querySelector(".subagent-messages"),
  };
}

function renderSubagents(agents) {
  const panel = $("subagents-panel");
  panel.hidden = !agents.length;
  if (!agents.length) {
    panel.open = false;
    subagentRows.clear();
    $("subagent-rows").replaceChildren();
    return;
  }
  const counts = {};
  for (const agent of agents) {
    const row = subagentRows.get(agent.id) || createSubagentRow(agent);
    subagentRows.set(agent.id, row);
    counts[agent.status] = (counts[agent.status] || 0) + 1;
    row.card.dataset.status = agent.status;
    row.title.textContent = agent.task || agent.name;
    const model = state.modelLabels?.[agent.model] || agent.model;
    row.profile.textContent = [agent.task && agent.name, model].filter(Boolean).join(" · ");
    row.status.textContent = SUBAGENT_STATUS[agent.status] || agent.status;
    row.meta.textContent = `${agent.tool_calls} tool call${agent.tool_calls === 1 ? "" : "s"}`;
    row.stop.hidden = !state.active || !["running", "waiting"].includes(agent.status);
  }
  $("subagent-counts").textContent = Object.entries(SUBAGENT_STATUS)
    .filter(([key]) => counts[key]).map(([key, label]) => `${counts[key]} ${label.toLowerCase()}`).join(" · ");
}

function renderSubagentMessage(data) {
  const row = subagentRows.get(data.id);
  if (!row || !data.message) return;
  const entry = document.createElement("div");
  const label = document.createElement("strong");
  label.textContent = data.status === "completed" ? "Result" : data.status === "failed" ? "Error" : data.name;
  const text = document.createElement("div");
  text.textContent = data.message;
  entry.append(label, text);
  row.messages.append(entry);
  if (row.messages.childElementCount > 4) row.messages.firstElementChild.remove();
}
