/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

/* -- Live stream ---------------------------------------------------------- */

function setBusy(busy, label = "Working…") {
  const wasBusy = state.busy;
  state.busy = busy;
  el.statusBar.hidden = !busy;
  el.statusText.textContent = label;
  if (busy || wasBusy) scrollDown(true);
  updateSendState();
  el.compactContext.disabled = busy || !state.active;
  if (busy && !state.busyWatchdog) {
    state.busyWatchdog = setInterval(checkBusyRun, 3000);
  } else if (!busy && state.busyWatchdog) {
    clearInterval(state.busyWatchdog);
    state.busyWatchdog = null;
  }
}

/** Send is only meaningful with a session (or draft) and something to send. */
function updateSendState() {
  const hasContent = el.prompt.value.trim().length > 0 || state.attachments.length > 0;
  const canSteer = state.busy && state.active?.model === "gpt-6-astra";
  el.send.disabled =
    (!state.active && !state.draft?.model) || !hasContent || (state.busy && !canSteer);
}

async function reconcileRun(conversationId) {
  if (state.reconciling || state.pendingMessages.size || state.active?.id !== conversationId) return;
  state.reconciling = true;
  try {
    const status = await get(`/api/conversations/${conversationId}/run-status`);
    if (state.active?.id !== conversationId) return;
    if (status.busy) return;
    const data = await get(`/api/conversations/${conversationId}`);
    if (state.active?.id !== conversationId) return;
    state.active = data;
    resetLive();
    renderEntries(data.entries || []);
    refreshUsageFromConversation();
    setBusy(false);
  } catch {
    // The EventSource keeps reconnecting; the next watchdog tick can retry.
  } finally {
    state.reconciling = false;
  }
}

function checkBusyRun() {
  if (!state.busy || !state.active || state.uploadsInProgress) return;
  if (Date.now() - state.lastStreamEventAt < 5000) return;
  reconcileRun(state.active.id);
}

function resetLive() {
  if (state.liveTextTimer !== null) clearTimeout(state.liveTextTimer);
  state.liveTextTimer = null;
  state.liveTextLastPaint = -Infinity;
  state.liveTextPending = "";
  state.liveReasoningPending = "";
  state.liveText = null;
  state.liveReasoning = null;
  state.liveTodos = null;
  state.lastTurnUsage = null;
  state.toolCards.clear();
}

