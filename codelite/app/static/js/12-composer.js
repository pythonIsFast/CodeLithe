/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

function renderAttachments() {
  el.attachments.replaceChildren();
  el.attachments.hidden = state.attachments.length === 0;
  for (const attachment of state.attachments) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = `Remove ${attachment.name}`;
    remove.setAttribute("aria-label", remove.title);
    remove.addEventListener("click", () => {
      state.attachments = state.attachments.filter((item) => item !== attachment);
      if (attachment.url) URL.revokeObjectURL(attachment.url);
      renderAttachments();
    });

    if (String(attachment.type || "").startsWith("image/")) {
      const preview = document.createElement("div");
      preview.className = "attachment-preview";
      preview.title = attachment.name;
      const image = document.createElement("img");
      image.src = attachment.url || fileUrl(attachment.path);
      image.alt = attachment.name;
      image.addEventListener("error", () => image.remove());
      remove.className = "attachment-preview-remove";
      preview.append(image, remove);
      el.attachments.appendChild(preview);
      continue;
    }

    const chip = document.createElement("span");
    chip.className = "attachment-chip";
    const name = document.createElement("span");
    name.textContent = attachment.name;
    name.title = attachment.path;
    chip.append(name, remove);
    el.attachments.appendChild(chip);
  }
  updateSendState();
}

function clearAttachments() {
  for (const attachment of state.attachments) {
    if (attachment.url) URL.revokeObjectURL(attachment.url);
  }
  state.attachments = [];
  renderAttachments();
}

