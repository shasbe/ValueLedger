/* ValueLedger admin UI — data entry for the AttributionService. */

const TABS = [
  ["setup", "Setup"], ["initiatives", "Initiatives"], ["task-types", "Task Types"],
  ["activities", "Activities"], ["users", "Users"], ["budgets", "Budgets"],
  ["policy", "Policy"], ["productivity", "Productivity"], ["ledger", "Ledger"],
];

const S = { key: "", email: "", initiatives: [], users: [] };

const $ = (s, r = document) => r.querySelector(s);
const el = (t, a = {}, ...kids) => {
  const n = document.createElement(t);
  for (const [k, v] of Object.entries(a)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c.nodeType ? c : String(c));
  return n;
};
const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined,
  { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let toastTimer;
function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg;
  t.style.background = bad ? "var(--danger)" : "var(--text)";
  t.style.color = bad ? "#fff" : "var(--bg)";
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (S.key) headers["X-API-Key"] = S.key;
  if (S.email) headers["X-User-Email"] = S.email;
  const res = await fetch(path, { ...opts, headers });
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) {
    const msg = (body && body.detail) ? (typeof body.detail === "string"
      ? body.detail : JSON.stringify(body.detail)) : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body;
}

/* ---------------- tabs ---------------- */

function initTabs() {
  const nav = $("#tabs");
  TABS.forEach(([id, label], i) => {
    nav.append(el("button", {
      "data-for": id, class: i === 0 ? "active" : "",
      onclick: () => showTab(id),
    }, label));
  });
}

function showTab(id) {
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.for === id));
  document.querySelectorAll("section[data-tab]").forEach((s) =>
    (s.hidden = s.dataset.tab !== id));
  if (!S.key) return;
  const load = {
    setup: async () => { await loadPolicyStatus(); await loadMcpTokens(); }, initiatives: loadInitiatives, "task-types": () => loadDim("task-types"),
    activities: () => loadDim("activities"), users: loadUsers, budgets: loadBudgets,
    policy: loadPolicy, productivity: loadProductivity, ledger: loadLedger,
  }[id];
  if (load) load().catch((e) => toast(e.message, true));
}

/* ---------------- connection ---------------- */

async function connect() {
  S.key = $("#apiKey").value.trim();
  S.email = $("#userEmail").value.trim();
  if (!S.key) return toast("Enter an API key", true);
  try {
    const org = await api("/v1/org");
    localStorage.setItem("vl_key", S.key);
    localStorage.setItem("vl_email", S.email);
    $("#orgTag").textContent = `${org.name} · ${org.privacy_mode}`;
    $("#connState").innerHTML =
      `<div class="banner ok"><span>●</span><span>Connected to <strong>${esc(org.name)}</strong>
       as <code>${esc(S.email || "(ingest key, admin)")}</code>. Privacy mode
       <code>${esc(org.privacy_mode)}</code> — the service never receives prompt text.</span></div>`;
    toast("Connected");
    const active = document.querySelector("#tabs button.active");
    showTab(active ? active.dataset.for : "setup");
  } catch (e) {
    $("#connState").innerHTML =
      `<div class="banner bad"><span>●</span><span>${esc(e.message)}</span></div>`;
    toast(e.message, true);
  }
}

async function createOrg() {
  const name = prompt("Organization name:");
  if (!name) return;
  try {
    const org = await api("/v1/orgs", { method: "POST", body: JSON.stringify({ name }) });
    $("#newOrgOut").innerHTML =
      `<div class="banner warn" style="margin-top:12px"><span>●</span><span>
       Created <strong>${esc(org.name)}</strong>. API key — save it now, it is not
       recoverable:<br><code>${esc(org.api_key)}</code></span></div>`;
    $("#apiKey").value = org.api_key;
    $("#userEmail").value = `admin@${name.toLowerCase().replace(/ /g, "")}.local`;
    toast("Org created — connect to continue");
  } catch (e) { toast(e.message, true); }
}

/* ---------------- MCP tokens ---------------- */

