function showError(message) {
  const element = document.getElementById("message");
  element.textContent = message;
  element.hidden = false;
  element.scrollIntoView({block: "nearest"});
}

async function api(url, data) {
  const response = await fetch(url, {
    method: "POST", credentials: "same-origin",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(data || {})
  });
  const body = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(Array.isArray(detail)
      ? detail.map(item => item.loc.join(".") + ": " + item.msg).join("; ")
      : detail || "Не удалось выполнить операцию");
  }
  return body;
}

document.querySelectorAll("form[data-api]").forEach(form => {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const data = Object.fromEntries(new FormData(form));
      if (data.slot_id) data.slot_id = Number(data.slot_id);
      const result = await api(form.dataset.api, data);
      location.href = form.dataset.success === "appointment"
        ? "/appointments/" + result.id : form.dataset.success;
    } catch (error) {
      showError(error.message);
      button.disabled = false;
    }
  });
});

document.querySelectorAll("form[data-filter]").forEach(form => {
  form.addEventListener("submit", event => {
    event.preventDefault();
    const params = new URLSearchParams();
    for (const [name, value] of new FormData(form)) if (value !== "") params.set(name, value);
    location.href = location.pathname + "?" + params.toString();
  });
});

document.querySelector("[data-logout]")?.addEventListener("click", async () => {
  try { await api("/api/auth/logout"); location.href = "/login"; }
  catch (error) { showError(error.message); }
});

document.querySelector("[data-cancel]")?.addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try { await api("/api/appointments/" + button.dataset.cancel + "/cancel"); location.reload(); }
  catch (error) { showError(error.message); button.disabled = false; }
});