async function uploadAttachment(file, conversationId = null) {
  const target = conversationId || state.active?.id;
  if (!target) throw new Error("Open a conversation before pasting a file.");
  if (file.size > MAX_UPLOAD_BYTES) throw new Error("Files must be 50 MB or smaller.");
  const form = new FormData();
  form.append("file", file, file.name || "pasted-file");
  const response = await fetch(`/api/conversations/${target}/uploads`, {
    method: "POST",
    body: form,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Upload failed (${response.status})`);
  return data;
}

function clipboardFiles(clipboardData) {
  if (!clipboardData) return [];
  const files = [];
  const seen = new Set();
  const add = (file) => {
    if (!(file instanceof File)) return;
    const key = `${file.name}:${file.size}:${file.type}:${file.lastModified}`;
    if (!seen.has(key)) { seen.add(key); files.push(file); }
  };
  for (const file of clipboardData.files || []) add(file);
  for (const item of clipboardData.items || []) {
    if (item.kind === "file") add(item.getAsFile());
  }
  return files;
}

async function navigatorClipboardFiles() {
  if (!navigator.clipboard?.read) return [];
  try {
    const items = await navigator.clipboard.read();
    const files = [];
    for (const item of items) {
      const type = item.types.find((candidate) => !candidate.startsWith("text/"));
      if (!type) continue;
      const blob = await item.getType(type);
      const extension = type === "image/png" ? "png" : type === "image/jpeg" ? "jpg" : "bin";
      files.push(new File([blob], `pasted-file.${extension}`, { type }));
    }
    return files;
  } catch {
    // Clipboard read permission is unavailable in some WebView backends.
    return [];
  }
}

async function nativeClipboardImage() {
  if (!window.pywebview?.api?.read_clipboard_image) return [];
  try {
    const image = await window.pywebview.api.read_clipboard_image();
    if (!image?.data || !image.type) return [];
    const binary = atob(image.data);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return [new File([bytes], image.name || "pasted-screenshot.png", { type: image.type })];
  } catch {
    return [];
  }
}

async function pastedFilesFallback() {
  const browserFiles = await navigatorClipboardFiles();
  return browserFiles.length ? browserFiles : nativeClipboardImage();
}

async function addPastedFiles(files) {
  if (!files.length || state.busy || (!state.active && !state.draft)) return;

  // In a draft there is no conversation id to upload into yet, so the files
  // stay local (with an object URL for previews) and are uploaded as soon as
  // the first prompt creates the conversation.
  if (!state.active) {
    for (const file of files) {
      if (file.size > MAX_UPLOAD_BYTES) {
        toast("Files must be 50 MB or smaller.", true);
        continue;
      }
      const type = file.type || "application/octet-stream";
      const attachment = { file, name: file.name || "pasted-file", type };
      if (type.startsWith("image/")) attachment.url = URL.createObjectURL(file);
      state.attachments.push(attachment);
    }
    renderAttachments();
    return;
  }

  state.uploadsInProgress += files.length;
  setBusy(true, "Uploading attachment…");
  try {
    const uploaded = await Promise.all([...files].map((file) => uploadAttachment(file)));
    state.attachments.push(...uploaded);
    renderAttachments();
    toast(`${uploaded.length} file${uploaded.length === 1 ? "" : "s"} attached.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.uploadsInProgress -= files.length;
    setBusy(false);
  }
}

async function sendMessage() {
  const text = el.prompt.value.trim();
  const draft = state.active ? null : state.draft;
  if ((!text && !state.attachments.length) || (!state.active && !draft)) return;
  if (draft && !draft.model) {
    toast("Enable a model in Settings → Model picker.", true);
    return;
  }
  const steering = Boolean(state.active && state.busy);
  if (steering && state.active.model !== "gpt-6-astra") return;
  if (steering && state.attachments.length) {
    toast("Send attachment follow-ups after the current Astra run finishes.");
    return;
  }
  if (state.uploadsInProgress) {
    toast("Wait for the attachment upload to finish.");
    return;
  }

  // A draft becomes a real conversation only at this point. Creating it in
  // the same action as the first message matches the reference desktop agent: an abandoned
  // new-session screen never leaves an empty chat in the sidebar.
  if (draft) {
    setBusy(true, "Starting session…");
    let conversation;
    try {
      conversation = await post("/api/conversations", {
        workspace: draft.workspace,
        mode: draft.mode,
        model: draft.model,
        reasoning_effort: draft.effort,
        fast_mode: draft.fast,
        flex_mode: draft.flex,
      });
    } catch (error) {
      toast(error.message, true);
      setBusy(false);
      return;
    }

    state.active = conversation;
    state.draft = null;
    el.composerContext.hidden = true;
    el.titleButton.classList.remove("untitled");
    el.title.textContent = conversation.title || "New session";
    renderDetails();
    await loadConversations();
    connectStream(conversation.id);

    // Draft attachments had no conversation to upload into; do it now before
    // the message that references them is sent.
    const local = state.attachments.filter((attachment) => attachment.file);
    if (local.length) {
      setBusy(true, "Uploading attachment…");
      try {
        const uploaded = [];
        for (const attachment of state.attachments) {
          uploaded.push(
            attachment.file
              ? await uploadAttachment(attachment.file, conversation.id)
              : attachment,
          );
        }
        for (const attachment of local) {
          if (attachment.url) URL.revokeObjectURL(attachment.url);
        }
        state.attachments = uploaded;
        renderAttachments();
      } catch (error) {
        // Do not leave behind a session whose first prompt never arrived.
        await del(`/api/conversations/${conversation.id}`).catch(() => {});
        disconnectStream();
        state.active = null;
        state.draft = draft;
        el.composerContext.hidden = false;
        el.title.textContent = "New session";
        el.titleButton.classList.add("untitled");
        await loadConversations();
        renderDetails();
        updateDraftControls();
        setBusy(false);
        toast(error.message, true);
        return;
      }
    }
  }

  const attachments = [...state.attachments];
  const attachmentPaths = attachments.map((attachment) => attachment.path);
  const attachmentNote = attachmentPaths.length
    ? `Attached workspace files:\n${attachmentPaths.map((path) => `- ${path}`).join("\n")}`
    : "";
  const message = [attachmentNote, text].filter(Boolean).join("\n\n") || "Please inspect the attached file.";

  const messageId = globalThis.crypto?.randomUUID?.() ||
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  el.prompt.value = "";
  el.prompt.style.height = "auto";
  el.emptyState.hidden = true;
  const optimistic = append(userMessage(text, attachments, messageId));
  optimistic.classList.add("pending");
  scrollDown(true);
  resetLive();
  setBusy(true);
  state.pendingMessages.add(messageId);

  try {
    await post(`/api/conversations/${state.active.id}/messages`, {
      text: message,
      attachments,
      message_id: messageId,
    });
    optimistic.classList.remove("pending");
    clearAttachments();
  } catch (error) {
    optimistic.classList.remove("pending");
    append(errorBubble(error.message));
    if (!steering) setBusy(false);
  } finally {
    state.pendingMessages.delete(messageId);
  }
}