async function loadMcpTokens() {
  try {
    const users = await api("/v1/users");
    $("#mcpUser").replaceChildren(...users.map((u) =>
      el("option", { value: u.email }, `${u.email} (${u.role})`)));
  } catch {}
  const rows = await api("/v1/mcp-tokens");
  const box = $("#mcpList");
  if (!rows.length) { box.innerHTML = '<div class="hint">No tokens minted yet.</div>'; return; }
  const t = el("table");
  t.append(el("thead", {}, el("tr", {}, ["User", "Label", "Last used", "State", ""]
    .map((h) => el("th", {}, h)))));
  const tb = el("tbody");
  for (const r of rows) {
    tb.append(el("tr", {},
      el("td", { class: "mono" }, r.user_email),
      el("td", {}, r.label || "—"),
      el("td", {}, r.last_used_at ? new Date(r.last_used_at).toLocaleString() : "never"),
      el("td", {}, el("span", { class: "pill " + (r.revoked ? "bad" : "ok") },
        r.revoked ? "revoked" : "active")),
      el("td", {}, r.revoked ? "" : el("button", {
        class: "link", onclick: async () => {
          if (!confirm(`Revoke token for ${r.user_email}?`)) return;
          await api(`/v1/mcp-tokens/${r.id}`, { method: "DELETE" });
          toast("Revoked"); loadMcpTokens();
        },
      }, "revoke")),
    ));
  }
  t.append(tb);
  box.replaceChildren(t);
}

/* ---------------- policy readiness ---------------- */

