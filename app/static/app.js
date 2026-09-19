// Amount stepper: big buttons instead of typing.
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-add],[data-reset]");
  if (b) {
    const box = b.closest("[data-stepper]");
    const value = box.querySelector("[data-value]");
    const cents = b.hasAttribute("data-reset") ? 0 : +value.value + +b.dataset.add;
    value.value = cents;
    box.querySelector("[data-display]").value = (cents / 100).toLocaleString(document.documentElement.lang, {
      style: "currency",
      currency: "EUR",
      useGrouping: false,
    });
    value.dispatchEvent(new Event("change", { bubbles: true }));
    return;
  }

  // PIN keypad: fills 4 dots, submits by itself.
  const k = e.target.closest("[data-key]");
  if (k) {
    const form = k.closest("form");
    const pin = form.querySelector("[name=pin]");
    pin.value = k.dataset.key === "back" ? pin.value.slice(0, -1) : (pin.value + k.dataset.key).slice(0, 4);
    form.querySelectorAll("[data-dot]").forEach((d, i) => d.classList.toggle("bg-pink-500", i < pin.value.length));
    if (pin.value.length === 4) form.submit();
  }
});

// Typing an amount works too: keep the hidden cents field in sync.
document.addEventListener("input", (e) => {
  const d = e.target.closest("[data-display]");
  if (!d) return;
  const value = d.closest("[data-stepper]").querySelector("[data-value]");
  value.value = Math.round(parseFloat(d.value.replace(/[^\d.,]/g, "").replace(",", ".")) * 100) || 0;
  value.dispatchEvent(new Event("change", { bubbles: true }));
});
document.addEventListener("focusin", (e) => e.target.matches("[data-display]") && e.target.select());
