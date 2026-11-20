/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

/* -- Token usage / context window ----------------------------------------- */

/** Circumference of the ring in index.html (2*pi*9), kept in sync with its CSS. */
const RING_CIRCUMFERENCE = 56.55;

const formatTokens = (n) => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return String(n);
};

function resetUsage() {
  el.usage.hidden = true;
  el.usage.classList.remove("warn", "high");
  el.usageRingFill.style.strokeDashoffset = RING_CIRCUMFERENCE;
  el.detailContextPct.textContent = "—";
  el.detailContextBar.style.width = "0%";
  el.detailContextNote.textContent = "No messages yet";
}

/**
 * Paint the context ring. Called both from live `usage` events and when a
 * conversation is opened, so the ring reflects the stored chat size at all
 * times rather than only while a run happens to be streaming.
 */
function updateUsage({ contextTokens, contextWindow, totalTokens }) {
  const hasContext =
    Number.isFinite(contextTokens) && Number.isFinite(contextWindow) && contextWindow > 0;
  const hasTotal = Number.isFinite(totalTokens) && totalTokens > 0;
  if (!hasContext && !hasTotal) {
    resetUsage();
    return;
  }

  el.usage.hidden = false;
  const fraction = hasContext ? Math.min(1, contextTokens / contextWindow) : 0;

  // The composer stays quiet, like the reference desktop agent model row: the ring carries
  // the percentage and the full token counts live in its tooltip and the
  // session-details rail.
  el.usageLabel.textContent = hasContext
    ? `${Math.round(fraction * 100)}%`
    : `${formatTokens(totalTokens)}`;

  el.usageRingFill.style.strokeDashoffset = RING_CIRCUMFERENCE * (1 - fraction);
  el.usage.classList.toggle("warn", fraction >= 0.6 && fraction < 0.85);
  el.usage.classList.toggle("high", fraction >= 0.85);

  el.detailContextPct.textContent = hasContext ? `${Math.round(fraction * 100)}%` : "—";
  el.detailContextBar.style.width = hasContext ? `${fraction * 100}%` : "0%";
  el.detailContextNote.textContent = hasContext
    ? `${formatTokens(contextTokens)} of ${formatTokens(contextWindow)} tokens used`
    : hasTotal ? `${formatTokens(totalTokens)} tokens in this session` : "No messages yet";

  const tips = [];
  if (hasContext) {
    // Say "input budget", not just "context": the window is the input
    // allowance, with output reserved separately.
    tips.push(
      `${formatTokens(contextTokens)} of ${formatTokens(contextWindow)} input ` +
        `budget (${Math.round(fraction * 100)}%)`
    );
  }
  if (hasTotal) tips.push(`${totalTokens.toLocaleString()} tokens in this conversation`);
  el.usage.title = tips.join("\n");
}

/* -- Account allowances --------------------------------------------------- */

function planWindowLabel(minutes) {
  if (minutes === 7 * 24 * 60) return "Weekly";
  if (minutes % 1440 === 0) return `${minutes / 1440}-day`;
  if (minutes % 60 === 0) return `${minutes / 60}-hour`;
  return `${minutes}-minute`;
}

function planResetLabel(resetAt) {
  if (!resetAt) return "";
  const date = new Date(typeof resetAt === "number" ? resetAt * 1000 : resetAt);
  return Number.isFinite(date.getTime()) ? `Resets ${date.toLocaleString()}` : "";
}

function paintPlanWindows(target, windows, emptyMessage) {
  target.replaceChildren();
  if (!windows.length) {
    const note = document.createElement("p");
    note.className = "detail-note";
    note.textContent = emptyMessage;
    target.appendChild(note);
    return;
  }
  for (const { label, percent, resetAt, description, valueText } of windows) {
    const row = document.createElement("div");
    row.className = "detail-plan-window";
    row.classList.toggle("recommended", Boolean(description));
    const title = document.createElement("span");
    title.textContent = label;
    const value = document.createElement("span");
    value.className = "detail-value";
    value.textContent = valueText || `${Math.round(percent)}% used`;
    const meter = document.createElement("progress");
    meter.max = 100;
    meter.value = percent;
    meter.setAttribute("aria-label", valueText ? `${label} share of monthly limit` : `${label} allowance used`);
    row.append(title, value, meter);
    const caption = description || planResetLabel(resetAt);
    if (caption) {
      const note = document.createElement("p");
      note.className = "detail-note";
      note.textContent = caption;
      row.appendChild(note);
    }
    target.appendChild(row);
  }
}

