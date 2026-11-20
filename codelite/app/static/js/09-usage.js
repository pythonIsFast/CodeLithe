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

/* -- ChatGPT plan usage ---------------------------------------------------- */

/** "in 3 d", "in 5 h" -- how long until the allowance resets. */
function untilReset(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const days = Math.floor(seconds / 86400);
  if (days >= 1) return `resets in ${days} d`;
  const hours = Math.floor(seconds / 3600);
  if (hours >= 1) return `resets in ${hours} h`;
  return `resets in ${Math.max(1, Math.floor(seconds / 60))} min`;
}

/**
 * Show how much of the ChatGPT plan's weekly allowance is gone.
 *
 * These figures come from the `x-codex-*` response headers, which only arrive
 * on a real request -- so this stays hidden until the first message has gone
 * out on a fresh install.
 */
function updatePlanUsage(usage) {
  const weekly = usage && usage.weekly;
  if (!weekly || !Number.isFinite(weekly.used_percent)) {
    el.planUsage.hidden = true;
    el.detailPlanCard.hidden = true;
    return;
  }

  const fraction = Math.min(1, Math.max(0, weekly.used_percent / 100));
  el.planUsage.hidden = false;
  el.detailPlanCard.hidden = false;
  el.planLabel.textContent = `${Math.round(weekly.used_percent)}% weekly`;
  el.planRingFill.style.strokeDashoffset = RING_CIRCUMFERENCE * (1 - fraction);
  el.planUsage.classList.toggle("warn", fraction >= 0.6 && fraction < 0.85);
  el.planUsage.classList.toggle("high", fraction >= 0.85);
  el.detailPlanBar.style.width = `${fraction * 100}%`;

  const lines = [
    `${Math.round(weekly.used_percent)}% of your weekly ChatGPT allowance used`,
  ];
  const reset = untilReset(weekly.reset_after_seconds);
  if (reset) lines.push(reset.charAt(0).toUpperCase() + reset.slice(1));
  if (usage.plan_type) lines.push(`Plan: ${usage.plan_type}`);
  el.planUsage.title = lines.join("\n");
  el.detailPlanNote.textContent = lines.slice(0, 2).join(" · ");
}

async function loadPlanUsage() {
  if (!state.auth?.authenticated) return;
  try {
    const data = await get("/api/usage");
    updatePlanUsage(data.usage);
  } catch {
    // Non-essential: a missing allowance readout must not break startup.
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