function connectStream(conversationId) {
  disconnectStream();
  const stream = new EventSource(`/api/conversations/${conversationId}/events`);
  state.stream = stream;

  state.lastStreamEventAt = Date.now();
  const on = (name, handler) =>
    stream.addEventListener(name, (event) => {
      state.lastStreamEventAt = Date.now();
      let data = {};
      try { data = JSON.parse(event.data); } catch { /* keepalive or noise */ }
      handler(data);
    });

  on("ready", (data) => {
    refreshComputerUse();
    if (data.busy) setBusy(true);
    else if (state.busy) reconcileRun(conversationId);
    else setBusy(false);
  });
  on("computer_use", refreshComputerUse);
  on("subagents", (data) => renderSubagents(data.agents || []));
  on("subagent_waiting", () => setBusy(true, "Waiting for subagents…"));
  on("agent_message", renderSubagentMessage);
  on("user_message", (data) => {
    const messageId = String(data.message_id || "");
    const existing = messageId
      ? [...el.messages.querySelectorAll(".user-message[data-message-id]")]
        .find((node) => node.dataset.messageId === messageId)
      : null;
    if (existing) {
      existing.classList.remove("pending");
    } else {
      el.emptyState.hidden = true;
      append(userMessage(data.text || "", data.attachments || [], messageId));
    }
  });
  on("steer_queued", () => setBusy(true, "Update queued for Astra…"));
  on("steer_applied", () => setBusy(true, "Astra is applying your update…"));
  on("configuration_queued", () => setBusy(true, "Reasoning update queued…"));
  on("configuration_applied", () => setBusy(true, "Reasoning updated for Astra…"));
  on("model_routing", () => setBusy(true, "Choosing the best model…"));
  on("model_selected", (data) => {
    append(modelDecisionCard(data));
    setBusy(true, `Auto selected ${modelDisplayName(data)}…`);
    scrollDown();
  });

  on("step", (data) => {
    // A fresh model turn: later text and tool calls belong in their own rows.
    finalizeLiveText();
    state.lastTurnUsage = null;
    setBusy(true, data.step > 1 ? `Working… (step ${data.step})` : "Working…");
  });

  on("text_delta", (data) => {
    if (!state.liveText) state.liveText = append(streamingAssistantBubble());
    state.liveTextPending += data.delta || "";
    scheduleLiveTextPaint();
  });

  on("reasoning_delta", (data) => {
    if (!state.liveReasoning) state.liveReasoning = append(reasoningBlock(""));
    state.liveReasoningPending += data.delta || "";
    scheduleLiveTextPaint();
  });

  on("tool_started", (data) => {
    // todo_write renders as its own plan card via the `todos` event.
    if (data.name === "todo_write") { setBusy(true, "Updating the plan…"); return; }
    if (HIDDEN_TOOL_NAMES.has(data.name)) { setBusy(true, "Viewing image…"); return; }
    finalizeLiveText();
    state.liveReasoning = null;
    const row = appendToolCall(toolRow(data.name || "tool", data.arguments));
    state.toolCards.set(data.call_id, { row, name: data.name, arguments: data.arguments });
    setBusy(true, data.name === "subagents" ? "Coordinating subagents…" : `Running ${data.name}…`);
  });

  on("tool_finished", (data) => {
    const entry = state.toolCards.get(data.call_id);
    if (entry && !entry.hidden) {
      finishToolRow(entry.row, data.output, data.ok);
      if (data.ok && entry.name === "showcase_file") {
        try {
          const args = JSON.parse(entry.arguments || "{}");
          const card = showcaseFile(args.path);
          if (card) append(card);
        } catch { /* tool arguments were already reported as invalid */ }
      }
    }
    setBusy(true);
  });

  on("usage", (data) => {
    // Mirror onto the active conversation so the ring survives a reconnect
    // and stays put once the run ends.
    if (state.active) {
      if (Number.isFinite(data.context_tokens)) state.active.context_tokens = data.context_tokens;
      if (Number.isFinite(data.context_window)) state.active.context_window = data.context_window;
      if (Number.isFinite(data.total_tokens)) state.active.total_tokens = data.total_tokens;
    }
    state.lastTurnUsage = {
      outputTokens: data.output_tokens,
      outputTokensPerSecond: data.output_tokens_per_second,
    };
    updateUsage({
      contextTokens: data.context_tokens,
      contextWindow: data.context_window,
      totalTokens: data.total_tokens,
    });
  });

  on("plan_usage", (data) => updatePlanUsage(data));

  on("compaction_started", () => setBusy(true, "Compacting earlier conversation…"));
  on("compacted", (data) => {
    setBusy(true);
    if (state.active && Number.isFinite(data.context_tokens)) {
      state.active.context_tokens = data.context_tokens;
      updateUsage({
        contextTokens: data.context_tokens,
        contextWindow: state.active.context_window,
        totalTokens: state.active.total_tokens,
      });
    }
    // `kept_items` is always 0 -- Compact replaces the model's entire working
    // history with one summary, by design. Worth saying plainly: the number
    // alone reads like data loss, but the full transcript above is untouched
    // and still saved; only what gets resent to the model next turn shrank.
    toast(
      `Compacted ${data.compacted_items} earlier message(s) into a summary. ` +
      `Nothing was deleted -- the full conversation above is still here.`
    );
  });
  on("compaction_failed", (data) => {
    toast(`Could not compact context: ${data.message}`, true);
  });
  on("compaction_skipped", (data) => toast(data.message || "Nothing to compact."));
  on("compaction_finished", () => setBusy(false));

  on("todos", (data) => {
    const todos = data.todos || [];
    if (!todos.length) return;
    // One card per run, updated in place -- appending a fresh copy on every
    // status flip would bury the conversation in near-identical lists.
    if (state.liveTodos && state.liveTodos.isConnected) {
      renderTodos(state.liveTodos, todos);
    } else {
      state.liveTodos = append(todoCard(todos));
    }
    finalizeLiveText();
    scrollDown();
  });

  on("judge_started", () => setBusy(true, "Safety model is reviewing the command…"));

  on("judge_finished", (data) => {
    if (!data.allowed) toast("The safety model blocked a command.", true);
    setBusy(true);
  });

  on("permission_request", (data) => {
    if (state.currentPermission?.id === data.id || state.permissionQueue.some((request) => request.id === data.id)) return;
    state.permissionQueue.push(data);
    showNextPermission();
  });

  on("permission_cancelled", ({ id }) => {
    state.permissionQueue = state.permissionQueue.filter((request) => request.id !== id);
    if (state.currentPermission?.id === id) {
      el.permOverlay.hidden = true;
      state.currentPermission = null;
      showNextPermission();
    }
  });

  on("question_request", (data) => {
    state.questionQueue.push(data);
    showNextQuestion();
  });

  on("title", (data) => {
    if (state.active) state.active.title = data.title;
    el.title.textContent = data.title;
    loadConversations();
  });

  on("done", () => { finalizeLiveText(); setBusy(false); resetLive(); });
  on("run_finished", () => {
    finalizeLiveText();
    setBusy(false);
    resetLive();
    if (state.active?.model === "auto" || state.active?.model.startsWith("opencode-go/")) {
      loadGoPlanUsage();
    }
  });
  on("cancelled", () => {
    finalizeLiveText();
    setBusy(false);
    resetLive();
    toast("Run cancelled.");
  });

  on("error", (data) => {
    finalizeLiveText();
    append(errorBubble(data.message || "Something went wrong."));
    setBusy(false);
    resetLive();
  });

  stream.onerror = () => {
    // EventSource reconnects on its own. If a tunnel dropped the final frame,
    // reconcile against persisted state instead of leaving “Working…” forever.
    state.lastStreamEventAt = 0;
    if (state.busy) reconcileRun(conversationId);
  };
}