function syncPlanCardVisibility() {
  el.detailPlanCard.hidden = el.detailPlanChatGPT.hidden && el.detailPlanGo.hidden;
}

function updatePlanUsage(usage) {
  const windows = [usage?.primary, usage?.secondary]
    .filter((window) => window?.window_minutes > 0 && Number.isFinite(window.used_percent))
    .sort((a, b) => a.window_minutes - b.window_minutes)
    .map((window) => ({
      label: planWindowLabel(window.window_minutes),
      percent: window.used_percent,
      resetAt: window.reset_at,
    }));
  el.detailPlanChatGPT.hidden = !state.auth?.authenticated;
  paintPlanWindows(el.detailPlanChatGPTWindows, windows, "Waiting for usage from a ChatGPT request.");
  syncPlanCardVisibility();
}

function weeklyGoRecommendation(monthly, now = Date.now()) {
  if (!Number.isFinite(monthly?.percent)) return null;
  const remainingTime = Date.parse(monthly.resets_at) - now;
  if (!(remainingTime > 0)) return null;

  const week = 7 * 24 * 60 * 60 * 1000;
  const remainingBudget = Math.max(0, 100 - monthly.percent);
  // Spread the unspent allowance evenly over the actual time until reset.
  // Spending this share in the next week preserves the same pace afterwards.
  const percent = remainingBudget * Math.min(1, week / remainingTime);
  return {
    label: "Weekly recommended",
    percent,
    valueText: percent > 0 && percent < 0.1
      ? "<0.1%"
      : `${percent.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`,
    description: remainingTime <= week
      ? "Share of the monthly limit to spend before reset."
      : "Share of the monthly limit to spend over the next 7 days.",
  };
}

function updateGoPlanUsage(usage, error = "") {
  state.goPlanUsage = usage;
  const windows = [];
  for (const [key, label] of [["rolling", "5-hour"], ["weekly", "Weekly"], ["monthly", "Monthly"]]) {
    const period = usage?.[key];
    if (Number.isFinite(period?.percent)) {
      windows.push({ label, percent: period.percent, resetAt: period.resets_at });
    }
  }
  const recommendation = weeklyGoRecommendation(usage?.monthly);
  if (recommendation) windows.push(recommendation);
  const configured = state.auth?.opencode_go_usage;
  el.detailPlanGo.hidden = !(state.auth?.opencode_go || configured);
  const empty = configured ? "Waiting for OpenCode Go usage." : "Add a service account key in Settings.";
  paintPlanWindows(el.detailPlanGoWindows, windows, error || empty);
  syncPlanCardVisibility();
}

async function fetchGoPlanUsage() {
  if (!state.auth?.opencode_go_usage) {
    updateGoPlanUsage(null);
    return {};
  }
  if (!state.goPlanRequest) {
    state.goPlanRequest = get("/api/usage/opencode-go").finally(() => {
      state.goPlanRequest = null;
    });
  }
  try {
    const { usage } = await state.goPlanRequest;
    updateGoPlanUsage(usage);
    return usage;
  } catch (error) {
    updateGoPlanUsage(null, error.message);
    throw error;
  }
}

function loadGoPlanUsage() {
  return fetchGoPlanUsage().catch(() => {});
}

function tickGoPlanUsage() {
  const usage = state.goPlanUsage;
  const reset = Date.parse(usage?.monthly?.resets_at);
  if (!Number.isFinite(reset)) return;
  updateGoPlanUsage(usage);
  if (reset <= Date.now()) loadGoPlanUsage();
}

async function loadPlanUsage() {
  loadGoPlanUsage();
  if (!state.auth?.authenticated) {
    updatePlanUsage(null);
    return;
  }
  try {
    const data = await get("/api/usage");
    updatePlanUsage(data.usage);
  } catch {
    // A missing allowance readout must not break startup.
  }
}

/** Recompute the ring from whatever the active conversation currently knows. */
function refreshUsageFromConversation() {
  const active = state.active;
  if (!active) {
    resetUsage();
    return;
  }
  updateUsage({
    contextTokens: active.context_tokens,
    contextWindow: active.context_window,
    totalTokens: active.total_tokens,
  });
}