async function loadPolicyStatus() {
  const st = await api("/v1/policy/status");
  const c = st.counts;
  const banner = st.ready
    ? `<div class="banner ok"><span>●</span><span>Policy v${st.policy_version} is ready to
       classify against. Checksum <code>${esc(st.checksum)}</code>.</span></div>`
    : `<div class="banner warn"><span>●</span><span><strong>Not ready.</strong>
       Classification will produce poor labels until these are fixed.</span></div>`;
  const problems = st.problems.length
    ? `<ul style="margin:10px 0 0;padding-left:20px;font-size:13px;color:var(--text-dim)">
       ${st.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`
    : "";
  $("#policyStatus").innerHTML = banner + `
    <div class="grid g4" style="margin-top:14px">
      ${[["Initiatives", c.initiatives], ["Task types", c.task_types],
         ["Activities", c.activities], ["Examples", c.examples]]
        .map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`)
        .join("")}
    </div>` + problems;
}

/* ---------------- initiatives ---------------- */

function formToObj(form) {
  const o = {};
  new FormData(form).forEach((v, k) => {
    v = String(v).trim();
    o[k] = v === "" ? null : v;
  });
  return o;
}

async function loadInitiatives() {
  S.initiatives = await api("/v1/initiatives");
  const box = $("#initList");
  if (!S.initiatives.length) { box.innerHTML = '<div class="empty">No initiatives yet.</div>'; return; }
  const t = el("table");
  t.append(el("thead", {}, el("tr", {}, ["Key", "Name", "Owner", "Cost center", "Budget", "Guidance", ""]
    .map((h) => el("th", {}, h)))));
  const tb = el("tbody");
  for (const i of S.initiatives) {
    const g = (i.classification_guidance || "").trim();
    tb.append(el("tr", {},
      el("td", {}, el("code", {}, i.key)),
      el("td", {}, i.name),
      el("td", { class: "mono" }, i.owner_email || "—"),
      el("td", {}, i.cost_center || "—"),
      el("td", { class: "num" }, i.budget_amount ? money(i.budget_amount) : "—"),
      el("td", {}, g
        ? el("span", { class: "pill ok", title: g }, `${g.length} chars`)
        : el("span", { class: "pill bad" }, "missing")),
      el("td", {},
        el("button", { class: "link", onclick: () => editInit(i) }, "edit"),
        el("button", { class: "link", onclick: () => delInit(i) }, "delete")),
    ));
  }
  t.append(tb);
  box.replaceChildren(t);
}

function editInit(i) {
  const f = $("#initForm");
  for (const k of ["key", "name", "owner_email", "cost_center", "budget_amount",
                   "budget_period", "classification_guidance"]) {
    if (f.elements[k]) f.elements[k].value = i[k] ?? "";
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
  toast(`Editing ${i.key}`);
}

async function delInit(i) {
  if (!confirm(`Delete initiative "${i.name}"?\n\nSessions already classified against it keep their rows, but the label will no longer resolve.`)) return;
  await api(`/v1/initiatives/${i.id}`, { method: "DELETE" });
  toast("Deleted");
  loadInitiatives();
}

/* ---------------- generic dimensions ---------------- */

const DIM_FIELDS = `
  <div class="grid g2">
    <div><label>Key</label><input name="key" required placeholder="coding"></div>
    <div><label>Name</label><input name="name" required placeholder="Coding"></div>
  </div>
  <div style="margin-top:12px">
    <label>Classification guidance (sent to the classifier)</label>
    <textarea name="classification_guidance" placeholder="Writing, modifying, reviewing or debugging production code, tests, infrastructure-as-code or build configuration."></textarea>
  </div>
  <div class="row" style="margin-top:14px">
    <button class="btn" type="submit">Save</button>
    <button class="btn ghost" type="reset">Clear</button>
  </div>`;

async function loadDim(kind) {
  const rows = await api(`/v1/${kind}`);
  const box = $(kind === "task-types" ? "#ttList" : "#acList");
  if (!rows.length) { box.innerHTML = '<div class="empty">Nothing defined yet.</div>'; return; }
  const t = el("table");
  t.append(el("thead", {}, el("tr", {}, ["Key", "Name", "Guidance", ""].map((h) => el("th", {}, h)))));
  const tb = el("tbody");
  for (const r of rows) {
    const g = (r.classification_guidance || "").trim();
    tb.append(el("tr", {},
      el("td", {}, el("code", {}, r.key)),
      el("td", {}, r.name),
      el("td", {}, g ? el("span", { class: "pill ok", title: g }, `${g.length} chars`)
                     : el("span", { class: "pill bad" }, "missing")),
      el("td", {},
        el("button", {
          class: "link", onclick: () => {
            const f = $(kind === "task-types" ? "#ttForm" : "#acForm");
            f.elements.key.value = r.key;
            f.elements.name.value = r.name;
            f.elements.classification_guidance.value = g;
            window.scrollTo({ top: 0, behavior: "smooth" });
          },
        }, "edit"),
        el("button", {
          class: "link", onclick: async () => {
            if (!confirm(`Delete "${r.name}"?`)) return;
            await api(`/v1/${kind}/${r.id}`, { method: "DELETE" });
            toast("Deleted"); loadDim(kind);
          },
        }, "delete")),
    ));
  }
  t.append(tb);
  box.replaceChildren(t);
}

/* ---------------- users ---------------- */

async function loadUsers() {
  S.users = await api("/v1/users");
  const box = $("#userList");
  if (!S.users.length) { box.innerHTML = '<div class="empty">No users yet.</div>'; return; }
  const counts = S.users.reduce((a, u) => (a[u.role] = (a[u.role] || 0) + 1, a), {});
  const legend = el("div", { class: "row", style: "margin-bottom:12px" },
    ...Object.entries(counts).map(([r, n]) => el("span", { class: "pill" }, `${r}: ${n}`)));
  const t = el("table");
  t.append(el("thead", {}, el("tr", {}, ["Email", "Name", "Role", "Cost center", ""]
    .map((h) => el("th", {}, h)))));
  const tb = el("tbody");
  for (const u of S.users) {
    tb.append(el("tr", {},
      el("td", { class: "mono" }, u.email),
      el("td", {}, u.display_name || "—"),
      el("td", {}, el("span", {
        class: "pill " + (u.role === "admin" || u.role === "finance" ? "warn" : ""),
      }, u.role)),
      el("td", {}, u.cost_center || "—"),
      el("td", {},
        el("button", {
          class: "link", onclick: () => {
            const f = $("#userForm");
            for (const k of ["email", "display_name", "role", "cost_center", "manager_email"]) {
              if (f.elements[k]) f.elements[k].value = u[k] ?? "";
            }
            window.scrollTo({ top: 0, behavior: "smooth" });
          },
        }, "edit"),
        el("button", {
          class: "link", onclick: async () => {
            if (!confirm(`Delete ${u.email}?`)) return;
            await api(`/v1/users/${u.id}`, { method: "DELETE" });
            toast("Deleted"); loadUsers();
          },
        }, "delete")),
    ));
  }
  t.append(tb);
  box.replaceChildren(legend, t);
}

/* ---------------- budgets ---------------- */

async function loadBudgets() {
  if (!S.initiatives.length) { try { S.initiatives = await api("/v1/initiatives"); } catch {} }
  const sel = $("#budgetScopeId");
  sel.replaceChildren(...S.initiatives.map((i) => el("option", { value: i.id }, i.name)));
  const rows = await api("/v1/budgets");
  const byId = Object.fromEntries(S.initiatives.map((i) => [i.id, i.name]));
  const box = $("#budgetList");
  if (!rows.length) { box.innerHTML = '<div class="empty">No budgets set.</div>'; return; }
  const t = el("table");
  t.append(el("thead", {}, el("tr", {}, ["Scope", "Target", "Period", "Amount", ""]
    .map((h) => el("th", {}, h)))));
  const tb = el("tbody");
  for (const b of rows) {
    tb.append(el("tr", {},
      el("td", {}, b.scope_type),
      el("td", {}, byId[b.scope_id] || b.scope_id || "—"),
      el("td", {}, b.period),
      el("td", { class: "num" }, money(b.amount_usd)),
      el("td", {}, el("button", {
        class: "link", onclick: async () => {
          if (!confirm("Delete budget?")) return;
          await api(`/v1/budgets/${b.id}`, { method: "DELETE" });
          toast("Deleted"); loadBudgets();
        },
      }, "delete")),
    ));
  }
  t.append(tb);
  box.replaceChildren(t);
}

/* ---------------- policy ---------------- */

async function loadPolicy() {
  const bundle = await api("/v1/policy");
  $("#globalGuidance").value = bundle.global_guidance || "";
  $("#extractionGuidance").value = bundle.output_extraction_guidance || "";
  $("#guidanceVer").textContent = `Currently at policy v${bundle.policy_version}.`;
  $("#bundlePreview").textContent = JSON.stringify(bundle, null, 2);
}

/* ---------------- productivity ---------------- */

const TRUST_CLASS = {
  customer_measured: "ok", team_survey: "", industry_estimate: "warn",
  vendor_claim: "bad", unset: "bad",
};

async function loadProductivity() {
  // task type options for the baseline form
  try {
    const tts = await api("/v1/task-types");
    $("#blTaskType").replaceChildren(
      ...tts.map((t) => el("option", { value: t.key }, `${t.name} (${t.key})`)));
  } catch {}

  try {
    const org = await api("/v1/org");
    if (org.session_minutes_cap) $("#capInput").value = org.session_minutes_cap;
  } catch {}

  await loadBaselines();
  await loadProdReport();
}

async function loadBaselines() {
  const rows = await api("/v1/baselines");
  const box = $("#baselineList");
  if (!rows.length) {
    box.innerHTML = '<div class="empty">No baselines yet — productivity cannot be estimated without one.</div>';
    return;
  }
  const t = el("table");
  t.append(el("thead", {}, el("tr", {}, ["Task type", "Unit", "Minutes", "Range",
    "Source", "Trust", ""].map((h) => el("th", {}, h)))));
  const tb = el("tbody");
  for (const b of rows) {
    const range = (b.minutes_low != null && b.minutes_high != null)
      ? `${b.minutes_low}–${b.minutes_high}` : "—";
    tb.append(el("tr", {},
      el("td", {}, el("code", {}, b.task_type_key)),
      el("td", {}, b.unit_plural || b.unit),
      el("td", { class: "num" }, b.human_minutes_per_unit),
      el("td", { class: "num" }, range),
      el("td", {}, b.source || el("span", { class: "pill bad" }, "none")),
      el("td", {}, el("span", { class: "pill " + (TRUST_CLASS[b.source_type] ?? "") },
        b.source_type)),
      el("td", {},
        el("button", {
          class: "link", onclick: () => {
            const f = $("#baselineForm");
            for (const k of ["task_type_key", "unit", "unit_plural",
                             "human_minutes_per_unit", "minutes_low", "minutes_high",
                             "source", "source_type"]) {
              if (f.elements[k]) f.elements[k].value = b[k] ?? "";
            }
            window.scrollTo({ top: 0, behavior: "smooth" });
          },
        }, "edit"),
        el("button", {
          class: "link", onclick: async () => {
            if (!confirm(`Delete baseline ${b.task_type_key}/${b.unit}?`)) return;
            await api(`/v1/baselines/${b.id}`, { method: "DELETE" });
            toast("Deleted"); loadProductivity();
          },
        }, "delete")),
    ));
  }
  t.append(tb);
  box.replaceChildren(t);
}

async function loadProdReport() {
  const d = await api("/v1/reports/productivity");
  const m = d.measured, mo = d.modelled, se = d.sensitivity, cv = d.modelled_coverage;
  const box = $("#prodReport");

  if (!mo.speedup_x) {
    box.innerHTML = `<div class="banner warn"><span>●</span><span>No productivity estimate
      available — needs sessions with counted output and a matching baseline.</span></div>`;
    return;
  }

  const crossesOne = se.speedup_low != null && se.speedup_low < 1;
  const headline = `
    <div class="banner ${crossesOne ? "warn" : "ok"}"><span>●</span><span>
      <strong>${mo.speedup_x}×</strong> faster than the human baseline, within a range of
      <strong>${se.speedup_low}× – ${se.speedup_high}×</strong> across your low and high
      estimates.${crossesOne ? " <strong>The low end falls below 1×</strong> — at the "
      + "pessimistic baseline this work was not faster than a human. That is worth "
      + "resolving before quoting the headline." : ""}
    </span></div>`;

  const measured = [
    ["Actual human time", m.actual_human_hours.toLocaleString() + " h",
      `capped at ${m.session_minutes_cap} min/session`],
    ["Units produced", m.units_produced.toLocaleString(),
      `${m.units_measured_pct}% measured, rest classifier-extracted`],
    ["Sessions with output", m.sessions_with_output.toLocaleString(),
      `of ${m.sessions_in_scope.toLocaleString()} in scope`],
    ["Estimate coverage", cv.coverage_pct + "%",
      `${cv.sessions_excluded_reverted} excluded as reverted`],
  ];
  const modelled = [
    ["Human-equivalent time", mo.human_equivalent_hours.toLocaleString() + " h",
      "from your baselines"],
    ["Hours saved", mo.hours_saved.toLocaleString() + " h",
      `range ${se.hours_saved_low.toLocaleString()} – ${se.hours_saved_high.toLocaleString()} h`],
    ["Speedup", mo.speedup_x + "×", `range ${se.speedup_low}× – ${se.speedup_high}×`],
  ];

  const tiles = (arr) => `<div class="grid g4">` + arr.map(([k, v, n]) =>
    `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>
     <div class="n">${esc(n)}</div></div>`).join("") + `</div>`;

  const assumptions = d.assumptions.length ? `
    <table style="margin-top:6px">
      <thead><tr><th>Task type</th><th>Unit</th><th class="num">Min/unit</th>
      <th class="num">Range</th><th class="num">Units counted</th><th>Source</th></tr></thead>
      <tbody>${d.assumptions.map((a) => `<tr>
        <td><code>${esc(a.task_type)}</code></td>
        <td>${esc(a.unit)}</td>
        <td class="num">${a.human_minutes_per_unit}</td>
        <td class="num">${a.range_minutes ? a.range_minutes.join("–") : "—"}</td>
        <td class="num">${a.units_counted.toLocaleString()}
          <span class="hint">${a.units_measured} measured</span></td>
        <td><span class="pill ${TRUST_CLASS[a.source_type] ?? ""}">${esc(a.source_type)}</span>
          <div class="hint">${esc(a.source || "no source recorded")}</div></td>
      </tr>`).join("")}</tbody>
    </table>` : '<div class="empty">No baselines matched any session.</div>';

  box.innerHTML = headline
    + `<h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.04em;
        color:var(--text-dim);margin:18px 0 8px">Measured</h3>` + tiles(measured)
    + `<h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.04em;
        color:var(--text-dim);margin:18px 0 8px">Modelled — depends on the baselines below</h3>`
    + tiles(modelled)
    + `<h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.04em;
        color:var(--text-dim);margin:18px 0 8px">Assumptions in use</h3>` + assumptions
    + `<h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.04em;
        color:var(--text-dim);margin:18px 0 8px">Caveats</h3>`
    + `<ul style="margin:0;padding-left:20px;font-size:12.5px;color:var(--text-dim)">`
    + d.caveats.map((c) => `<li style="margin-bottom:4px">${esc(c)}</li>`).join("")
    + `</ul>`;
}

