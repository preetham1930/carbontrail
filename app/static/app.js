"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const eur = (v) => v == null ? "–" : new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(v);
const num = (v, d = 2) => v == null ? "–" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const AGENTS = {
  document_intake_agent: ["Document intake", "#23727A"],
  scope_quality_agent: ["Scope & quality", "#A63D40"],
  calculation_agent: ["Calculation", "#1B2629"],
  advisor_agent: ["Advisor", "#1F6F54"],
  supplier_data_agent: ["Supplier data", "#A7761E"],
  regulation_agent: ["Regulation", "#5B6B6E"],
  cbam_analysis_pipeline: ["Pipeline", "#5B6B6E"],
};
const DOC_TYPES = {
  electricity_bill: "Electricity bill", fuel_invoice: "Fuel invoice", precursor_purchase_invoice: "Wire rod purchases",
  production_register: "Production register", export_invoice: "Export invoice", supplier_emissions_declaration: "Supplier emissions data", other: "Other",
};

const S = { runId: null, run: null, es: null, seen: 0, docs: {}, scenTimer: null, scen: null };

/* ---------- start ---------- */
$$("[data-run-demo]").forEach((b) => b.addEventListener("click", () => start({ useSamples: true })));
$("#file-input").addEventListener("change", (e) => e.target.files.length && start({ files: e.target.files }));
$("#add-input").addEventListener("change", (e) => e.target.files.length && addDocs({ files: e.target.files }));
// Phone menu
{
  const btn = $("#menu-btn"), sheet = $("#menu");
  const set = (open) => { sheet.hidden = !open; btn.setAttribute("aria-expanded", String(open)); btn.classList.toggle("open", open); };
  btn.onclick = () => set(sheet.hidden);
  sheet.addEventListener("click", (e) => { if (e.target.closest("a, button")) set(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") set(false); });
  addEventListener("resize", () => innerWidth > 880 && set(false));
}
// Upload controls are real buttons (keyboard and screen readers) that open the hidden file inputs.
$$("[data-pick]").forEach((b) => (b.onclick = () => $("#" + b.dataset.pick).click()));

async function start({ useSamples = false, files = null }) {
  const btns = $$("[data-run-demo]");
  btns.forEach((b) => (b.disabled = true));
  const fd = new FormData();
  fd.append("profile", "{}");
  fd.append("use_samples", useSamples ? "true" : "false");
  if (files) [...files].forEach((f) => fd.append("files", f));
  try {
    const r = await fetch("/api/runs", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const data = await r.json();
    S.runId = data.id;
    S.seen = 0;
    $("#landing").hidden = true;
    $("#workspace").hidden = false;
    document.body.classList.replace("mode-landing", "mode-work");
    document.dispatchEvent(new Event("ct:workspace"));
    window.scrollTo({ top: 0 });
    setPhase(0);
    data.documents.forEach(upsertDoc);
    setStatus("Reading your documents", true);
    listen();
  } catch (err) {
    alert("Could not start: " + err.message);
  } finally { btns.forEach((b) => (b.disabled = false)); }
}

async function addDocs({ files = null, sample = "" }) {
  const fd = new FormData();
  if (files) [...files].forEach((f) => fd.append("files", f));
  if (sample) fd.append("sample", sample);
  const r = await fetch(`/api/runs/${S.runId}/documents`, { method: "POST", body: fd });
  if (!r.ok) { alert((await r.json()).detail || "Upload failed"); return false; }
  const data = await r.json();
  data.documents.forEach(upsertDoc);
  markBriefsStale();
  $("#live").hidden = false;
  setPhase(0);
  setStatus("Reading the new document and updating the figures", true);
  listen();
  return true;
}

/* ---------- streaming ---------- */
function listen() {
  if (S.es) S.es.close();
  const es = new EventSource(`/api/runs/${S.runId}/stream?since=${S.seen}`);
  S.es = es;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    S.seen++;
    onEvent(ev);
    if (ev.type === "done" || ev.type === "error") { es.close(); S.es = null; }
  };
  // A dropped connection (flaky network): reopen from the last event received, so nothing is missed or repeated.
  es.onerror = () => {
    if (S.es !== es) return;
    es.close();
    clearTimeout(S.retry);
    S.retry = setTimeout(() => { if (S.es === es) listen(); }, 1500);
  };
}

function onEvent(ev) {
  const li = traceItem(ev);
  if (li) $("#trace").append(li);
  if (ev.type === "doc_start") upsertDoc({ id: ev.doc, name: ev.name, state: "reading" });
  if (ev.type === "doc_done") upsertDoc({ id: ev.doc, name: ev.name, source: ev.source, doc_type: ev.doc_type, items: ev.items.length, error: ev.error, set_aside: ev.set_aside, set_aside_reason: ev.set_aside_reason });
  if (ev.type === "stage") setStatus(ev.text, true);
  if (ev.type === "result") { refresh(false); setPhase(2); }
  if (ev.type === "tool_call" && ev.agent === "advisor_agent") setPhase(3);
  if (ev.type === "done") { setStatus(""); refresh(true); setPhase(4); }
  if (ev.type === "error") showStop(ev.text);
}

function traceItem(ev) {
  const li = document.createElement("li");
  li.className = "tr";
  const who = (a) => { const [n, c] = AGENTS[a] || [a || "CarbonTrail", "#5B6B6E"]; return `<div class="who" style="--agent:${c}">${esc(n)}</div>`; };
  switch (ev.type) {
    case "stage": li.className = "tr stage"; li.textContent = ev.text; return li;
    case "doc_start": return null;
    case "doc_done": {
      const head = ev.set_aside ? `Set aside <b>${esc(ev.name)}</b>: ${esc(ev.set_aside_reason || "not an invoice, bill, statement or register")}. No figures taken from it.`
        : ev.source === "error" ? `Could not read <b>${esc(ev.name)}</b>: ${esc(ev.error)}`
        : `Read <b>${esc(ev.name)}</b> as ${esc(DOC_TYPES[ev.doc_type] || ev.doc_type)}${ev.issuer ? " from " + esc(ev.issuer) : ""}: ${ev.items.length} figure${ev.items.length === 1 ? "" : "s"}`
          + (ev.languages?.length > 1 ? ` · ${esc(ev.languages.join(" + "))}` : "") + (ev.source === "replayed" ? ` <span class="sev medium">replayed</span>` : "");
      const items = ev.items.slice(0, 8).map((i) => `<li>${esc(i.description)}: <b>${esc(i.quantity ?? "–")} ${esc(i.unit ?? "")}</b>${i.confidence < 0.75 ? ` <span class="sev medium">check</span>` : ""}</li>`).join("");
      const notes = (ev.notes || []).map((n) => `<li>Note: ${esc(n)}</li>`).join("");
      li.innerHTML = who(ev.agent) + `<div class="what">${head}<ul class="items">${items}${ev.items.length > 8 ? `<li>…and ${ev.items.length - 8} more</li>` : ""}${notes}</ul></div>`;
      return li;
    }
    case "note": li.innerHTML = who("") + `<div class="what">${esc(ev.text)}</div>`; return li;
    case "result": li.innerHTML = who("calculation_agent") + `<div class="what">Exact calculation done: SEE <b>${num(ev.see, 3)} tCO2e/t</b>. The agents now review it.</div>`; return li;
    case "tool_call": {
      const raw = `${ev.tool}(${ev.args && Object.keys(ev.args).length ? JSON.stringify(ev.args) : ""})`;
      li.innerHTML = who(ev.agent) + `<div class="what">${toolSentence(ev.tool, ev.args || {})}<details class="raw"><summary>Technical detail</summary><code>${esc(raw)}</code></details></div>`;
      return li;
    }
    case "tool_result": li.innerHTML = who(ev.agent) + `<div class="what"><details><summary>Result of ${esc(ev.tool)}</summary><pre>${esc(typeof ev.result === "string" ? ev.result : JSON.stringify(ev.result, null, 1))}</pre></details></div>`; return li;
    case "agent_message": li.innerHTML = who(ev.agent) + `<div class="what msg">${md(ev.text)}</div>`; return li;
    case "warning": li.innerHTML = who("") + `<div class="what"><span class="sev medium">note</span> ${esc(ev.text)}</div>`; return li;
    case "error": li.innerHTML = who("") + `<div class="what"><span class="sev high">error</span> ${esc(ev.text)}</div>`; return li;
    case "done": li.className = "tr stage"; li.textContent = "Done."; return li;
  }
  return null;
}

/* Plain-English line for each agent tool call (the raw call stays one click away). */
function toolSentence(tool, a) {
  const sev = (x) => `<span class="sev ${esc(x)}">${esc(x)}</span>`;
  switch (tool) {
    case "get_activity_data": return "Reads the figures taken from your documents.";
    case "check_cbam_scope": return `Checks which products CBAM covers${a.cn_codes ? ` (CN ${esc([].concat(a.cn_codes).join(", "))})` : ""}.`;
    case "run_data_quality_checks": return "Runs the data quality checks a verifier would run.";
    case "record_finding": return `Records a finding ${sev(a.severity || "medium")} <b>${esc(a.title || "")}</b>`;
    case "calculate_embedded_emissions": return "Runs the exact EU calculation in code.";
    case "compare_with_eu_defaults": return "Compares your figure with the EU default values and prices it in euros.";
    case "simulate_scenario": {
      const parts = [];
      if (a.precursor_see != null) parts.push(`wire rod at ${esc(a.precursor_see)} tCO2e/t`);
      if (a.fuel_switch_to) parts.push(`furnace on ${esc(String(a.fuel_switch_to).replace(/_/g, " "))}`);
      if (a.yield_gain_pct != null) parts.push(`${esc(a.yield_gain_pct)}% better yield`);
      if (a.year != null) parts.push(`the year ${esc(a.year)}`);
      return `Tests a what-if: ${parts.join(", ") || "a change to your inputs"}.`;
    }
    default: return `Uses the ${esc(tool.replace(/_/g, " "))} tool.`;
  }
}

/* Progress stepper: 0 Read, 1 Calculate, 2 Review, 3 Advise; 4 = all done. */
function setPhase(n) {
  $$("#phases li").forEach((li) => {
    const i = +li.dataset.ph;
    li.classList.toggle("done", i < n);
    li.classList.toggle("active", i === n);
  });
}

/* The run stopped before a result (e.g. no factory records among the files): say why and what to upload. */
function showStop(text) {
  $("#status").innerHTML = `<div class="stop"><b>We couldn’t calculate a figure yet.</b><p>${esc(text)}</p><button type="button" class="btn small primary" data-pick="add-input">Add documents</button></div>`;
  $("#status .stop button").onclick = () => $("#add-input").click();
}

function setStatus(text, busy = false) {
  $("#status").innerHTML = text ? (busy ? '<span class="spin" aria-hidden="true"></span>' : "") + esc(text) : "";
}

/* ---------- documents ---------- */
function upsertDoc(d) {
  S.docs[d.id] = { ...(S.docs[d.id] || {}), ...d };
  const doc = S.docs[d.id];
  let li = $(`#doc-${d.id}`);
  if (!li) {
    li = document.createElement("li");
    li.id = `doc-${d.id}`;
    $("#doc-list").append(li);
  }
  const img = /\.(jpe?g|png|webp)$/i.test(doc.name) && S.runId ? `<img class="thumb" src="/api/runs/${S.runId}/docs/${doc.id}" alt="">` : `<span class="thumb">PDF</span>`;
  let state = "Waiting", cls = "";
  if (doc.state === "reading") state = "Reading…";
  if (doc.source === "gemini") { state = `${DOC_TYPES[doc.doc_type] || "Read"} · ${doc.items} figure${doc.items === 1 ? "" : "s"}`; cls = "ok"; }
  if (doc.source === "replayed") { state = `Replayed · ${doc.items} figures`; cls = "warn"; }
  if (doc.source === "error") { state = "Could not read"; cls = "err"; }
  if (doc.set_aside) { state = "Set aside · not a business record"; cls = "warn"; }
  li.innerHTML = `<button class="doc" data-doc="${doc.id}">${img}<span><span class="name">${esc(doc.name)}</span><span class="state ${cls}">${esc(state)}</span></span></button>`;
  li.querySelector("button").onclick = () => openDoc(doc.id);
}

/* ---------- results ---------- */
async function getRun(tries = 6) {
  // The run lives on the server; a failed fetch (network blip) is retried rather than leaving the page half drawn.
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(`/api/runs/${S.runId}`, { cache: "no-store" });
      if (r.ok) return r;
      if (r.status === 404) return null;
    } catch (e) { /* offline for a moment */ }
    await new Promise((res) => setTimeout(res, 1000 * (i + 1)));
  }
  return null;
}

async function refresh(final) {
  const r = await getRun();
  if (!r) return;
  S.run = await r.json();
  const run = S.run;
  $("#company").textContent = `${run.profile.company} · ${run.profile.period_start} to ${run.profile.period_end}`;
  run.documents.forEach(upsertDoc);
  if (!run.result) return;
  $("#result").hidden = false;
  renderHeader(run.result);
  renderGoods(run.result);
  renderOrigins(run);
  renderSteps(run.result);
  renderFindings(run);
  initWhatIf(run.result);
  renderSupplier(run);
  $("#dl-xlsx").href = `/api/runs/${S.runId}/export/data-pack.xlsx`;
  $("#dl-pdf").href = `/api/runs/${S.runId}/export/evidence.pdf`;
  if (final) {
    const p0 = run.result.precursors[0];
    if (p0) { $("#w-see").value = p0.see; S.scen = null; ["w-fuel", "w-year"].forEach((id) => ($("#" + id).selectedIndex = 0)); $("#w-yield").value = 0; $("#w-solar").checked = false; initWhatIf(run.result); drawRuler(run.result, null); }
    renderBriefs(run.briefs || {});
    renderAgentScenarios(run.agent_scenarios || []);
    $("#trace-done").innerHTML = $("#trace").innerHTML;
    $("#live").hidden = true;
  }
}

function renderHeader(r) {
  $("#see-value").textContent = num(r.see, 2);
  const bases = r.precursors.map((p) => p.basis).join(", ");
  $("#see-basis").innerHTML = `${num(r.activity_level_t, 1)} t of fasteners · wire rod on <b>${esc(bases || "n/a")}</b>`;
  const [qy, qq] = String(r.price.quarter).split("-");
  $("#money-k").textContent = `Gross CBAM cost of the ${num(r.totals.export_eu_t, 0)} t shipped to the EU this period, at the ${qq} ${qy} certificate price of EUR ${num(r.price.eur_per_t, 2)}/t`;
  $("#cost-actual").textContent = eur(r.totals.gross_cost_actual_eur);
  $("#cost-default").textContent = eur(r.totals.gross_cost_default_eur);
  const sav = r.totals.saving_eur;
  $("#saving").innerHTML = sav == null ? "" : sav > 0
    ? `Your own data is worth <span class="verdant">${eur(sav)}</span> to your buyers this period.`
    : `Your figure is above the EU default by ${eur(-sav)}: check the findings before sharing it.`;
  const t = r.totals;
  $("#net-line").innerHTML = t.net_cost_actual_eur == null ? "" :
    `After the free-allocation adjustment, buyers pay an estimated <b>${eur(t.net_cost_actual_eur)}</b> with your data and <b>${eur(t.net_cost_default_eur)}</b> on defaults. The saving stays the same.`;
  drawRuler(r, S.scen);
  const pPrec = r.breakdown.precursors * 100, pFuel = r.breakdown.process_fuels * 100;
  $("#split").innerHTML = `<div class="s-prec" style="width:${pPrec}%" title="Purchased wire rod">Wire rod ${pPrec.toFixed(0)}%</div><div class="s-fuel" style="width:${pFuel}%" title="Fuel burned in the furnace">${pFuel < 20 && innerWidth < 620 ? "" : "Furnace fuel "}${pFuel.toFixed(0)}%</div>`;
}

const SHORT = { "731815": "bolts", "731816": "nuts" };
const shortName = (g) => SHORT[String(g.cn).replace(/\s/g, "").slice(0, 6)] || String(g.description || "goods").split(/[ ,]/)[0].toLowerCase();

function drawRuler(r, scen) {
  const main = [...r.goods].sort((a, b) => b.export_eu_t - a.export_eu_t)[0];
  const def = main?.default_marked;
  const vals = [r.see, def, scen?.see].filter((v) => v != null);
  const max = Math.ceil(Math.max(...vals) + 0.6);
  const pos = (v) => `${(v / max) * 100}%`;
  let html = `<div class="track"></div>`;
  for (let t = 0; t <= max; t++) html += `<div class="tick" style="left:${pos(t)}"><span>${t}</span></div>`;
  const edge = (v) => (v / max > 0.8 ? " edge-r" : v / max < 0.12 ? " edge-l" : "");
  html += `<div class="mark actual${edge(r.see)}" style="left:${pos(r.see)}"><b>Your data ${num(r.see, 2)}</b><i></i></div>`;
  const close = def != null && Math.abs(def - r.see) / max < 0.16;
  if (def != null) html += close ? `<div class="mark default below${edge(def)}" style="left:${pos(def)}"><i></i><b>EU default, ${esc(shortName(main))} ${num(def, 2)}</b></div>` : `<div class="mark default${edge(def)}" style="left:${pos(def)}"><b>EU default, ${esc(shortName(main))} ${num(def, 2)}</b><i></i></div>`;
  if (scen) html += `<div class="mark scen ${close ? "" : "below"}${edge(scen.see)}" style="left:${pos(scen.see)}"><i></i><b>What if ${num(scen.see, 2)}</b></div>`;
  $("#ruler").innerHTML = html;
}

function renderGoods(r) {
  $("#goods-table").innerHTML = `<tr><th>Good</th><th class="num">Made (t)</th><th class="num">To EU (t)</th><th class="num">EU default</th></tr>` +
    r.goods.map((g) => `<tr><td>${esc(g.description)}<br><span class="muted small">CN ${esc(g.cn)}</span> ${chips(g.evidence.slice(0, 3))}</td><td class="num">${num(g.production_t, 1)}</td><td class="num">${num(g.export_eu_t, 1)}</td><td class="num">${g.default_marked == null ? "–" : num(g.default_marked, 2)}</td></tr>`).join("");
  bindChips($("#goods-table"));
}

function renderOrigins(run) {
  // Your own figure next to the EU default values. Your buyer uses your country's default unless you send actual data,
  // so that row is the one to beat; the other countries are context.
  const rows = run.country_comparison || [];
  const me = run.result.see;
  const home = (run.profile && run.profile.country) || "";
  const max = Math.max(...rows.map((r) => r.default_2026), me) * 1.05;
  const show = (c) => (c === "Viet Nam" ? "Vietnam" : c);   // the EU lists use "Viet Nam"
  const all = [...rows.map((r) => ({ label: show(r.country), v: r.default_2026, home: r.country === home })), { label: "You", v: me, me: true }]
    .sort((a, b) => a.v - b.v);
  const tag = (r) => (r.me ? '<i class="o-tag me">your actual data</i>' : r.home ? '<i class="o-tag home">your default today</i>' : "");
  $("#origins").innerHTML = all.map((r) => `<div class="origin ${r.me ? "me" : r.home ? "home" : ""}"><span>${esc(r.label)}${tag(r)}</span><span class="bar-o"><span style="width:${(r.v / max) * 100}%"></span></span><span>${num(r.v, 2)}</span></div>`).join("");
  const def = rows.find((r) => r.country === home);
  $("#origins-note").textContent = def
    ? (me < def.default_2026
      ? `Without your data, your buyer uses ${home}'s default of ${num(def.default_2026, 2)}. Your own figure, ${num(me, 2)}, is ${Math.round((1 - me / def.default_2026) * 100)}% lower.`
      : `Your own figure, ${num(me, 2)}, is above ${home}'s default of ${num(def.default_2026, 2)}. Your buyer can still use the default; the levers below show how to bring your figure down.`)
    : "";
}

function renderSteps(r) {
  $("#steps").innerHTML = r.steps.map((s) => `<li class="step ${s.counted === false ? "off" : ""}"><h4>${esc(s.title)}${s.counted === false ? ' <span class="sev low">not counted</span>' : ""}</h4>
    <span class="formula">${esc(s.formula)}</span><div class="working">${esc(s.working)}</div>
    ${s.note ? `<div class="note">${esc(s.note)}</div>` : ""}<div class="note">Rule: ${esc(s.rule || "")}</div>${chips(s.evidence || [])}</li>`).join("");
  $("#fa-note").textContent = r.free_allocation_note + " " + (r.electricity_note || "");
  bindChips($("#steps"));
}

function renderFindings(run) {
  const agentF = (run.agent_findings || []).map((f) => ({ ...f, agent: true }));
  const covered = new Set(agentF.flatMap((f) => f.covers || []));      // an agent finding that explains a check replaces it
  const rank = { high: 0, medium: 1, low: 2, info: 3 };
  const all = [...agentF, ...run.findings.filter((f) => !covered.has(f.code) && (f.severity !== "info" || ["non_eu_export", "dg_excluded"].includes(f.code)))]
    .sort((a, b) => (rank[a.severity] ?? 2) - (rank[b.severity] ?? 2));
  $("#find-count").textContent = all.length ? String(all.length) : "";
  $("#find-count").title = `${all.length} finding${all.length === 1 ? "" : "s"}, ${all.filter((f) => f.severity === "high").length} high`;
  $("#findings").innerHTML = all.map((f) => `<li class="finding ${f.severity}"><h4><span class="sev ${f.severity}">${f.severity}</span>${f.agent ? '<span class="sev agent">agent review</span>' : ""}${esc(f.title)}</h4>
    <p>${esc(f.detail)}</p>${f.action ? `<p class="act">${esc(f.action)}</p>` : ""}${chips(f.evidence || [])}</li>`).join("");
  bindChips($("#findings"));
}

/* The agents' written briefs describe one set of figures; while new documents are analysed, hide them
   so the page never shows a new number next to old advice. */
function markBriefsStale() {
  const wait = `<p class="muted updating"><span class="spin" aria-hidden="true"></span> The agents are updating this for the new figures…</p>`;
  ["#brief-advice", "#brief-calc", "#brief-quality"].forEach((id) => ($(id).innerHTML = wait));
  $("#agent-scenarios").innerHTML = "";
}

function renderBriefs(b) {
  const by = b.source === "gemini" ? "Written by the agent from tool results" : "Deterministic summary (agents unavailable)";
  $("#brief-advice").innerHTML = b.advice ? md(b.advice) + `<p class="by">Advisor agent · ${by}</p>` : "";
  $("#brief-calc").innerHTML = b.calculation ? md(b.calculation) + `<p class="by">Calculation agent · ${by}</p>` : "";
  $("#brief-quality").innerHTML = b.quality ? md(b.quality) + `<p class="by">Scope & quality agent · ${by}</p>` : "";
}

function renderAgentScenarios(list) {
  if (!list.length) { $("#agent-scenarios").innerHTML = ""; return; }
  $("#agent-scenarios").innerHTML = `<div class="card agent-scen"><h3>Scenarios the advisor agent tested</h3><table class="tbl"><tr><th>Change</th><th class="num">SEE after</th><th class="num">Exposure change</th></tr>` +
    list.map((s) => `<tr><td>${esc(s.applied.join("; "))}</td><td class="num">${num(s.see_after, 2)}</td><td class="num">${eur(s.gross_cost_change_eur_this_period)}</td></tr>`).join("") + `</table></div>`;
}

/* ---------- evidence ---------- */
function chips(ids) {
  if (!ids?.length) return "";
  const ev = S.run?.evidence || {};
  const tip = (id) => { const e = ev[id]; return e ? `Source ${id}: ${e.doc_name}, page ${e.page}. “${e.quote}”. Click to open it.` : "Show where this number comes from"; };
  return `<div class="ev-chips">${ids.map((id) => `<button class="ev" data-ev="${esc(id)}" title="${esc(tip(id))}" aria-label="${esc(tip(id))}">${esc(id)}</button>`).join("")}</div>`;
}
function bindChips(root) { $$(".ev", root).forEach((b) => (b.onclick = () => openEvidence([b.dataset.ev]))); }

function openEvidence(ids) {
  const ev = (S.run?.evidence || {});
  const items = ids.map((i) => ev[i]).filter(Boolean);
  if (!items.length) return;
  const docId = items[0].doc_id;
  $("#drawer-title").textContent = items[0].doc_name;
  $("#drawer-sub").textContent = `Page ${items[0].page} · read by ${S.docs[docId]?.source === "replayed" ? "stored extraction" : "Gemini"} · confidence ${Math.round(items[0].confidence * 100)}%`;
  $("#drawer-quotes").innerHTML = items.map((e) => `<div class="quote"><b>${esc(e.id)}</b> · ${esc(e.description)}: <b>${esc(e.quantity ?? "")} ${esc(e.unit ?? "")}</b><q>${esc(e.quote)}</q></div>`).join("");
  showDoc(docId);
}

function openDoc(docId) {
  const d = S.docs[docId];
  $("#drawer-title").textContent = d?.name || "Document";
  $("#drawer-sub").textContent = d?.doc_type ? DOC_TYPES[d.doc_type] : "";
  const ev = Object.values(S.run?.evidence || {}).filter((e) => e.doc_id === docId);
  $("#drawer-quotes").innerHTML = ev.map((e) => `<div class="quote"><b>${esc(e.id)}</b> · ${esc(e.description)}: <b>${esc(e.quantity ?? "")} ${esc(e.unit ?? "")}</b><q>${esc(e.quote)}</q></div>`).join("") || `<p class="muted small">No figures extracted yet.</p>`;
  showDoc(docId);
}

function showDoc(docId) {
  const d = S.docs[docId];
  const url = `/api/runs/${S.runId}/docs/${docId}`;
  $("#drawer-doc").innerHTML = /\.pdf$/i.test(d?.name || "") ? `<iframe src="${url}" title="Source document"></iframe>` : `<img src="${url}" alt="Source document ${esc(d?.name || "")}">`;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#drawer-close").focus();
}
$("#drawer-close").onclick = () => { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer-close").click(); });

/* ---------- tabs ---------- */
$$(".tab").forEach((t) => t.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.setAttribute("aria-selected", x === t ? "true" : "false"));
  $$(".tabpanel").forEach((p) => (p.hidden = p.dataset.panel !== t.dataset.tab));
}));
function goTab(name) { $(`.tab[data-tab="${name}"]`).click(); }

