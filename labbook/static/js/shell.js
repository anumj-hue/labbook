// App shell behaviour: drawer, sidebar collapse, theme toggle, toasts, confirm dialogs.
(() => {
  const root = document.documentElement;
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } },
  };

  // --- Theme: light / dark / system (saved per device) ---
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(pref) {
    const dark = pref === "dark" || (pref === "system" && media.matches);
    root.dataset.theme = dark ? "dark" : "light";
    root.dataset.themePref = pref;
    document.querySelectorAll("[data-theme-choice]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(btn.dataset.themeChoice === pref));
    });
  }
  applyTheme(store.get("theme") || "system");
  media.addEventListener("change", () => applyTheme(root.dataset.themePref || "system"));
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-theme-choice]");
    if (!btn) return;
    store.set("theme", btn.dataset.themeChoice);
    applyTheme(btn.dataset.themeChoice);
  });

  // --- Sidebar collapse (desktop, remembered) ---
  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-sidebar-toggle]")) return;
    const collapsed = root.dataset.sidebar !== "collapsed";
    if (collapsed) root.dataset.sidebar = "collapsed"; else delete root.dataset.sidebar;
    store.set("sidebar", collapsed ? "collapsed" : "expanded");
  });

  // --- Drawer (mobile / tablet) ---
  const sidebar = document.getElementById("sidebar");
  let lastFocus = null;
  function openDrawer() {
    lastFocus = document.activeElement;
    root.dataset.drawer = "open";
    document.querySelectorAll("[data-drawer-open]").forEach((b) => b.setAttribute("aria-expanded", "true"));
    sidebar?.querySelector("a, button")?.focus();
  }
  function closeDrawer() {
    if (root.dataset.drawer !== "open") return;
    delete root.dataset.drawer;
    document.querySelectorAll("[data-drawer-open]").forEach((b) => b.setAttribute("aria-expanded", "false"));
    lastFocus?.focus();
  }
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-drawer-open]")) openDrawer();
    else if (e.target.closest("[data-drawer-close]")) closeDrawer();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
    if (e.key === "Tab" && root.dataset.drawer === "open" && sidebar) {
      const items = sidebar.querySelectorAll("a[href], button:not([disabled]), input, select, textarea");
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  window.matchMedia("(min-width: 1024px)").addEventListener("change", (e) => { if (e.matches) closeDrawer(); });

  // --- Toasts: auto-dismiss after 5 s, errors stay until dismissed (spec §6.10) ---
  function dismiss(toast) {
    toast.dataset.leaving = "";
    setTimeout(() => toast.remove(), 200);
  }
  document.querySelectorAll("[data-toast]").forEach((toast) => {
    if (toast.dataset.toast !== "error") setTimeout(() => dismiss(toast), 5000);
  });
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-toast-dismiss]");
    if (btn) dismiss(btn.closest("[data-toast]"));
  });

  // --- Confirm dialog for destructive forms: <form data-confirm="Message"> ---
  const dialog = document.getElementById("confirm-dialog");
  let pendingForm = null;
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (!form.dataset.confirm || form.dataset.confirmed === "1" || !dialog) return;
    e.preventDefault();
    pendingForm = form;
    dialog.querySelector("[data-confirm-message]").textContent = form.dataset.confirm;
    dialog.querySelector("[data-confirm-accept]").textContent = form.dataset.confirmLabel || "Confirm";
    dialog.showModal();
  });
  dialog?.addEventListener("close", () => {
    if (dialog.returnValue === "confirm" && pendingForm) {
      pendingForm.dataset.confirmed = "1";
      pendingForm.requestSubmit();
    }
    pendingForm = null;
  });
})();
