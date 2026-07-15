document.documentElement.classList.add("js");

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const revealQuery = window.matchMedia("(min-width: 40rem) and (prefers-reduced-motion: no-preference)");
const revealItems = [...document.querySelectorAll("[data-reveal]")];

if (revealQuery.matches && "IntersectionObserver" in window) {
  const revealObserver = new IntersectionObserver((entries, observer) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-in");
      observer.unobserve(entry.target);
    });
  });
  revealItems.forEach((item) => revealObserver.observe(item));
} else {
  revealItems.forEach((item) => item.classList.add("is-in"));
}

const commandDialog = document.querySelector("#command-menu");
const commandTrigger = document.querySelector("#command-trigger");
const commandInput = document.querySelector("#command-input");
const commandEmpty = document.querySelector("#command-empty");
const commandItems = [...document.querySelectorAll("[data-command]")];
const commandGroups = [...document.querySelectorAll("[data-command-group]")];
const commandClose = document.querySelector("[data-command-close]");
let activeCommand = 0;
let returnFocus = null;

function visibleCommands() {
  return commandItems.filter((item) => !item.hidden);
}

function setActiveCommand(index) {
  const items = visibleCommands();
  if (!items.length) return;
  activeCommand = (index + items.length) % items.length;
  commandItems.forEach((item) => {
    const selected = item === items[activeCommand];
    item.classList.toggle("is-active", selected);
    item.setAttribute("aria-selected", String(selected));
  });
  items[activeCommand].scrollIntoView({ block: "nearest" });
}

function filterCommands() {
  const query = commandInput.value.trim().toLocaleLowerCase();
  commandItems.forEach((item) => {
    item.hidden = Boolean(query) && !item.dataset.search.includes(query);
  });
  commandGroups.forEach((group) => {
    group.hidden = !group.querySelector("[data-command]:not([hidden])");
  });
  commandEmpty.hidden = visibleCommands().length > 0;
  activeCommand = 0;
  setActiveCommand(0);
}

function openCommands() {
  if (!commandDialog || commandDialog.open) return;
  returnFocus = document.activeElement instanceof HTMLElement && document.activeElement !== document.body
    ? document.activeElement
    : commandTrigger;
  commandInput.value = "";
  filterCommands();
  commandDialog.showModal();
  document.body.dataset.dialogOpen = "true";
  window.requestAnimationFrame(() => commandInput.focus());
}

function closeCommands() {
  if (!commandDialog?.open) return;
  commandDialog.close();
}

commandTrigger?.addEventListener("click", openCommands);
commandClose?.addEventListener("click", closeCommands);

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
    event.preventDefault();
    commandDialog?.open ? closeCommands() : openCommands();
  }
});

commandInput?.addEventListener("input", filterCommands);
commandInput?.addEventListener("keydown", (event) => {
  const items = visibleCommands();
  if (event.key === "ArrowDown") {
    event.preventDefault();
    setActiveCommand(activeCommand + 1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    setActiveCommand(activeCommand - 1);
  } else if (event.key === "Enter" && items.length) {
    event.preventDefault();
    items[activeCommand].click();
  }
});

commandItems.forEach((item) => {
  item.addEventListener("pointerenter", () => {
    const index = visibleCommands().indexOf(item);
    if (index >= 0) setActiveCommand(index);
  });
  item.addEventListener("click", closeCommands);
});

commandDialog?.addEventListener("click", (event) => {
  if (event.target === commandDialog) closeCommands();
});

commandDialog?.addEventListener("close", () => {
  delete document.body.dataset.dialogOpen;
  if (returnFocus instanceof HTMLElement) returnFocus.focus({ preventScroll: true });
});

const copyStatus = document.querySelector("#copy-status");

async function writeClipboard(value) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Continue to the selection-based fallback when browser permission is unavailable.
    }
  }
  const fallback = document.createElement("textarea");
  fallback.value = value;
  fallback.setAttribute("readonly", "");
  fallback.style.position = "fixed";
  fallback.style.opacity = "0";
  document.body.append(fallback);
  fallback.select();
  const copied = document.execCommand("copy");
  fallback.remove();
  if (!copied) throw new Error("Clipboard command was rejected");
}

function restoreCopyButton(button) {
  window.clearTimeout(Number(button.dataset.restoreTimer));
  button.textContent = button.dataset.label;
  delete button.dataset.state;
  delete button.dataset.restoreTimer;
}

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const source = document.getElementById(button.dataset.copyTarget);
    if (!source) return;
    button.disabled = true;
    button.dataset.state = "loading";
    button.textContent = "Copying…";
    try {
      await writeClipboard(source.textContent.trim());
      button.dataset.state = "success";
      button.textContent = "Copied";
      copyStatus.textContent = `${button.dataset.label} copied to clipboard.`;
    } catch {
      button.dataset.state = "error";
      button.textContent = "Copy failed";
      copyStatus.textContent = "Clipboard access failed. Select and copy the command manually.";
    } finally {
      button.disabled = false;
      button.dataset.restoreTimer = String(window.setTimeout(() => restoreCopyButton(button), 2500));
    }
  });
  button.addEventListener("pointerleave", () => {
    if (button.dataset.state === "success") restoreCopyButton(button);
  });
});

const demoVideo = document.querySelector("#demo-video");
const videoToggle = document.querySelector("#video-toggle");

function updateVideoControl() {
  const playing = Boolean(demoVideo && !demoVideo.paused);
  videoToggle.textContent = playing ? "Pause demo" : "Play demo";
  videoToggle.setAttribute("aria-pressed", String(playing));
  delete videoToggle.dataset.state;
}

videoToggle?.addEventListener("click", async () => {
  if (demoVideo.paused) {
    videoToggle.dataset.state = "loading";
    videoToggle.textContent = "Starting…";
    try {
      await demoVideo.play();
    } catch {
      videoToggle.dataset.state = "error";
      videoToggle.textContent = "Playback failed";
    }
  } else {
    demoVideo.pause();
  }
});

demoVideo?.addEventListener("play", updateVideoControl);
demoVideo?.addEventListener("pause", updateVideoControl);
reduceMotion.addEventListener("change", (event) => {
  if (event.matches) demoVideo?.pause();
});
updateVideoControl();

const currentYear = document.querySelector("#current-year");
if (currentYear) currentYear.textContent = String(new Date().getFullYear());
