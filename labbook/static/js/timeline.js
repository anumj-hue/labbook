// Drag-on-timeline booking picker (spec §6.7).
// Selection state is a slot range [a, b) shared by the desktop (x) and mobile (y) tracks and kept in
// sync with the Start / End fields, which stay the accessible fallback. Every change is checked by
// POST /bookings/validate, which runs the same rules as submit.
(() => {
  const form = document.querySelector("[data-booking-form]");
  if (!form) return;

  const startField = form.querySelector('[data-field="start"]');
  const endField = form.querySelector('[data-field="end"]');
  const dateField = form.querySelector('[data-field="date"]');
  const unitField = form.querySelector('[data-field="unit"]');
  const button = form.querySelector("[data-book-button]");
  const messages = form.querySelector("[data-booking-messages]");
  const hint = form.querySelector("[data-booking-hint]");
  const overrideBox = form.querySelector("[data-override]");
  const summary = (key) => form.querySelector(`[data-summary="${key}"]`);

  const timeline = form.querySelector("[data-timeline]");
  const slots = timeline ? Number(timeline.dataset.slots) : 0;
  const startMinute = timeline ? Number(timeline.dataset.startMinute) : 0;
  const slotMinutes = timeline ? Number(timeline.dataset.slotMinutes) : 15;
  const maxSlots = timeline ? Math.max(1, Number(timeline.dataset.maxSlots)) : 4;
  const tracks = timeline ? [...timeline.querySelectorAll("[data-track]")] : [];

  let sel = null; // {a, b} slot indices, b exclusive
  let invalid = false;

  const pad = (n) => String(n).padStart(2, "0");
  const toTime = (minute) => `${pad(Math.floor(minute / 60))}:${pad(minute % 60)}`;
  const toMinute = (value) => {
    if (!value) return null;
    const [h, m] = value.split(":").map(Number);
    return h * 60 + m;
  };
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  function fromFields() {
    const s = toMinute(startField.value);
    const e = toMinute(endField.value);
    if (s === null || e === null || e <= s) sel = null;
    else sel = { a: (s - startMinute) / slotMinutes, b: (e - startMinute) / slotMinutes };
    render();
  }

  function toFields() {
    if (!sel) return;
    startField.value = toTime(startMinute + sel.a * slotMinutes);
    endField.value = toTime(startMinute + sel.b * slotMinutes);
  }

  function render() {
    for (const track of tracks) {
      const el = track.querySelector("[data-selection]");
      const visible = sel && sel.b > 0 && sel.a < slots;
      el.hidden = !visible;
      if (!visible) continue;
      const a = clamp(sel.a, 0, slots);
      const b = clamp(sel.b, 0, slots);
      const [pos, size] = track.dataset.axis === "x" ? ["left", "width"] : ["top", "height"];
      el.style[pos] = `${(a / slots) * 100}%`;
      el.style[size] = `${((b - a) / slots) * 100}%`;
      el.toggleAttribute("data-invalid", invalid);
      const minutes = (sel.b - sel.a) * slotMinutes;
      const length = minutes % 60 ? (minutes > 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${minutes} min`) : `${minutes / 60} h`;
      el.querySelector("[data-selection-label]").textContent =
        `${toTime(startMinute + sel.a * slotMinutes)}–${toTime(startMinute + sel.b * slotMinutes)} · ${length}`;
    }
  }

  function slotAt(track, event) {
    const rect = track.getBoundingClientRect();
    const fraction = track.dataset.axis === "x"
      ? (event.clientX - rect.left) / rect.width
      : (event.clientY - rect.top) / rect.height;
    return clamp(Math.floor(fraction * slots), 0, slots - 1);
  }

  // --- Pointer: mouse drag creates; touch taps create; handles resize ---
  for (const track of tracks) {
    let mode = null; // create | start | end
    let anchor = 0;
    let touch = null;

    track.addEventListener("pointerdown", (event) => {
      const handle = event.target.closest("[data-handle]");
      if (handle && sel) {
        mode = handle.dataset.handle;
      } else if (event.pointerType === "mouse" && event.button === 0) {
        mode = "create";
        anchor = slotAt(track, event);
        sel = { a: anchor, b: anchor + 1 };
        render();
      } else {
        touch = { x: event.clientX, y: event.clientY, slot: slotAt(track, event) };
        return; // let touch scroll; a tap is handled on pointerup
      }
      event.preventDefault();
      track.setPointerCapture(event.pointerId);
    });

    track.addEventListener("pointermove", (event) => {
      if (!mode) return;
      const i = slotAt(track, event);
      if (mode === "create") sel = { a: Math.min(anchor, i), b: Math.max(anchor, i) + 1 };
      else if (mode === "start") sel = { a: clamp(i, 0, sel.b - 1), b: sel.b };
      else sel = { a: sel.a, b: clamp(i + 1, sel.a + 1, slots) };
      render();
    });

    const finish = (event) => {
      if (mode) {
        mode = null;
        toFields();
        validate();
      } else if (touch && event.type === "pointerup") {
        const moved = Math.hypot(event.clientX - touch.x, event.clientY - touch.y);
        if (moved < 10) {
          const length = Math.min(60 / slotMinutes, maxSlots);
          const a = clamp(touch.slot, 0, slots - 1);
          sel = { a, b: Math.min(a + length, slots) };
          render();
          toFields();
          validate();
        }
      }
      touch = null;
    };
    track.addEventListener("pointerup", finish);
    track.addEventListener("pointercancel", finish);

    // --- Keyboard ---
    track.addEventListener("keydown", (event) => {
      const forward = ["ArrowRight", "ArrowDown"].includes(event.key);
      const back = ["ArrowLeft", "ArrowUp"].includes(event.key);
      if (event.key === "Enter") {
        event.preventDefault();
        if (!button.disabled) form.requestSubmit();
        return;
      }
      if (!forward && !back) return;
      event.preventDefault();
      if (!sel) sel = { a: 0, b: Math.min(60 / slotMinutes, maxSlots, slots) };
      else if (event.shiftKey) sel = { a: sel.a, b: clamp(sel.b + (forward ? 1 : -1), sel.a + 1, slots) };
      else {
        const shift = forward ? Math.min(1, slots - sel.b) : -Math.min(1, sel.a);
        sel = { a: sel.a + shift, b: sel.b + shift };
      }
      render();
      toFields();
      validate();
    });
  }

  // --- Fields ---
  for (const field of [startField, endField, unitField]) {
    field?.addEventListener("change", () => { fromFields(); validate(); });
  }
  form.querySelector("textarea")?.addEventListener("input", () => validate());
  dateField?.addEventListener("change", () => {
    if (!dateField.value) return;
    const target = new URL(form.dataset.navUrl.replace("__DATE__", dateField.value), window.location.origin);
    target.searchParams.set("start", startField.value);
    target.searchParams.set("end", endField.value);
    if (unitField?.value && unitField.value !== "0") target.searchParams.set("unit", unitField.value);
    window.location.assign(target);
  });

  // --- Live validation ---
  let timer = null;
  let requestId = 0;
  function validate() {
    clearTimeout(timer);
    timer = setTimeout(run, 200);
  }

  function showMessages(items) {
    messages.replaceChildren(...items.map((v) => {
      const li = document.createElement("li");
      li.className = "flex items-start gap-1.5";
      li.textContent = v.message;
      return li;
    }));
  }

  async function run() {
    const id = ++requestId;
    let data;
    try {
      const response = await fetch(form.dataset.validateUrl, {
        method: "POST", body: new FormData(form), headers: { "X-Requested-With": "fetch" },
      });
      data = await response.json();
    } catch {
      return; // network hiccup: submit still validates on the server
    }
    if (id !== requestId) return;
    summary("unit").textContent = data.summary.unit;
    summary("when").textContent = data.summary.when || "—";
    summary("duration").textContent = data.summary.duration || "—";
    hint.hidden = !data.incomplete;
    button.disabled = !data.ok;
    invalid = !data.ok && !data.incomplete;
    showMessages(data.violations);
    if (overrideBox) {
      overrideBox.hidden = !data.override;
      if (data.override) {
        const o = data.override;
        overrideBox.querySelector("[data-override-text]").textContent =
          `${o.unit} is booked by ${o.student} (${o.when}, made ${o.made}). Overriding cancels it and tells them why.`;
      }
    }
    render();
  }

  // Start disabled until the server agrees (without JS the button stays enabled and submit validates).
  button.disabled = true;
  fromFields();
  run();
})();
