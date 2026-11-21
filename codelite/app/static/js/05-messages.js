/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/**
 * A small Markdown subset for chat messages, including tables and fenced code.
 */
function inlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function markdownTableCells(line) {
  if (!line?.includes("|")) return null;
  // Some models escape the opening pipe and use backslash line breaks.
  const row = line.trim().replace(/^\\\|/, "|").replace(/\|\s*\\$/, "|");
  const cells = [""];
  let lastToken = "";
  for (const [token] of row.matchAll(/`[^`]+`|\\[\\|]|\||[^`\\|]+|[`\\]/g)) {
    if (token === "|") {
      cells.push("");
    } else {
      cells[cells.length - 1] += token.replace(/\\([\\|])/g, "$1");
    }
    lastToken = token;
  }
  if (cells.length === 1) return null;
  if (row.startsWith("|")) cells.shift();
  if (lastToken === "|") cells.pop();
  return cells.map((cell) => cell.trim());
}

function renderMarkdownTable(lines, start) {
  const header = markdownTableCells(lines[start]);
  if (!header?.length) return null;
  const separator = markdownTableCells(lines[start + 1]);
  if (separator?.length !== header.length || !separator.every((cell) => /^:?-+:?$/.test(cell))) {
    return null;
  }

  const alignment = separator.map((cell) => {
    if (!cell.endsWith(":")) return "";
    return cell.startsWith(":") ? ' class="align-center"' : ' class="align-right"';
  });
  const headings = header.map((cell, index) =>
    `<th scope="col"${alignment[index]}>${inlineMarkdown(cell)}</th>`).join("");
  const rows = [];
  let nextLine = start + 2;
  for (; nextLine < lines.length; nextLine++) {
    const cells = markdownTableCells(lines[nextLine]);
    if (!cells?.length) break;
    const columns = header.map((_, index) =>
      `<td${alignment[index]}>${inlineMarkdown(cells[index] || "")}</td>`).join("");
    rows.push(`<tr>${columns}</tr>`);
  }
  return {
    html: `<div class="markdown-table-scroll"><table><thead><tr>${headings}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`,
    nextLine,
  };
}

/**
 * Render one blank-line-separated block.
 *
 * Classified per line rather than per block: a block is often a heading with
 * its text underneath, or a sentence followed by bullets. Deciding once for the
 * whole block meant anything mixed fell through to a paragraph -- which is why
 * a `## heading` used to render as literal hashes.
 */
function renderTextBlock(block) {
  const out = [];
  let list = [];
  let paragraph = [];
  const flushList = () => {
    if (list.length) out.push(`<ul>${list.join("")}</ul>`);
    list = [];
  };
  const flushParagraph = () => {
    if (paragraph.length) out.push(`<p>${paragraph.join("<br />")}</p>`);
    paragraph = [];
  };

  const lines = block.split("\n");
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const table = renderMarkdownTable(lines, index);
    if (table) {
      flushList();
      flushParagraph();
      out.push(table.html);
      index = table.nextLine - 1;
      continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      flushList();
      flushParagraph();
      // Offset by three: the surrounding page owns h1-h3, so a message's own
      // "#" must not compete with the app's headings.
      const level = Math.min(6, heading[1].length + 3);
      out.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) {
      flushParagraph();
      list.push(`<li>${inlineMarkdown(bullet[1])}</li>`);
      continue;
    }
    flushList();
    paragraph.push(inlineMarkdown(line));
  }
  flushList();
  flushParagraph();
  return out.join("");
}

function formatMarkdown(text) {
  const parts = String(text).replace(/\r\n?/g, "\n").split(/```/);
  return parts
    .map((part, index) => {
      if (index % 2 === 1) {
        const newline = part.indexOf("\n");
        const body = newline === -1 ? part : part.slice(newline + 1);
        return `<pre><code>${escapeHtml(body.replace(/\n$/, ""))}</code></pre>`;
      }
      return part
        .split(/\n{2,}/)
        .filter((block) => block.trim())
        .map(renderTextBlock)
        .join("");
    })
    .join("");
}

function atBottom() {
  const box = el.messages;
  return box.scrollHeight - box.scrollTop - box.clientHeight < 120;
}

function scrollDown(force = false) {
  if (force || atBottom()) el.messages.scrollTop = el.messages.scrollHeight;
}

function append(node) {
  const stick = atBottom();
  el.messages.appendChild(node);
  if (stick) scrollDown(true);
  return node;
}

/* -- Rendering ------------------------------------------------------------ */

function userBubble(text) {
  const div = document.createElement("div");
  div.className = "msg user";
  div.textContent = text;
  return div;
}

function userTextForDisplay(text) {
  return String(text)
    .replace(/^Attached workspace files:\n(?:- .+\n?)+\n*/, "")
    .trim() || "Attached file";
}

function userMessage(text, attachments = [], messageId = "") {
  const message = document.createElement("div");
  message.className = "user-message";
  if (messageId) message.dataset.messageId = messageId;
  for (const attachment of attachments) {
    if (!String(attachment.type || "").startsWith("image/")) continue;
    const image = document.createElement("img");
    image.className = "user-image-attachment";
    image.src = attachment.url || fileUrl(attachment.path);
    image.alt = attachment.name || attachment.path;
    // User uploads are part of the message itself. Loading them eagerly avoids
    // Edge's lazy-image placeholder leaving a blank gap above fresh messages.
    image.addEventListener("error", () => image.remove());
    message.appendChild(image);
  }
  message.appendChild(userBubble(userTextForDisplay(text)));
  return message;
}

function assistantBubble(text = "") {
  const div = document.createElement("div");
  div.className = "msg assistant";
  div.dataset.raw = text;
  div.innerHTML = formatMarkdown(text);
  return div;
}

function streamingAssistantBubble() {
  const div = document.createElement("div");
  div.className = "msg assistant streaming";
  div.appendChild(document.createTextNode(""));
  return div;
}

function errorBubble(message) {
  const div = document.createElement("div");
  div.className = "msg error";
  div.textContent = message;
  return div;
}

function reasoningBlock(text = "") {
  const div = document.createElement("div");
  div.className = "reasoning";
  div.appendChild(document.createTextNode(text));
  return div;
}

function modelDecisionCard(data) {
  const card = document.createElement("section");
  card.className = "model-decision";
  const title = document.createElement("strong");
  // The effort is part of the decision, not a detail: it changes how long
  // every turn of the run takes, so it belongs in the headline.
  title.textContent = data.effort
    ? `Auto selected ${modelDisplayName(data)} at ${data.effort} effort`
    : `Auto selected ${modelDisplayName(data)}`;
  const reason = document.createElement("p");
  reason.textContent = data.reason || "Choosing the lowest capable model.";
  card.append(title, reason);

  const profiles = data.profiles || {};
  const list = document.createElement("div");
  list.className = "model-decision-profiles";
  for (const [model, profile] of Object.entries(profiles)) {
    if (!profile) continue;
    const row = document.createElement("p");
    row.className = model === data.model ? "selected" : "";
    row.textContent = `${profile.name}: ${profile.fit} ${profile.limit}`;
    list.appendChild(row);
  }
  card.appendChild(list);
  return card;
}

function modelDisplayName(data) {
  return data.model_name || data.model;
}
