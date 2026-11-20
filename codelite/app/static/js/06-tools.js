/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

/* -- Message footer -------------------------------------------------------- */

const ICONS = {
  copy:
    `<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">` +
    `<rect x="5.5" y="5.5" width="8" height="8" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.3"/>` +
    `<path d="M10.5 3.5H4A1.5 1.5 0 0 0 2.5 5v6.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>`,
  retry:
    `<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">` +
    `<path d="M13.5 8a5.5 5.5 0 1 1-1.9-4.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>` +
    `<path d="M13.5 2v3h-3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};

/** "vor 3 Min." style relative time, recomputed on render rather than stored. */
function relativeTime(iso) {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 45) return "just now";
  const steps = [
    [60, "min", 60],
    [3600, "h", 3600],
    [86400, "d", 86400],
  ];
  for (const [limit, unit, divisor] of steps) {
    if (seconds < limit * 60 || unit === "d") {
      const value = Math.floor(seconds / divisor);
      if (value >= 1) return `${value} ${unit} ago`;
    }
  }
  return new Date(then).toLocaleDateString();
}

function footerButton(icon, label, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "msg-action";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.innerHTML = icon;
  button.addEventListener("click", onClick);
  return button;
}

const FOOT_MARK =
  `<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">` +
  `<circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/>` +
  `<circle cx="12" cy="12" r="2.7" fill="currentColor"/></svg>`;

/**
 * Add the action row under an assistant message: the reference-style mark with
 * time and token count, plus copy/retry behind a hover.
 */
function attachFooter(bubble, { at, outputTokens } = {}) {
  const footer = document.createElement("div");
  footer.className = "msg-foot";

  const mark = document.createElement("span");
  mark.className = "msg-mark";
  mark.innerHTML = FOOT_MARK;
  footer.appendChild(mark);

  if (at) {
    const time = document.createElement("span");
    time.className = "msg-time";
    time.textContent = relativeTime(at);
    time.title = new Date(at).toLocaleString();
    time.dataset.at = at;
    footer.appendChild(time);
  }

  if (Number.isFinite(outputTokens) && outputTokens > 0) {
    const cost = document.createElement("span");
    cost.className = "msg-tokens";
    cost.textContent = `↓ ${formatTokens(outputTokens)} tokens`;
    cost.title = `${outputTokens.toLocaleString()} tokens in this model output`;
    footer.appendChild(cost);
  }

  const actions = document.createElement("span");
  actions.className = "msg-actions";
  actions.appendChild(
    footerButton(ICONS.copy, "Copy message", async (event) => {
      const raw = bubble.dataset.raw || bubble.textContent;
      try {
        await navigator.clipboard.writeText(raw);
        toast("Copied.");
      } catch {
        toast("Could not copy to the clipboard.", true);
      }
      event.currentTarget.blur();
    })
  );
  actions.appendChild(
    footerButton(ICONS.retry, "Send this prompt again", () => retryLastPrompt())
  );
  footer.appendChild(actions);

  bubble.appendChild(footer);
  return footer;
}

/**
 * Close out the streaming bubble and give it its footer.
 *
 * Has to happen after the last delta: `text_delta` rebuilds the bubble's
 * innerHTML each time, which would throw away an already-appended footer.
 */
function finalizeLiveText() {
  const bubble = state.liveText;
  state.liveText = null;
  if (!bubble || !bubble.isConnected) return;
  if (bubble.querySelector(".msg-foot")) return;
  attachFooter(bubble, {
    at: new Date().toISOString(),
    outputTokens: state.lastTurnOutputTokens,
  });
}

/** Re-send the most recent user message. */
function retryLastPrompt() {
  const bubbles = el.messages.querySelectorAll(".msg.user");
  const last = bubbles[bubbles.length - 1];
  if (!last) {
    toast("Nothing to retry yet.");
    return;
  }
  el.prompt.value = last.textContent;
  el.prompt.focus();
  el.prompt.dispatchEvent(new Event("input"));
  toast("Prompt restored — press Enter to send it again.");
}

const TODO_GLYPH = { completed: "✓", in_progress: "→", pending: "" };

/** The agent's plan. Re-rendered in place as it advances, not appended anew. */
function todoCard(todos) {
  const card = document.createElement("div");
  card.className = "todos";
  renderTodos(card, todos);
  return card;
}

function renderTodos(card, todos) {
  const done = todos.filter((t) => t.status === "completed").length;
  card.replaceChildren();

  const head = document.createElement("div");
  head.className = "todos-head";
  head.textContent = `Plan · ${done}/${todos.length}`;
  card.appendChild(head);

  const list = document.createElement("ul");
  list.className = "todos-list";
  for (const todo of todos) {
    const item = document.createElement("li");
    item.className = `todo ${todo.status}`;
    const mark = document.createElement("span");
    mark.className = "todo-mark";
    mark.textContent = TODO_GLYPH[todo.status] ?? "";
    const text = document.createElement("span");
    text.textContent = todo.content;
    item.append(mark, text);
    list.appendChild(item);
  }
  card.appendChild(list);
}

function safeArgs(rawArguments) {
  try {
    const parsed = JSON.parse(rawArguments || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function firstText(args) {
  for (const value of Object.values(args)) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

/**
 * One glyph per category. A folder icon above a web search claims a file was
 * touched, so every category carries its own shape rather than sharing one.
 */
const CATEGORY_ICON_PATHS = {
  read: ["M4 2h5l3 3v9H4z", "M9 2v3h3"],
  list: ["M2 3.5A1.5 1.5 0 0 1 3.5 2h4l1.5 1.5H12.5A1.5 1.5 0 0 1 14 5v7a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 12z"],
  search: ["M7 11.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9z", "M10.5 10.5 14 14"],
  write: ["M11.5 2.5 13.5 4.5 5.5 12.5 2.5 13.5 3.5 10.5z"],
  shell: ["M2 3.5h12v9H2z", "M4.5 7 6 8.5 4.5 10", "M8 10.5h3.5"],
  extension: ["M2.5 2.5h5v5h-5z", "M8.5 2.5h5v5h-5z", "M8.5 8.5h5v5h-5z"],
  websearch: ["M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12z", "M2.5 6.5h11", "M2.5 9.5h11", "M8 2c-2 2.5-2 9.5 0 12", "M8 2c2 2.5 2 9.5 0 12"],
  webfetch: ["M8 2.5v7", "M5 7l3 3 3-3", "M3 13h10"],
  intel: ["M6.5 2.5C5 2.5 5 5 5 6.5S3.5 8 3.5 8 5 9.5 5 11s0 2.5 1.5 2.5", "M9.5 2.5C11 2.5 11 5 11 6.5s1.5 1.5 1.5 1.5-1.5 1.5-1.5 3 0 2.5-1.5 2.5"],
  memory: ["M4 2.5h8v11l-4-3-4 3z"],
  image: ["M2.5 3.5h11v9h-11z", "M5.5 7a1 1 0 1 0 0-2 1 1 0 0 0 0 2z", "M2.5 11 6 8l3 2.5 2.5-2 2 1.5"],
  showcase: ["M1.5 8S4 4 8 4s6.5 4 6.5 4-2.5 4-6.5 4S1.5 8 1.5 8z", "M8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4z"],
  ask: ["M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12z", "M6.2 6.2A1.9 1.9 0 0 1 9.9 6.8c0 1.3-1.9 1.5-1.9 2.7", "M8 11.4h.01"],
  tool: ["M4 8h.01", "M8 8h.01", "M12 8h.01"],
};

function categoryIcon(category) {
  const paths = CATEGORY_ICON_PATHS[category] || CATEGORY_ICON_PATHS.tool;
  return (
    `<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" fill="none"` +
    ` stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">` +
    paths.map((d) => `<path d="${d}"/>`).join("") +
    `</svg>`
  );
}

const CHEVRON_ICON =
  `<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">` +
  `<path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6"` +
  ` stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const HIDDEN_TOOL_NAMES = new Set(["view_image"]);

/**
 * Which category a tool call is summarized under.
 *
 * Every tool is listed explicitly. The fallback deliberately says "tool" and
 * not "file": an unlisted tool is one nobody has classified yet, and guessing
 * "read 1 file" for it describes work that never happened. Add new tools here
 * when they are added to `tools/registry.py`.
 */
const TOOL_CATEGORY = {
  read_file: "read",
  list_dir: "list",
  grep: "search",
  find_files: "search",
  write_file: "write",
  edit_file: "write",
  shell: "shell",
  extensions: "extension",
  web_search: "websearch",
  web_fetch: "webfetch",
  code_intelligence: "intel",
  project_memory: "memory",
  generate_image: "image",
  view_image: "image",
  showcase_file: "showcase",
  ask_user: "ask",
};

function toolCategory(name) {
  return TOOL_CATEGORY[name] || "tool";
}

/**
 * Per-tool presentation, in the shape the reference desktop agent transcript uses: file
 * work gets a bright verb plus a mono path chip, everything else is a quiet
 * sentence. Anything unlisted falls back to its first string argument.
 */
const TOOL_PRESENTATION = {
  read_file: { verb: "Read", chip: (args) => args.path },
  write_file: { verb: "Write", chip: (args) => args.path },
  edit_file: { verb: "Edit", chip: (args) => args.path },
  list_dir: { verb: "List", chip: (args) => args.path || "." },
  find_files: { verb: "Find", chip: (args) => args.glob || args.pattern },
  grep: { verb: "Search", chip: (args) => args.pattern },
  shell: { verb: "Run", text: (args) => args.command },
  web_search: { verb: "Search the web", text: (args) => args.query },
  web_fetch: { verb: "Fetch", text: (args) => args.url },
  generate_image: { verb: "Generate image", text: (args) => args.prompt },
  ask_user: { verb: "Ask", text: (args) => args.question || args.title },
  browser: { verb: "Browse", text: (args) => args.url || args.action },
  code_intelligence: { verb: "Code intelligence", text: firstText },
  project_memory: { verb: "Memory", text: firstText },
  extensions: { verb: "Extension", text: firstText },
};

function truncate(value, max = 72) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/**
 * One row per call: verb + target on the line, a quiet result line under it
 * once finished, and the raw output behind a click.
 */
function toolRow(name, rawArguments) {
  const presentation = TOOL_PRESENTATION[name] || {};
  const args = safeArgs(rawArguments);
  const chip = presentation.chip ? truncate(presentation.chip(args), 64) : "";
  const label = truncate(
    presentation.text ? presentation.text(args) : firstText(args),
  );

  const wrap = document.createElement("div");
  wrap.className = "tool-call running";
  wrap._name = name;

  const head = document.createElement("button");
  head.type = "button";
  head.className = "tool-call-row";
  head.innerHTML =
    `<span class="tool-call-icon">${categoryIcon(toolCategory(name))}</span>` +
    (chip
      ? `<span class="tool-call-verb"></span><span class="tool-call-chip"></span>`
      : `<span class="tool-call-text"></span>`) +
    `<span class="tool-call-chevron">${CHEVRON_ICON}</span>`;
  if (chip) {
    head.querySelector(".tool-call-verb").textContent = presentation.verb || name;
    const chipElement = head.querySelector(".tool-call-chip");
    chipElement.textContent = chip;
    chipElement.title = chip;
  } else {
    head.querySelector(".tool-call-text").textContent =
      [presentation.verb, label && label !== presentation.verb ? label : ""]
        .filter(Boolean).join(" ") || name;
  }
  head.addEventListener("click", () => {
    const open = wrap.classList.toggle("open");
    wrap._body.hidden = !open;
  });

  const sub = document.createElement("div");
  sub.className = "tool-call-sub";
  sub.hidden = true;

  const body = document.createElement("div");
  body.className = "tool-call-body";
  body.hidden = true;
  const pre = document.createElement("pre");
  pre.textContent = "…";
  body.appendChild(pre);

  wrap.append(head, sub, body);
  wrap._sub = sub;
  wrap._body = body;
  wrap._pre = pre;
  return wrap;
}

/** The quiet line under a finished call, e.g. "Read 250 lines". */
function outputSummary(name, output) {
  const text = String(output || "");
  const lines = /\((\d+) lines?\)/.exec(text);
  if (name === "read_file" && lines) return `Read ${lines[1]} lines`;
  if (name === "write_file" && lines) return `Wrote ${lines[1]} lines`;
  if (name === "edit_file") {
    const replacements = /\((\d+) replacement/.exec(text);
    if (replacements) {
      return `Edited ${replacements[1]} replacement${replacements[1] === "1" ? "" : "s"}`;
    }
  }
  const first = text.split("\n").find((line) => line.trim());
  return first ? truncate(first) : "Done";
}

function finishToolRow(row, output, ok) {
  row.classList.remove("running");
  row.classList.toggle("failed", !ok);
  row._pre.textContent = output || "(no output)";
  row._sub.textContent = outputSummary(row._name, output);
  row._sub.hidden = false;
  if (!ok) {
    // A blocked call is worth reading immediately, like the reference desktop agent does.
    row.classList.add("open");
    row._body.hidden = false;
  }
}

function fileUrl(path) {
  const conversation = state.active && state.active.id;
  if (!conversation || !path) return "";
  const value = String(path);
  const uploadPrefix = `uploads/${conversation}/`;
  const route = value.startsWith(uploadPrefix)
    ? `uploads/${value.slice(uploadPrefix.length)}`
    : `files/${value}`;
  const encodedRoute = route.split("/").map(encodeURIComponent).join("/");
  return `/api/conversations/${encodeURIComponent(conversation)}/${encodedRoute}`;
}

function showcaseFile(path) {
  const url = fileUrl(path);
  if (!url) return null;
  const name = String(path).split("/").pop() || path;
  const extension = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  const card = document.createElement("figure");
  card.className = "file-showcase";

  let preview;
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(extension)) {
    preview = document.createElement("img");
    preview.src = url;
    preview.alt = name;
    preview.loading = "lazy";
  } else if (["mp4", "webm", "mov"].includes(extension)) {
    preview = document.createElement("video");
    preview.src = url;
    preview.controls = true;
  } else if (["mp3", "wav", "ogg", "m4a"].includes(extension)) {
    preview = document.createElement("audio");
    preview.src = url;
    preview.controls = true;
  } else {
    preview = document.createElement("a");
    preview.href = url;
    preview.textContent = "Open file";
    preview.target = "_blank";
    preview.rel = "noopener";
  }
  card.appendChild(preview);
  const caption = document.createElement("figcaption");
  const link = document.createElement("a");
  link.href = url;
  link.textContent = path;
  link.target = "_blank";
  link.rel = "noopener";
  caption.appendChild(link);
  card.appendChild(caption);
  return card;
}

/** Consecutive tool rows stack tightly, the way the reference desktop agent lists steps. */
function toolStack() {
  const stack = document.createElement("div");
  stack.className = "tool-stack";
  return stack;
}

function appendToolCallInto(sink, row) {
  const last = sink.lastElementChild;
  if (last && last.classList.contains("tool-stack")) {
    last.appendChild(row);
  } else {
    const stack = toolStack();
    stack.appendChild(row);
    sink.appendChild(stack);
  }
  return row;
}

function appendToolCall(row) {
  appendToolCallInto(el.messages, row);
  scrollDown();
  return row;
}

/**
 * Render a stored conversation from its Responses-API items.
 *
 * `entries` carry each item's stored timestamp, token count and item id
 * alongside the payload, which is what lets a reloaded message keep its
 * footer and lets older pages dedupe against already-rendered items.
 *
 * By default this is an authoritative full render (fresh state, whole
 * transcript). `openConversation` passes only the newest page plus
 * `hasMore`, and `prependEntries` feeds older pages through the same
 * per-entry renderer into a fragment, so tool-call rows and todo cards
 * stay consistent across page boundaries.
 */
let entryRows = new Map();
let entryTodos = null;

function renderEntryInto(sink, entry) {
  const item = entry && entry.payload;
  if (!item || typeof item !== "object") return;
  const meta = entry.meta || {};

  if (item.role === "user") {
    sink.appendChild(userMessage(
      textFromContent(item.content),
      meta.attachments || [],
      meta.message_id || ""
    ));
    return;
  }
  if (item.type === "message" || item.role === "assistant") {
    const text = textFromContent(item.content);
    if (text.trim()) {
      const bubble = sink.appendChild(assistantBubble(text));
      attachFooter(bubble, { at: entry.created_at, outputTokens: meta.output_tokens });
    }
    return;
  }
  if (item.type === "function_call") {
    // The plan is state, not an event: show the latest version as one card
    // rather than a tool row per revision.
    if (item.name === "todo_write") {
      const parsed = parseTodoArgs(item.arguments);
      if (parsed.length) {
        if (entryTodos) renderTodos(entryTodos, parsed);
        else { entryTodos = todoCard(parsed); sink.appendChild(entryTodos); }
      }
      return;
    }
    if (HIDDEN_TOOL_NAMES.has(item.name)) {
      entryRows.set(item.call_id || item.id, { hidden: true });
      return;
    }
    const row = appendToolCallInto(sink, toolRow(item.name || "tool", item.arguments));
    entryRows.set(item.call_id || item.id, { row, name: item.name, arguments: item.arguments });
    return;
  }
  if (item.type === "function_call_output") {
    const call = entryRows.get(item.call_id);
    const output = typeof item.output === "string" ? item.output : JSON.stringify(item.output);
    const ok = !/^Error:|did not allow/.test(output);
    if (call && !call.hidden) {
      finishToolRow(call.row, output, ok);
      if (ok && call.name === "showcase_file") {
        try {
          const args = JSON.parse(call.arguments || "{}");
          const card = showcaseFile(args.path);
          if (card) sink.appendChild(card);
        } catch { /* the tool row still records malformed historical arguments */ }
      }
    }
  }
}

function renderEntries(entries, hasMore = null, scroll = true) {
  el.messages.replaceChildren();
  entryRows = new Map();
  entryTodos = null;
  // A full render is authoritative: whatever pagination window existed
  // before belongs to a previous transcript.
  state.entryIds = new Set();
  state.entryCursor = null;
  state.entryHasMore = false;

  for (const entry of entries) {
    renderEntryInto(el.messages, entry);
  }
  trackEntryWindow(entries, hasMore);

  el.messages.appendChild(el.emptyState);
  el.emptyState.hidden = entries.length > 0;
  if (scroll) scrollDown(true);
}

/**
 * Track which transcript items are rendered and how far back they reach.
 *
 * `entryCursor` is the oldest rendered item id (the pagination cursor for
 * the next older page) and `entryHasMore` says whether such a page exists.
 */
function trackEntryWindow(entries, hasMore = null) {
  const ids = entries.map((entry) => entry.id).filter((id) => typeof id === "number");
  if (ids.length) {
    const oldest = Math.min(...ids);
    state.entryCursor = state.entryCursor === null ? oldest : Math.min(state.entryCursor, oldest);
    for (const id of ids) state.entryIds.add(id);
  }
  if (hasMore !== null) state.entryHasMore = hasMore;
}

/**
 * Prepend an already-fetched older page (newest→oldest input, rendered
 * oldest first) above the current transcript without losing the scroll
 * position: capture the viewport offsets, insert, then compensate for the
 * height the inserted rows added.
 */
function prependEntries(entries) {
  if (!entries.length) return;
  const box = el.messages;
  const first = box.firstElementChild;
  const fragment = document.createDocumentFragment();
  entryRows = new Map();
  entryTodos = null;
  for (const entry of entries) {
    renderEntryInto(fragment, entry);
  }
  const prevHeight = box.scrollHeight;
  const prevTop = box.scrollTop;
  box.insertBefore(fragment, first);
  box.scrollTop = box.scrollHeight - prevHeight + prevTop;
}

function parseTodoArgs(raw) {
  try {
    const parsed = JSON.parse(raw || "{}");
    return Array.isArray(parsed.todos) ? parsed.todos : [];
  } catch {
    return [];
  }
}

function textFromContent(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part && typeof part.text === "string")
    .map((part) => part.text)
    .join("");
}