/* ---------- what-if ---------- */
let whatIfReady = false;
function initWhatIf(r) {
  const p = r.precursors[0];
  if (!whatIfReady) {
    $("#w-see").value = p ? p.see : 2;
    ["w-see", "w-fuel", "w-yield", "w-year", "w-solar"].forEach((id) => $("#" + id).addEventListener("input", queueScenario));
    whatIfReady = true;
  }
  $("#o-see").textContent = `${num($("#w-see").value, 2)} tCO2e/t`;
  $("#o-yield").textContent = `${$("#w-yield").value}%`;
  if (!S.scen) $("#whatif-out").innerHTML = `<p class="muted">Move a control to see how your figure and your buyers' bill would change.</p>`;
}
function queueScenario() {
  $("#o-see").textContent = `${num($("#w-see").value, 2)} tCO2e/t`;
  $("#o-yield").textContent = `${$("#w-yield").value}%`;
  clearTimeout(S.scenTimer);
  S.scenTimer = setTimeout(runScenario, 220);
}
async function runScenario() {
  const r = S.run.result;
  const p = r.precursors[0];
  const body = {
    precursor_see: p && Math.abs(+$("#w-see").value - p.see) > 0.001 ? +$("#w-see").value : null,
    fuel_switch_to: $("#w-fuel").value || null,
    yield_gain_pct: +$("#w-yield").value || null,
    renewable_electricity_pct: $("#w-solar").checked ? 100 : null,
    year: +$("#w-year").value !== 2026 ? +$("#w-year").value : null,
  };
  const res = await fetch(`/api/runs/${S.runId}/scenario`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) return;
  const s = await res.json();
  S.scen = s.applied.length ? { see: s.scenario.see } : null;
  drawRuler(r, S.scen);
  if (!s.applied.length) { $("#whatif-out").innerHTML = `<p class="muted">No change applied.</p>`; return; }
  const dC = s.delta_cost_eur;
  $("#whatif-out").innerHTML = `<p class="muted small">New specific embedded emissions</p><p class="big">${num(s.scenario.see, 2)}</p>
    <p class="delta">${s.delta_see <= 0 ? "" : "+"}${num(s.delta_see, 2)} tCO2e/t · ${dC <= 0 ? "saves" : "adds"} ${eur(Math.abs(dC))} gross on this period's EU shipments</p>
    <p class="small">On EU defaults the same shipments would cost ${eur(s.scenario.default_cost)}.</p>
    <ul>${s.applied.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>`;
}