function disconnectStream() {
  if (state.stream) {
    state.stream.close();
    state.stream = null;
  }
}

/* -- Permissions ---------------------------------------------------------- */

/** Colourise a unified diff. Built by hand rather than with innerHTML on raw
 *  diff text -- the content is file data and must never become markup. */
function renderDiff(diff) {
  el.permDiff.replaceChildren();
  if (!diff) {
    el.permDiff.hidden = true;
    return;
  }
  el.permDiff.hidden = false;
  for (const line of diff.split("\n")) {
    const row = document.createElement("span");
    if (line.startsWith("+++") || line.startsWith("---")) row.className = "hunk";
    else if (line.startsWith("@@")) row.className = "hunk";
    else if (line.startsWith("+")) row.className = "add";
    else if (line.startsWith("-")) row.className = "del";
    row.textContent = `${line}\n`;
    el.permDiff.appendChild(row);
  }
}

function showNextPermission() {
  if (state.currentPermission || !state.permissionQueue.length) return;
  const request = state.permissionQueue.shift();
  state.currentPermission = request;

  el.permKind.textContent =
    request.kind === "shell"
      ? `${request.agent_name || "The agent"} wants to run a shell command.`
      : `${request.agent_name || "The agent"} wants to write to a file.`;
  el.permDetail.textContent = request.detail;
  el.permScope.textContent = request.session_scope
    ? `Session scope: ${request.session_scope}`
    : "";
  renderDiff(request.diff);
  el.permFeedback.value = "";
  el.permOverlay.hidden = false;
  el.permOnce.focus();
}

async function replyPermission(reply) {
  const request = state.currentPermission;
  if (!request || !state.active) return;
  el.permOverlay.hidden = true;
  state.currentPermission = null;
  try {
    await post(`/api/conversations/${state.active.id}/permission/${request.id}`, {
      reply,
      feedback: el.permFeedback.value,
    });
  } catch (error) {
    toast(error.message, true);
  }
  showNextPermission();
}

function showNextQuestion() {
  if (state.currentQuestion || !state.questionQueue.length) return;
  const question = state.questionQueue.shift();
  state.currentQuestion = question;
  el.questionTitle.textContent = question.header || "Question";
  el.questionText.textContent = question.question;
  el.questionOptions.replaceChildren();
  for (const option of question.options || []) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "question-option";
    const label = document.createElement("strong");
    label.textContent = option.label;
    button.appendChild(label);
    if (option.description) {
      const description = document.createElement("span");
      description.textContent = option.description;
      button.appendChild(description);
    }
    button.addEventListener("click", () => replyQuestion(option.label));
    el.questionOptions.appendChild(button);
  }
  el.questionAnswer.value = "";
  el.questionAnswer.hidden = !question.allow_freeform;
  el.questionSubmit.hidden = !question.allow_freeform;
  el.questionCancel.textContent = question.allow_freeform ? "Cancel" : "Cancel question";
  el.questionOverlay.hidden = false;
  (question.allow_freeform ? el.questionAnswer : el.questionOptions.querySelector("button"))?.focus();
}

async function replyQuestion(answer) {
  const question = state.currentQuestion;
  if (!question || !state.active) return;
  const reply = String(answer || el.questionAnswer.value).trim() || "The user cancelled the question.";
  el.questionOverlay.hidden = true;
  state.currentQuestion = null;
  try {
    await post(`/api/conversations/${state.active.id}/question/${question.id}`, { answer: reply });
  } catch (error) {
    toast(error.message, true);
  }
  showNextQuestion();
}
