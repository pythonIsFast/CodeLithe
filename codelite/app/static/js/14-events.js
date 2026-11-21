/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

function dataTransferHasFiles(dataTransfer) {
  if (!dataTransfer) return false;
  if (dataTransfer.files?.length) return true;
  if ([...(dataTransfer.items || [])].some((item) => item.kind === "file")) return true;
  return [...(dataTransfer.types || [])].includes("Files");
}

function wireEvents() {
  // Transcript pagination: near the top of the message area, fetch the next
  // older page and prepend it (see `maybeLoadOlderEntries`).
  el.messages.addEventListener("scroll", () => { maybeLoadOlderEntries(); });
  el.titleButton.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleTitleMenu();
  });
  el.sidebarToggle.addEventListener("click", () => setSidebarOpen(!state.sidebarOpen));
  el.detailsToggle.addEventListener("click", () => setDetailsOpen(!state.detailsOpen));
  el.detailRemote.addEventListener("click", () => openSettings("remote"));
  el.topbarSettings.addEventListener("click", () => openSettings());
  // The behaviour panels render from the schema inside openSettings, so the
  // first of them is addressable by the time the tab is selected.
  el.sideCustomize.addEventListener("click", () => openSettings("group:provider"));
  el.sideSearch.addEventListener("click", () => {
    const show = el.sideSearchField.hidden;
    el.sideSearchField.hidden = !show;
    el.sideSearch.setAttribute("aria-expanded", String(show));
    if (show) {
      el.conversationFilter.focus();
    } else {
      el.conversationFilter.value = "";
      state.filter = "";
      renderConversationList();
    }
  });
  el.conversationFilter.addEventListener("input", () => {
    state.filter = el.conversationFilter.value;
    renderConversationList();
  });

  $("auth-opencode-go").addEventListener("click", () => openSettings("opencode-go"));
  $("auth-custom-provider").addEventListener("click", () => openSettings("custom-providers"));
  $("custom-provider-form").addEventListener("submit", saveCustomProvider);
  $("custom-provider-cancel").addEventListener("click", () => editCustomProvider());
  $("go-keys").addEventListener("submit", (event) => {
    event.preventDefault();
    const changes = {};
    const inference = $("go-api-key").value.trim();
    const usage = $("go-usage-key").value.trim();
    if (inference) changes.api_key = inference;
    if (usage) changes.usage_key = usage;
    if (inference || usage) saveGoKeys(changes);
  });
  $("go-remove-api-key").addEventListener("click", () => saveGoKeys({ api_key: "" }));
  $("go-remove-usage-key").addEventListener("click", () => saveGoKeys({ usage_key: "" }));
  $("go-refresh-usage").addEventListener("click", refreshGoUsage);
  $("model-visibility").addEventListener("submit", saveModelVisibility);
  el.authLogin.addEventListener("click", startAuthLogin);
  el.authFree.addEventListener("click", () => {
    enablePollinationsFree().catch((error) => {
      el.authStatus.textContent = error.message;
      el.authStatus.classList.add("error");
    });
  });
  el.settingsLogin.addEventListener("click", startAuthLogin);
  el.authCopyLink.addEventListener("click", copyAuthLink);
  el.settingsAuthCopyLink.addEventListener("click", copyAuthLink);
  el.updateButton.addEventListener("click", runUpdate);
  el.bankedResetsRedeem.addEventListener("click", redeemBankedReset);
  el.sidebarUpdate.addEventListener("click", () => openSettings("updates"));
  el.remoteDownloadButton.addEventListener("click", downloadRemote);
  el.remoteStart.addEventListener("click", startRemote);
  el.remoteStop.addEventListener("click", stopRemote);
  el.remoteRemove.addEventListener("click", removeRemote);
  el.remoteCopyUrl.addEventListener("click", () => copyRemoteValue(el.remoteUrl, "URL"));
  el.remoteCopyPassword.addEventListener("click", () => copyRemoteValue(el.remotePassword, "Password"));
  el.attachFiles.addEventListener("click", () => el.attachmentPicker.click());
  el.attachmentPicker.addEventListener("change", () => {
    addPastedFiles([...el.attachmentPicker.files]);
    el.attachmentPicker.value = "";
  });

  document.addEventListener("dragover", (event) => {
    if (dataTransferHasFiles(event.dataTransfer)) event.preventDefault();
  }, true);
  document.addEventListener("drop", (event) => {
    if (!dataTransferHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    if (!el.composer.contains(event.target)) toast("Drop files onto the chat box to attach them.");
  }, true);

  for (const eventName of ["dragenter", "dragover"]) {
    el.composer.addEventListener(eventName, (event) => {
      if (!dataTransferHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      el.composer.classList.add("dragging-files");
    });
  }
  el.composer.addEventListener("dragleave", () => {
    el.composer.classList.remove("dragging-files");
  });
  el.composer.addEventListener("drop", (event) => {
    if (!dataTransferHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    el.composer.classList.remove("dragging-files");
    addPastedFiles(clipboardFiles(event.dataTransfer));
  });
  el.settingsButton.addEventListener("click", () => openSettings());
  el.sidebarRemote.addEventListener("click", () => openSettings("remote"));
  el.settingsOverlay.addEventListener("click", (event) => {
    if (event.target === el.settingsOverlay) {
      closeSettings();
      return;
    }
    const page = event.target.closest("[data-settings-tab], [data-settings-target]");
    if (page) {
      selectSettingsTab(page.dataset.settingsTab || page.dataset.settingsTarget);
      if (page.dataset.settingsTarget) {
        $("settings-page-title").focus();
      }
      return;
    }
    const category = event.target.closest("[data-settings-category]");
    if (category) {
      const first = settingsPages().find((item) =>
        item.closest("[data-settings-pages]").dataset.settingsPages === category.dataset.settingsCategory,
      );
      if (first) selectSettingsTab(first.dataset.settingsTab);
    }
  });
  el.settingsContent.addEventListener("click", (event) => {
    const save = event.target.closest("[data-behaviour-save]");
    const reset = event.target.closest("[data-behaviour-reset]");
    if (!save && !reset) return;
    const status = event.target.closest("[data-behaviour-panel]")?.querySelector(".behaviour-status");
    if (save) {
      saveBehaviour(collectBehaviour()).catch((error) => {
        if (status) status.textContent = error.message;
        toast(error.message, true);
      });
      return;
    }
    if (!state.behaviour) return;
    const defaults = {};
    for (const setting of state.behaviour.schema.settings) defaults[setting.key] = setting.default;
    renderBehaviour(state.behaviour.schema, defaults);
    const newStatus = el.settingsContent.querySelector(".settings-panel:not([hidden]) .behaviour-status");
    // Rendered, not saved: restoring is a proposal until the user confirms it
    // with Save, the same as any other edit in this panel.
    if (newStatus) newStatus.textContent = "Defaults filled in. Save to apply them.";
  });

  el.codexImport.addEventListener("click", () => {
    runCodexImport().catch((error) => { el.importStatus.textContent = error.message; });
  });
  el.settingsOverlay.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const controls = [...el.settingsOverlay.querySelectorAll(
      "button, input, select, textarea, a[href], [tabindex]",
    )].filter((control) => control.tabIndex >= 0 && !control.disabled && control.getClientRects().length);
    const edge = event.shiftKey ? controls[0] : controls[controls.length - 1];
    if (event.target !== edge) return;
    event.preventDefault();
    const next = event.shiftKey ? controls[controls.length - 1] : controls[0];
    next?.focus();
  });
  el.settingsClose.addEventListener("click", closeSettings);
  el.settingsSearch.addEventListener("input", () => {
    renderSettingsSearch();
    el.settingsContent.scrollTop = 0;
  });
  el.settingsSearch.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      $("settings-search-results").querySelector("button")?.click();
    }
  });
  el.globalMemorySave.addEventListener("click", () => {
    saveGlobalMemory().catch((error) => toast(error.message, true));
  });
  el.projectMemorySave.addEventListener("click", () => {
    saveProjectSetting("memory", el.projectMemory).catch((error) => toast(error.message, true));
  });
  el.projectMcpSave.addEventListener("click", () => {
    saveProjectSetting("mcp", el.projectMcpConfig).catch((error) => toast(error.message, true));
  });
  el.projectLspSave.addEventListener("click", () => {
    saveProjectSetting("lsp", el.projectLspConfig).catch((error) => toast(error.message, true));
  });

  el.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });

  el.prompt.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  el.prompt.addEventListener("input", () => {
    el.prompt.style.height = "auto";
    el.prompt.style.height = `${Math.min(el.prompt.scrollHeight, 220)}px`;
    updateSendState();
  });

  document.addEventListener("paste", (event) => {
    const files = clipboardFiles(event.clipboardData);
    if (files.length) {
      event.preventDefault();
      addPastedFiles(files);
      return;
    }
    // WebKitGTK sometimes omits files from the paste event but exposes them
    // through the asynchronous Clipboard API while the key gesture is active.
    const types = [...(event.clipboardData?.types || [])];
    // Empty types are another WebKitGTK quirk, so let the native bridge try
    // them too. Pure text pastes remain untouched.
    if (types.length === 0 || types.some((type) => !type.startsWith("text/"))) {
      pastedFilesFallback().then((clipboardFiles) => {
        if (clipboardFiles.length) addPastedFiles(clipboardFiles);
      });
    }
  });

  el.stopRun.addEventListener("click", async () => {
    if (!state.active) return;
    await post(`/api/conversations/${state.active.id}/cancel`).catch(() => {});
  });

  el.compactContext.addEventListener("click", async () => {
    if (!state.active || state.busy) return;
    setBusy(true, "Compacting earlier conversation…");
    await post(`/api/conversations/${state.active.id}/compact`).catch((error) => {
      setBusy(false);
      toast(error.message, true);
    });
  });

  el.modeSelect.addEventListener("change", async () => {
    if (!state.active && state.draft) {
      state.draft.mode = el.modeSelect.value;
      syncModeChip();
      renderDetails();
      return;
    }
    if (!state.active) return;
    const updated = await patch(`/api/conversations/${state.active.id}`, {
      mode: el.modeSelect.value,
    }).catch((error) => {
      toast(error.message, true);
      return null;
    });
    if (updated) {
      Object.assign(state.active, updated);
      syncModeChip();
      renderDetails();
      toast(`Permission mode: ${el.modeSelect.selectedOptions[0].textContent}`);
    }
  });

  el.modelSelect.addEventListener("change", async () => {
    const model = el.modelSelect.value;
    // The level list and Fast availability follow the model, so they have to
    // be rebuilt here -- a level the new model rejects would be a 400.
    fillEffortSelect(el.effortSelect, el.effortSelect.value, model);
    renderFastToggle(state.active ? state.active.fast_mode : state.draft?.fast, model);
    if (!state.active && state.draft) {
      state.draft.model = model;
      state.draft.effort = el.effortSelect.value;
      return;
    }
    if (!state.active) return;
    const body = { model };
    if (el.effortSelect.value !== (state.active.reasoning_effort || "")) {
      body.reasoning_effort = el.effortSelect.value;
    }
    const updated = await patch(`/api/conversations/${state.active.id}`, body)
      .catch((error) => { toast(error.message, true); return null; });
    if (updated) Object.assign(state.active, updated);
  });

  el.renameSave.addEventListener("click", () => {
    commitRename().catch((error) => toast(error.message, true));
  });
  el.renameCancel.addEventListener("click", () => {
    el.renameOverlay.hidden = true;
    state.pendingRename = null;
  });
  el.renameInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    commitRename().catch((error) => toast(error.message, true));
  });

  el.confirmOk.addEventListener("click", () => {
    commitDelete().catch((error) => toast(error.message, true));
  });
  el.confirmCancel.addEventListener("click", () => {
    el.confirmOverlay.hidden = true;
    state.pendingDelete = null;
  });

  // A menu anchored to a row must close on any click elsewhere, or it
  // outlives the row it belongs to. Escape is handled with the overlays below.
  document.addEventListener("click", () => {
    closeConversationMenu();
    closeEnvironmentMenu();
  });

  el.fastToggle.addEventListener("click", async () => {
    const next = el.fastToggle.getAttribute("aria-pressed") !== "true";
    renderFastToggle(next, el.modelSelect.value);
    // Mutually exclusive with Flex -- both set one Codex `service_tier`.
    if (!state.active && state.draft) {
      state.draft.fast = next;
      if (next) state.draft.flex = false;
      return;
    }
    if (!state.active) return;
    const updated = await patch(`/api/conversations/${state.active.id}`, { fast_mode: next })
      .catch((error) => { toast(error.message, true); return null; });
    if (updated) Object.assign(state.active, updated);
  });

  el.effortSelect.addEventListener("change", async () => {
    // Auto overrides this per run, so say so instead of letting the picker
    // look like it was ignored.
    if (el.modelSelect.value === "auto" && el.effortSelect.value) {
      toast("Auto picks the effort itself; this applies if you choose a model.");
    }
    if (!state.active && state.draft) {
      state.draft.effort = el.effortSelect.value;
      return;
    }
    if (!state.active) return;
    await patch(`/api/conversations/${state.active.id}`, {
      reasoning_effort: el.effortSelect.value,
    }).catch((error) => toast(error.message, true));
    if (state.active) state.active.reasoning_effort = el.effortSelect.value;
  });

  el.permOnce.addEventListener("click", () => replyPermission("once"));
  el.permSession.addEventListener("click", () => replyPermission("session"));
  el.permDeny.addEventListener("click", () => replyPermission("deny"));
  el.questionSubmit.addEventListener("click", () => replyQuestion());
  el.questionCancel.addEventListener("click", () => replyQuestion("The user cancelled the question."));
  el.questionAnswer.addEventListener("keydown", (event) => {
    if (event.key === "Enter") replyQuestion();
  });

  el.newChat.addEventListener("click", () => startNewSession());

  el.draftEnvironment.addEventListener("click", (event) => {
    event.stopPropagation();
    const show = el.environmentMenu.hidden;
    closeEnvironmentMenu();
    if (show) {
      el.environmentMenu.hidden = false;
      el.draftEnvironment.setAttribute("aria-expanded", "true");
    }
  });
  el.environmentMenu.addEventListener("click", (event) => event.stopPropagation());
  el.environmentMenu
    .querySelector("[data-environment='local']")
    .addEventListener("click", () => closeEnvironmentMenu());
  el.environmentRemote.addEventListener("click", () => {
    closeEnvironmentMenu();
    openSettings("remote");
  });
  el.draftFolder.addEventListener("click", () => {
    chooseDraftFolder().catch((error) => toast(error.message, true));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeConversationMenu();
    closeEnvironmentMenu();
    if (!el.settingsOverlay.hidden) {
      if (el.settingsSearch.value) {
        el.settingsSearch.value = "";
        renderSettingsSearch();
        el.settingsSearch.focus();
      } else {
        closeSettings();
      }
      return;
    }
    if (!el.renameOverlay.hidden) {
      el.renameOverlay.hidden = true;
      state.pendingRename = null;
    }
    if (!el.confirmOverlay.hidden) {
      el.confirmOverlay.hidden = true;
      state.pendingDelete = null;
    }
    if (!el.questionOverlay.hidden) replyQuestion("The user cancelled the question.");
  });
}

/** Keep "just now" from ageing into a lie without re-rendering the messages. */
