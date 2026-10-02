const $ = (id) => document.getElementById(id);

let role = null;
let client = null;
let adminTable = "clients";
let searchTimer = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let body = null;
  if (text) {
    try { body = JSON.parse(text); } catch { body = { detail: text }; }
  }
  if (!response.ok) {
    const detail = body && body.detail ? body.detail : "Request failed.";
    throw new Error(typeof detail === "string" ? detail : "Request failed.");
  }
  return body;
}

function cell(text) {
  const td = document.createElement("td");
  td.textContent = text == null ? "" : String(text);
  return td;
}

function flagCell(flag) {
  const td = document.createElement("td");
  const span = document.createElement("span");
  span.className = `flag flag-${flag || "normal"}`;
  span.textContent = flag || "";
  td.appendChild(span);
  return td;
}

function addMessage(kind, text, citations) {
  const bubble = document.createElement("article");
  bubble.className = `bubble ${kind}`;
  const body = document.createElement("p");
  body.textContent = text;
  bubble.appendChild(body);
  if (citations && citations.length) {
    const list = document.createElement("ul");
    list.className = "citations";
    citations.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = `${item.label}: ${item.snippet}`;
      list.appendChild(li);
    });
    bubble.appendChild(list);
  }
  $("transcript").appendChild(bubble);
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

function showDesk() {
  $("login").hidden = true;
  $("desk").hidden = false;
  $("who").hidden = false;
  $("who").replaceChildren();
  const label = document.createElement("span");
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Sign out";
  button.addEventListener("click", signOut);
  if (role === "admin") {
    label.textContent = "Staff desk";
    $("chat-scope").textContent = "Answers can draw on every chart.";
    $("admin-records").hidden = false;
    $("customer-records").hidden = true;
    $("ask").question.placeholder = "Which patients have a high HbA1c?";
    loadAdmin();
  } else {
    label.textContent = `${client.full_name} · ${client.mrn}`;
    $("chat-scope").textContent = "Answers stay inside this chart.";
    $("customer-records").hidden = false;
    $("admin-records").hidden = true;
    $("ask").question.placeholder = "What was my latest HbA1c?";
    loadChart();
  }
  $("who").append(label, button);
}

async function signOut() {
  const path = role === "admin" ? "/admin/v1/logout" : "/customer/v1/logout";
  await api(path, { method: "POST" });
  role = null;
  client = null;
  $("transcript").replaceChildren();
  $("desk").hidden = true;
  $("who").hidden = true;
  $("login").hidden = false;
}

async function boot() {
  try {
    const health = await api("/health");
    if (!health.clients) {
      const banner = $("banner");
      banner.hidden = false;
      banner.textContent = "The chart database is empty. From the project folder run: python -m app.seed";
    }
  } catch {
    /* The page still offers sign-in. */
  }
  try {
    const me = await api("/admin/v1/me");
    role = me.role;
    showDesk();
    return;
  } catch { /* not staff */ }
  try {
    const me = await api("/customer/v1/me");
    role = me.role;
    client = me.client;
    showDesk();
  } catch { /* show login */ }
}

function switchLogin(panel) {
  $("tab-customer").classList.toggle("active", panel === "customer");
  $("tab-admin").classList.toggle("active", panel === "admin");
  $("customer-login").hidden = panel !== "customer";
  $("admin-login").hidden = panel !== "admin";
  $("login-error").textContent = "";
}

$("tab-customer").addEventListener("click", () => switchLogin("customer"));
$("tab-admin").addEventListener("click", () => switchLogin("admin"));

$("customer-login").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  const data = new FormData(event.target);
  try {
    const result = await api("/customer/v1/login", {
      method: "POST",
      body: JSON.stringify({ mrn: data.get("mrn"), password: data.get("password") }),
    });
    role = "customer";
    client = result.client;
    showDesk();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});

$("admin-login").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  const data = new FormData(event.target);
  try {
    await api("/admin/v1/login", {
      method: "POST",
      body: JSON.stringify({ password: data.get("password") }),
    });
    role = "admin";
    showDesk();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});

$("ask").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = new FormData(event.target).get("question").trim();
  if (!question) return;
  addMessage("user", question);
  event.target.reset();
  const button = $("ask-button");
  button.disabled = true;
  button.textContent = "Reading the chart…";
  const path = role === "admin" ? "/admin/v1/query" : "/customer/v1/query";
  try {
    const result = await api(path, {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    addMessage(result.refused ? "refused" : "answer", result.answer, result.citations);
  } catch (error) {
    addMessage("refused", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Ask";
  }
});

function fillBody(tbody, rows, builder) {
  tbody.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.appendChild(cell("No rows"));
    tbody.appendChild(tr);
    return;
  }
  rows.forEach((row) => tbody.appendChild(builder(row)));
}

