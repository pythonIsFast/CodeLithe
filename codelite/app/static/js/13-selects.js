/* Part of the CodeLithe UI. Loaded as a classic script in numeric order
 * from index.html; the files share one global scope on purpose, because
 * the interface deliberately has no build step. */

/* -- Custom selects ------------------------------------------------------- */

const customSelects = new Map();

function closeCustomSelects(except = null) {
  for (const picker of customSelects.values()) {
    if (picker === except) continue;
    picker.root.classList.remove("open");
    picker.trigger.setAttribute("aria-expanded", "false");
    picker.menu.hidden = true;
  }
}

function renderCustomSelect(select) {
  const picker = customSelects.get(select);
  if (!picker) return;
  const selected = select.selectedOptions[0];
  picker.trigger.textContent = selected?.textContent || "Select…";
  picker.menu.replaceChildren();
  for (const option of select.options) {
    if (option.hidden || option.disabled) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "custom-select-option";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(option.selected));
    button.textContent = option.textContent;
    button.addEventListener("click", () => {
      select.value = option.value;
      renderCustomSelect(select);
      closeCustomSelects();
      select.dispatchEvent(new Event("change", { bubbles: true }));
      picker.trigger.focus();
    });
    picker.menu.appendChild(button);
  }
}

function changeCustomSelect(select, direction) {
  const options = [...select.options].filter((option) => !option.hidden && !option.disabled);
  const index = options.findIndex((option) => option.selected);
  const target = options[Math.max(0, Math.min(options.length - 1, index + direction))];
  if (!target || target.selected) return;
  select.value = target.value;
  renderCustomSelect(select);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

function fitSettingsSelect(picker) {
  const scrollport = picker.root.closest(".settings-content");
  if (!scrollport) return;
  const bounds = scrollport.getBoundingClientRect();
  const anchor = picker.root.getBoundingClientRect();
  const gap = 6;
  const above = anchor.top - bounds.top - gap;
  const below = bounds.bottom - anchor.bottom - gap;

  picker.menu.style.maxHeight = "";
  const openUp = picker.menu.offsetHeight > below && above > below;
  picker.root.classList.toggle("opens-up", openUp);
  picker.menu.style.maxHeight = `${Math.max(0, Math.floor(openUp ? above : below))}px`;
}

/** Wire one picker into the customSelects map. Safe to call more than once
 * for the same root (behaviour settings rebuild their pickers on every
 * render), since it always re-reads the current DOM nodes. */
function initCustomSelect(root) {
  const select = root.querySelector("select");
  const trigger = root.querySelector(".custom-select-trigger");
  const menu = root.querySelector(".custom-select-menu");
  const picker = { root, trigger, menu };
  customSelects.set(select, picker);
  trigger.setAttribute("aria-label", select.title || root.closest("label")?.querySelector("span")?.textContent || "Select option");
  trigger.addEventListener("click", () => {
    const opening = menu.hidden;
    closeCustomSelects(picker);
    root.classList.toggle("open", opening);
    trigger.setAttribute("aria-expanded", String(opening));
    menu.hidden = !opening;
    if (opening) {
      fitSettingsSelect(picker);
      const selected = menu.querySelector("[aria-selected='true']");
      if (selected && root.closest(".settings-content")) {
        // Reveal the selection within the menu without scrolling the page.
        menu.scrollTop = selected.offsetTop - (menu.clientHeight - selected.offsetHeight) / 2;
        selected.focus({ preventScroll: true });
      } else {
        selected?.focus();
      }
    }
  });
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (menu.hidden) changeCustomSelect(select, event.key === "ArrowDown" ? 1 : -1);
    } else if ((event.key === "Enter" || event.key === " ") && menu.hidden) {
      event.preventDefault();
      trigger.click();
    }
  });
  renderCustomSelect(select);
}

function initCustomSelects() {
  for (const root of document.querySelectorAll("[data-select-picker]")) {
    initCustomSelect(root);
  }
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-select-picker]")) closeCustomSelects();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeCustomSelects();
  });
}
