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

function renderSubagents(agents) {
  const rows = $("subagent-rows");
  rows.replaceChildren();
  $("subagents-panel").hidden = !agents.length;
  if (!agents.length) $("subagent-messages").replaceChildren();
  const conversation = state.active?.id;
  for (const agent of agents) {
    const row = document.createElement("div");
    row.className = "subagent-row";
    row.dataset.status = agent.status;
    const text = document.createElement("span");
    const model = state.modelLabels?.[agent.model] || agent.model;
    text.textContent = `${agent.name} · ${model} · ${agent.status} · ${agent.tool_calls} tool calls`;
    row.append(text);
    if (conversation && ["running", "waiting"].includes(agent.status)) {
      const stop = document.createElement("button");
      stop.type = "button";
      stop.className = "compact-button";
      stop.textContent = "Stop";
      stop.addEventListener("click", () => post(`/api/conversations/${conversation}/subagents/${agent.id}/stop`, {})
        .catch((error) => toast(error.message, true)));
      row.append(stop);
    }
    rows.append(row);
  }
}

function renderSubagentMessage(data) {
  const messages = $("subagent-messages");
  const row = document.createElement("div");
  row.textContent = `${data.name}: ${data.message}`;
  messages.append(row);
  if (messages.childElementCount > 4) messages.firstElementChild.remove();
}