/* ---------- supplier ---------- */
function renderSupplier(run) {
  const r = run.result;
  const p = r.precursors[0];
  if (!p) { $("#supplier").innerHTML = `<p class="muted">No precursor found in the documents.</p>`; return; }
  const onDefault = p.basis.startsWith("EU default");
  const req = run.supplier_request;
  $("#supplier").innerHTML = `
    <div class="step-card">
      <h3>${onDefault ? "Your wire rod is on the EU default value" : `<span class="done-tag">Supplier data in use</span>`}</h3>
      <p>${esc(p.description)} from <b>${esc(p.supplier || "your supplier")}</b>: ${num(p.consumed_t, 1)} t consumed at <b>${num(p.see, 2)} tCO2e/t</b> (${esc(p.basis)}). It makes up ${(r.breakdown.precursors * 100).toFixed(0)}% of your footprint, so it is the number most worth improving.</p>
      ${p.see_evidence?.length ? chips(p.see_evidence) : ""}
    </div>
    <div class="step-card">
      <h3>1. Ask the mill for its verified figure</h3>
      <p class="small muted">The supplier data agent drafts a request that names the exact fields the EU method needs. You review and send it yourself.</p>
      ${req ? `<label class="small">Subject<input type="text" id="sup-subj" value="${esc(req.subject)}"></label><label class="small">Message<textarea id="sup-body">${esc(req.body)}</textarea></label>
        <div class="row-btns"><button class="btn small" id="sup-copy">Copy message</button><a class="btn small ghost" id="sup-mail" href="#">Open in email</a><button class="btn small ghost" id="sup-draft">Draft again</button></div>`
      : `<div class="row-btns"><button class="btn small primary" id="sup-draft">Draft the request</button></div>`}
    </div>
    <div class="step-card">
      <h3>2. Add the supplier's reply</h3>
      <p class="small muted">Upload the declaration the mill sends back. CarbonTrail reads it, checks it is verified, and recalculates.</p>
      <div class="row-btns"><button type="button" class="btn small" id="sup-pick">Upload the reply</button><input type="file" id="sup-upload" accept=".pdf,.jpg,.jpeg,.png" hidden>
      <button class="btn small ghost" id="sup-demo">Use the demo mill's reply</button></div>
    </div>`;
  bindChips($("#supplier"));
  $("#sup-draft").onclick = draftSupplier;
  $("#sup-pick").onclick = () => $("#sup-upload").click();
  if (req) {
    $("#sup-copy").onclick = () => navigator.clipboard.writeText(`${$("#sup-subj").value}\n\n${$("#sup-body").value}`).then(() => ($("#sup-copy").textContent = "Copied"));
    $("#sup-mail").onclick = (e) => { e.preventDefault(); location.href = `mailto:?subject=${encodeURIComponent($("#sup-subj").value)}&body=${encodeURIComponent($("#sup-body").value)}`; };
  }
  $("#sup-upload").onchange = (e) => e.target.files.length && addDocs({ files: e.target.files }).then((ok) => ok && goTab("summary"));
  $("#sup-demo").onclick = () => addDocs({ sample: "11_supplier_emissions_declaration.pdf" }).then((ok) => ok && goTab("summary"));
}