async function loadChart() {
  const chart = await api("/customer/v1/chart");
  fillBody($("own-visits"), chart.appointments, (row) => {
    const tr = document.createElement("tr");
    tr.append(cell(row.appointment_date), cell(row.reason), cell(row.diagnosis));
    return tr;
  });
  fillBody($("own-labs"), chart.bloodwork, (row) => {
    const tr = document.createElement("tr");
    tr.append(cell(row.collected_at), cell(row.analyte), cell(`${row.value} ${row.unit}`), flagCell(row.flag));
    return tr;
  });
}

function renderAdminTable(rows) {
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const tbody = document.createElement("tbody");
  const headers = {
    clients: ["MRN", "Name", "Born", "Phone"],
    appointments: ["Date", "Client", "Reason", "Diagnosis"],
    bloodwork: ["Collected", "Client", "Analyte", "Value", "Flag"],
  };
  const headRow = document.createElement("tr");
  headers[adminTable].forEach((name) => {
    const th = document.createElement("th");
    th.textContent = name;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    if (adminTable === "clients") {
      tr.append(cell(row.mrn), cell(row.full_name), cell(row.date_of_birth), cell(row.phone));
    } else if (adminTable === "appointments") {
      tr.append(cell(row.appointment_date), cell(`${row.full_name} · ${row.mrn}`), cell(row.reason), cell(row.diagnosis));
    } else {
      tr.append(cell(row.collected_at), cell(`${row.full_name} · ${row.mrn}`), cell(row.analyte), cell(`${row.value} ${row.unit}`), flagCell(row.flag));
    }
    tbody.appendChild(tr);
  });
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.appendChild(cell("No rows"));
    tbody.appendChild(tr);
  }
  table.append(thead, tbody);
  $("admin-table").replaceChildren(table);
}

async function loadAdmin() {
  const q = $("search").value.trim();
  const query = q ? `?q=${encodeURIComponent(q)}` : "";
  const rows = await api(`/admin/v1/${adminTable}${query}`);
  renderAdminTable(rows);
  const clients = adminTable === "clients" && !q ? rows : await api("/admin/v1/clients");
  fillClientSelects(clients);
}

function fillClientSelects(rows) {
  ["appointment_client", "lab_client"].forEach((name) => {
    const select = document.querySelector(`[name="${name}"]`);
    select.replaceChildren();
    rows.forEach((row) => {
      const option = document.createElement("option");
      option.value = row.id;
      option.textContent = `${row.mrn} · ${row.full_name}`;
      select.appendChild(option);
    });
  });
}

document.querySelectorAll("#admin-records .tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("#admin-records .tab").forEach((tab) => tab.classList.remove("active"));
    button.classList.add("active");
    adminTable = button.dataset.table;
    loadAdmin().catch((error) => { $("save-note").textContent = error.message; });
  });
});

$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    loadAdmin().catch((error) => { $("save-note").textContent = error.message; });
  }, 200);
});

function showKind(kind) {
  document.querySelectorAll("[data-kind]").forEach((block) => {
    block.hidden = block.dataset.kind !== kind;
  });
}

document.querySelector("[name=kind]").addEventListener("change", (event) => {
  showKind(event.target.value);
});

$("add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(event.target);
  const kind = data.get("kind");
  const button = $("save-button");
  button.disabled = true;
  button.textContent = "Saving and indexing…";
  $("save-note").textContent = "";
  let path = "/admin/v1/clients";
  let payload = {
    full_name: data.get("full_name"),
    date_of_birth: data.get("date_of_birth"),
    email: data.get("email"),
    phone: data.get("phone"),
    address: data.get("address"),
  };
  if (kind === "appointment") {
    path = "/admin/v1/appointments";
    payload = {
      client_id: Number(data.get("appointment_client")),
      appointment_date: data.get("appointment_date"),
      clinician_name: data.get("clinician_name"),
      reason: data.get("reason"),
      note: data.get("note"),
      diagnosis: data.get("diagnosis"),
    };
  } else if (kind === "bloodwork") {
    path = "/admin/v1/bloodwork";
    payload = {
      client_id: Number(data.get("lab_client")),
      collected_at: data.get("collected_at"),
      panel_name: data.get("panel_name"),
      analyte: data.get("analyte"),
      value: Number(data.get("value")),
      unit: data.get("unit"),
      ref_low: data.get("ref_low") === "" ? null : Number(data.get("ref_low")),
      ref_high: data.get("ref_high") === "" ? null : Number(data.get("ref_high")),
    };
  }
  try {
    const created = await api(path, { method: "POST", body: JSON.stringify(payload) });
    if (created.mrn) {
      $("save-note").textContent = `Saved ${created.mrn}. Password is ${created.password}.`;
    } else {
      $("save-note").textContent = "Saved and indexed.";
    }
    event.target.reset();
    showKind("client");
    await loadAdmin();
  } catch (error) {
    $("save-note").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Save entry";
  }
});

boot();
