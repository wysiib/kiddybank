// Amount stepper: big buttons instead of typing.
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-add],[data-reset]");
  if (b) {
    const box = b.closest("[data-stepper]");
    const value = box.querySelector("[data-value]");
    const cents = b.hasAttribute("data-reset") ? 0 : +value.value + +b.dataset.add;
    value.value = cents;
    box.querySelector("[data-display]").textContent = (cents / 100).toLocaleString(document.documentElement.lang, {
      style: "currency",
      currency: "EUR",
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