/* ---------------- ledger ---------------- */

async function loadLedger() {
  let rep = null, scope = "organization";
  try {
    rep = await api("/v1/reports/executive");
  } catch {
    // Not finance/admin — fall back to the self view, which every role can read.
    rep = await api("/v1/reports/me");
    scope = `personal (${rep.user_email})`;
  }
  const s = rep.summary, cov = s.coverage;
  const tiles = [
    ["Attributed spend", money(s.attributed_spend_usd), `${s.sessions} sessions`],
    ["Merged PRs", s.merged_prs, s.cost_per_merged_pr_usd
      ? `${money(s.cost_per_merged_pr_usd)} per merged PR` : "no merges in scope"],
    ["Dark spend", money(s.dark_spend_usd), `${s.dark_spend_pct}% — no durable artifact`],
    ["Rework spend", money(s.rework_spend_usd), `${s.rework_spend_pct}% — later reverted`],
    ["Classification coverage", cov.classification_pct + "%",
      `${cov.unclassified_sessions} unclassified`],
    ["Cost coverage", cov.cost_pct + "%", "rest is chat — classified, not priced"],
    ["Classification overhead", money(s.classification_overhead_usd),
      `${s.classification_overhead_pct}% of tracked spend`],
    ["Scope", scope, "server-side role scoping"],
  ];
  $("#ledgerSummary").innerHTML = `<div class="grid g4">` + tiles.map(([k, v, n]) =>
    `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>
     <div class="n">${esc(n)}</div></div>`).join("") + `</div>`;

  const rows = rep.by_initiative || [];
  const box = $("#ledgerByInit");
  if (!rows.length) { box.innerHTML = '<div class="empty">No data.</div>'; return; }
  const max = Math.max(...rows.map((r) => r.spend_usd), 1);
  const t = el("table");
  t.append(el("thead", {}, el("tr", {}, ["Initiative", "Spend", "", "Sessions", "Merged PRs",
    "$/merged PR", "Dark spend"].map((h) => el("th", {}, h)))));
  const tb = el("tbody");
  for (const r of rows) {
    tb.append(el("tr", {},
      el("td", {}, r.label === "Unclassified"
        ? el("span", { class: "pill warn" }, "Unclassified") : r.label),
      el("td", { class: "num" }, money(r.spend_usd)),
      el("td", {}, el("div", { class: "bar" },
        el("i", { style: `width:${(100 * r.spend_usd / max).toFixed(1)}%` }))),
      el("td", { class: "num" }, r.sessions),
      el("td", { class: "num" }, r.merged_prs),
      el("td", { class: "num" }, r.cost_per_merged_pr_usd ? money(r.cost_per_merged_pr_usd) : "—"),
      el("td", { class: "num" }, money(r.dark_spend_usd)),
    ));
  }
  t.append(tb);
  box.replaceChildren(t);

  const pb = await api("/v1/price-book");
  const pt = el("table");
  pt.append(el("thead", {}, el("tr", {}, ["Model", "Input /MTok", "Output /MTok",
    "Cache read", "Cache write 5m", "Cache write 1h"].map((h) => el("th", {}, h)))));
  const ptb = el("tbody");
  for (const p of pb) {
    ptb.append(el("tr", {},
      el("td", { class: "mono" }, p.model),
      el("td", { class: "num" }, money(p.input_per_mtok)),
      el("td", { class: "num" }, money(p.output_per_mtok)),
      el("td", { class: "num" }, money(p.cache_read_per_mtok)),
      el("td", { class: "num" }, money(p.cache_write_5m_per_mtok)),
      el("td", { class: "num" }, money(p.cache_write_1h_per_mtok)),
    ));
  }
  pt.append(ptb);
  $("#priceBook").replaceChildren(pt);
}