async function draftSupplier() {
  const b = $("#sup-draft");
  b.disabled = true; b.textContent = "Drafting…";
  let data = null;
  try {
    const res = await fetch(`/api/runs/${S.runId}/supplier-request`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sender: "Operations Head, Verkam Fasteners" }) });
    data = res.ok ? await res.json() : null;
  } catch { data = null; }
  if (!data) { b.disabled = false; b.textContent = "Draft the request"; setStatus("The supplier data agent could not be reached. Try again in a minute."); return; }
  S.run.supplier_request = data;
  renderSupplier(S.run);
}

/* ---------- ask ---------- */
$$("[data-open=ask]").forEach((b) => (b.onclick = () => $("#ask").showModal()));
$$("#ask-suggest .chip").forEach((c) => (c.onclick = () => { $("#ask-input").value = c.textContent; $("#ask-form").requestSubmit(); }));
$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#ask-input").value.trim();
  if (!q) return;
  $("#ask-input").value = "";
  const log = $("#ask-log");
  log.insertAdjacentHTML("beforeend", `<div class="ask-q">${esc(q)}</div>`);
  const a = document.createElement("div");
  a.className = "ask-a";
  a.innerHTML = `<span class="spin" aria-hidden="true"></span> Checking the regulation notes…`;
  log.append(a);
  log.scrollTop = log.scrollHeight;
  const slow = setTimeout(() => { a.innerHTML = `<span class="spin" aria-hidden="true"></span> Gemini is busy; trying a backup model…`; }, 20000);
  const ctl = new AbortController();
  const stop = setTimeout(() => ctl.abort(), 90000);
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, run_id: S.runId }), signal: ctl.signal });
    let d = {};
    try { d = await r.json(); } catch { /* proxy error page */ }
    if (r.ok && d.answer) a.innerHTML = md(d.answer);
    else askFailed(a, q, d.detail || `The regulation agent did not answer (HTTP ${r.status}).`);
  } catch (err) {
    askFailed(a, q, err.name === "AbortError" ? "The regulation agent took too long to answer." : "The regulation agent could not be reached.");
  } finally { clearTimeout(slow); clearTimeout(stop); }
  log.scrollTop = log.scrollHeight;
});

function askFailed(el, q, why) {
  el.classList.add("err");
  el.innerHTML = `<p>${esc(why)}</p><button type="button" class="btn small ghost">Try again</button>`;
  el.querySelector("button").onclick = () => { el.previousElementSibling?.remove(); el.remove(); $("#ask-input").value = q; $("#ask-form").requestSubmit(); };
}


/* ---------- tiny markdown ---------- */
function md(src) {
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  let html = "", list = false;
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\*(.+?)\*/g, "<i>$1</i>").replace(/`(.+?)`/g, "<code>$1</code>");
  for (const raw of lines) {
    const l = raw.trim();
    if (/^[-*•]\s+/.test(l)) { if (!list) { html += "<ul>"; list = true; } html += `<li>${inline(l.replace(/^[-*•]\s+/, ""))}</li>`; continue; }
    if (list) { html += "</ul>"; list = false; }
    if (/^#{1,4}\s/.test(l)) html += `<h3>${inline(l.replace(/^#{1,4}\s/, ""))}</h3>`;
    else if (l) html += `<p>${inline(l)}</p>`;
  }
  if (list) html += "</ul>";
  return html;
}