/* ---------------- wiring ---------------- */

function bindForm(sel, path, after, transform) {
  $(sel).addEventListener("submit", async (e) => {
    e.preventDefault();
    let body = formToObj(e.target);
    if (transform) body = transform(body);
    try {
      await api(path, { method: "POST", body: JSON.stringify(body) });
      toast("Saved");
      e.target.reset();
      if (after) after();
    } catch (err) { toast(err.message, true); }
  });
}

function boot() {
  initTabs();
  $("#ttForm").innerHTML = DIM_FIELDS;
  $("#acForm").innerHTML = DIM_FIELDS;

  $("#connectBtn").addEventListener("click", connect);
  $("#newOrgBtn").addEventListener("click", createOrg);

  $("#mintMcp").addEventListener("click", async () => {
    const email = $("#mcpUser").value;
    const label = $("#mcpLabel").value.trim();
    if (!email) return toast("Pick a user", true);
    try {
      const r = await api(`/v1/mcp-tokens?user_email=${encodeURIComponent(email)}`
        + (label ? `&label=${encodeURIComponent(label)}` : ""), { method: "POST" });
      $("#mcpOut").innerHTML = `<div class="banner warn" style="margin-top:12px">
        <span>●</span><span>Token for <strong>${esc(r.user_email)}</strong> — save it now,
        only its hash is stored:<br><code>${esc(r.token)}</code></span></div>`;
      $("#mcpLabel").value = "";
      toast("Token minted");
      loadMcpTokens();
    } catch (e) { toast(e.message, true); }
  });
  $("#userEmail").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
  $("#apiKey").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });

  bindForm("#initForm", "/v1/initiatives", loadInitiatives, (b) => ({
    ...b,
    description: b.classification_guidance || "",
    budget_amount: b.budget_amount ? Number(b.budget_amount) : null,
    status: "active",
  }));
  bindForm("#ttForm", "/v1/task-types", () => loadDim("task-types"),
    (b) => ({ ...b, description: b.classification_guidance || "" }));
  bindForm("#acForm", "/v1/activities", () => loadDim("activities"),
    (b) => ({ ...b, description: b.classification_guidance || "" }));
  bindForm("#userForm", "/v1/users", loadUsers);
  bindForm("#budgetForm", "/v1/budgets", loadBudgets,
    (b) => ({ ...b, amount_usd: Number(b.amount_usd) }));

  bindForm("#baselineForm", "/v1/baselines", loadProductivity, (b) => ({
    ...b,
    human_minutes_per_unit: Number(b.human_minutes_per_unit),
    minutes_low: b.minutes_low ? Number(b.minutes_low) : null,
    minutes_high: b.minutes_high ? Number(b.minutes_high) : null,
    source: b.source || "",
    active: true,
  }));

  $("#saveCap").addEventListener("click", async () => {
    const v = Number($("#capInput").value);
    try {
      await api(`/v1/org/settings?session_minutes_cap=${v}`, { method: "PUT" });
      toast("Cap updated"); loadProdReport();
    } catch (e) { toast(e.message, true); }
  });

  $("#saveExtraction").addEventListener("click", async () => {
    try {
      await api("/v1/policy", {
        method: "PUT",
        body: JSON.stringify({ output_extraction_guidance: $("#extractionGuidance").value }),
      });
      toast("Extraction guidance republished"); loadPolicy();
    } catch (e) { toast(e.message, true); }
  });

  $("#saveGuidance").addEventListener("click", async () => {
    try {
      await api("/v1/policy", {
        method: "PUT",
        body: JSON.stringify({ global_guidance: $("#globalGuidance").value }),
      });
      toast("Policy republished at a new version");
      loadPolicy();
    } catch (e) { toast(e.message, true); }
  });

  // The landing page hands credentials over in the hash so a reviewer lands
  // straight in the right role without copying anything.
  const h = new URLSearchParams(location.hash.slice(1));
  const hashKey = h.get("key"), hashEmail = h.get("email");
  if (hashKey) {
    history.replaceState(null, "", location.pathname);   // don't leave it in the URL
    $("#apiKey").value = hashKey;
    $("#userEmail").value = hashEmail || "";
    connect();
    return;
  }
  const k = localStorage.getItem("vl_key");
  const em = localStorage.getItem("vl_email");
  if (k) { $("#apiKey").value = k; $("#userEmail").value = em || ""; connect(); }
}

document.addEventListener("DOMContentLoaded", boot);
