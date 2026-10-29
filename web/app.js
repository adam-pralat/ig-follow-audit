const $ = (sel) => document.querySelector(sel);

const state = {
  report: null,
  snapshot: null,
  tab: "not_following_back",
  query: "",
  sort: "oldest",
  showIgnored: false,
};

const mutualSince = (p) => (p.follows_you_since && p.you_follow_since
  ? Math.max(p.follows_you_since, p.you_follow_since) : p.follows_you_since || p.you_follow_since);

const TABS = [
  { key: "not_following_back", label: "Not following you back", date: (p) => p.you_follow_since,
    meta: "You followed them", ignorable: true,
    hint: "Accounts you follow that don't follow you. Ignore the ones you don't expect a follow-back from (celebs, brands)." },
  { key: "fans", label: "You don't follow back", date: (p) => p.follows_you_since,
    meta: "Followed you", hint: "Accounts that follow you that you don't follow." },
  { key: "mutuals", label: "Mutuals", date: mutualSince, meta: "Mutuals since" },
  { key: "pending", label: "Pending requests", date: (p) => p.ts, meta: "Requested",
    hint: "Follow requests you sent to private accounts that haven't been accepted." },
  { key: "recently_unfollowed", label: "Recently unfollowed", date: (p) => p.ts, meta: "You unfollowed them",
    hint: "Accounts Instagram lists as recently unfollowed by you." },
];

// ---------- helpers ----------

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const profileUrl = (u) => `https://www.instagram.com/${encodeURIComponent(u)}/`;

const fmtDate = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString(undefined,
  { year: "numeric", month: "short", day: "numeric" }) : "unknown date");

const fmtNum = (n) => (n ?? 0).toLocaleString();

function ago(ts) {
  if (!ts) return "";
  const days = (Date.now() / 1000 - ts) / 86400;
  if (days < 1) return "today";
  if (days < 30) return `${Math.round(days)}d ago`;
  if (days < 365) return `${Math.round(days / 30)}mo ago`;
  return `${(days / 365).toFixed(1).replace(/\.0$/, "")}y ago`;
}

function avatarColor(name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 55% 50%)`;
}

function toast(message, ms = 4000) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, ms);
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} failed (${res.status})`);
  return res.json();
}

// ---------- loading ----------

async function load() {
  const q = state.snapshot ? `?snapshot=${state.snapshot}` : "";
  state.report = await api(`/api/report${q}`);
  render();
}

function render() {
  const r = state.report;
  const hasData = r.snapshots.length > 0;
  state.newestImport = Math.max(0, ...r.snapshots.map((s) => s.id));
  $("#empty").hidden = hasData;
  $("#dashboard").hidden = !hasData;
  $("#snapshot-picker").hidden = !hasData;
  if (!hasData) return;

  state.snapshot = r.current.id;
  renderSnapshotPicker();
  $("#subtitle").textContent = `Export from ${fmtDate(Date.parse(r.current.export_date) / 1000)} · ${r.current.source}`;
  renderStats();
  renderChanges();
  renderTabs();
  renderList();
  renderFacts();
  renderActivity();
  renderHistory();
}

function renderSnapshotPicker() {
  const { snapshots, current } = state.report;
  $("#snapshot").innerHTML = [...snapshots].reverse().map((s) =>
    `<option value="${s.id}" ${s.id === current.id ? "selected" : ""}>${escapeHtml(s.export_date)} — ${escapeHtml(s.source)}</option>`
  ).join("");
}

// ---------- stats ----------

function delta(now, before) {
  if (before == null) return "";
  const d = now - before;
  if (d === 0) return `<span class="delta">no change</span>`;
  return `<span class="delta ${d > 0 ? "up" : "down"}">${d > 0 ? "+" : ""}${fmtNum(d)} since last export</span>`;
}

function renderStats() {
  const { stats: s, previous: p } = state.report;
  const rate = s.follow_back_rate == null ? "—" : `${Math.round(s.follow_back_rate * 100)}%`;
  const cards = [
    { label: "Followers", value: fmtNum(s.followers), sub: delta(s.followers, p?.followers) },
    { label: "Following", value: fmtNum(s.following), sub: delta(s.following, p?.following) },
    { label: "Mutuals", value: fmtNum(s.mutuals), sub: delta(s.mutuals, p?.mutuals) },
    { label: "Not following you back", value: fmtNum(s.not_following_back), highlight: true,
      sub: s.not_following_back_ignored ? `${fmtNum(s.not_following_back_ignored)} ignored` : "" },
    { label: "You don't follow back", value: fmtNum(s.fans), sub: "" },
    { label: "Follow-back rate", value: rate, sub: "of people you follow" },
  ];
  $("#stats").innerHTML = cards.map((c) => `
    <div class="stat ${c.highlight ? "highlight" : ""}">
      <div class="label">${c.label}</div>
      <div class="value">${c.value}</div>
      <div class="sub">${c.sub}</div>
    </div>`).join("");
}

// ---------- changes between exports ----------

function chips(people, warn) {
  if (!people.length) return `<span class="muted">Nobody</span>`;
  return `<div class="chips">${people.map((p) => {
    const flagged = warn && warn(p);
    return `<a class="chip ${flagged ? "warn" : ""}" href="${profileUrl(p.username)}" target="_blank" rel="noopener"
      title="${flagged ? "You still follow them" : ""}">@${escapeHtml(p.username)}</a>`;
  }).join("")}</div>`;
}

function renderChanges() {
  const c = state.report.changes;
  if (!c) {
    $("#changes-title").textContent = "Since your last export";
    $("#changes").innerHTML = `<p class="muted">This is your first export. Import another one later and you'll see who followed and unfollowed you in between.</p>`;
    return;
  }
  $("#changes-title").textContent = `Since your export on ${fmtDate(Date.parse(c.since) / 1000)}`;
  const groups = [
    { title: "New followers", people: c.new_followers },
    { title: "Unfollowed you", people: c.lost_followers, warn: (p) => p.you_follow_since },
    { title: "You followed", people: c.you_followed },
    { title: "You unfollowed", people: c.you_unfollowed },
  ];
  $("#changes").innerHTML = `<div class="changes">${groups.map((g) => `
    <div class="change">
      <h3><span class="n">${g.people.length}</span>${g.title}</h3>
      ${chips(g.people, g.warn)}
    </div>`).join("")}</div>
    ${c.lost_followers.some((p) => p.you_follow_since)
      ? `<p class="hint">Red = they unfollowed you but you still follow them.</p>` : ""}`;
}

// ---------- lists ----------

function renderTabs() {
  const lists = state.report.lists;
  const tabs = TABS.filter((t) => lists[t.key].length || ["not_following_back", "fans", "mutuals"].includes(t.key));
  if (!tabs.some((t) => t.key === state.tab)) state.tab = tabs[0].key;
  $("#tabs").innerHTML = tabs.map((t) => {
    const count = t.ignorable ? lists[t.key].filter((p) => !p.ignored).length : lists[t.key].length;
    return `<button class="tab" role="tab" data-tab="${t.key}" aria-selected="${t.key === state.tab}">
      ${t.label}<span class="count">${fmtNum(count)}</span></button>`;
  }).join("");
}

function renderList() {
  const tab = TABS.find((t) => t.key === state.tab);
  $("#show-ignored-wrap").hidden = !tab.ignorable;
  $("#tab-hint").textContent = tab.hint || "";

  const q = state.query.trim().toLowerCase().replace(/^@/, "");
  let people = state.report.lists[tab.key].filter((p) =>
    (!q || p.username.includes(q)) && (!tab.ignorable || state.showIgnored || !p.ignored));

  const undated = state.sort === "newest" ? -Infinity : Infinity;
  if (state.sort === "az") people.sort((a, b) => a.username.localeCompare(b.username));
  else people.sort((a, b) => ((tab.date(a) ?? undated) - (tab.date(b) ?? undated)) * (state.sort === "newest" ? -1 : 1));

  if (!people.length) {
    $("#list").innerHTML = `<li class="empty-list">${q ? "No matches." : "Nobody here 🎉"}</li>`;
    return;
  }
  $("#list").innerHTML = people.map((p) => {
    const ts = tab.date(p);
    const button = tab.ignorable
      ? `<button class="link-btn" data-ignore="${escapeHtml(p.username)}" data-value="${!p.ignored}">
           ${p.ignored ? "Unignore" : "Ignore"}</button>` : "";
    return `<li class="person ${p.ignored && tab.ignorable ? "is-ignored" : ""}">
      <span class="avatar" style="background:${avatarColor(p.username)}">${escapeHtml(p.username.replace(/[^a-z0-9]/gi, "")[0] || "?")}</span>
      <div class="who">
        <a class="name" href="${profileUrl(p.username)}" target="_blank" rel="noopener">@${escapeHtml(p.username)}</a>
        <div class="meta">${tab.meta} ${ts ? `${fmtDate(ts)} · ${ago(ts)}` : "— date unknown"}</div>
      </div>
      ${button}
    </li>`;
  }).join("");
}

// ---------- fun facts ----------

function renderFacts() {
  $("#facts").innerHTML = state.report.facts.map((f) => `
    <div class="fact">
      <div class="label">${escapeHtml(f.label)}</div>
      <div class="value">${f.username
        ? `<a href="${profileUrl(f.username)}" target="_blank" rel="noopener">@${escapeHtml(f.username)}</a>`
        : escapeHtml(f.value)}</div>
      ${f.ts ? `<div class="date">${fmtDate(f.ts)} · ${ago(f.ts)}</div>` : ""}
    </div>`).join("") || `<p class="muted">Not enough dated data for trivia.</p>`;
}

// ---------- charts ----------

function attachTooltip(container, svg, items) {
  let tip = container.querySelector(".tooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "tooltip";
    tip.hidden = true;
    container.appendChild(tip);
  }
  svg.addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const item = items.find((it) => x >= it.x0 && x < it.x1);
    if (!item) { tip.hidden = true; return; }
    tip.hidden = false;
    tip.innerHTML = item.html;
    tip.style.left = `${(item.x0 + item.x1) / 2}px`;
    tip.style.top = `${item.top}px`;
  });
  svg.addEventListener("mouseleave", () => { tip.hidden = true; });
}

function niceMax(v) {
  if (v <= 5) return 5;
  const mag = 10 ** Math.floor(Math.log10(v));
  return Math.ceil(v / mag) * mag;
}

function axis(width, height, pad, max) {
  return [0, 0.5, 1].map((f) => {
    const y = pad.top + (height - pad.top - pad.bottom) * (1 - f);
    return `<line class="grid-line" x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}"/>
            <text x="${pad.left - 8}" y="${y + 4}" text-anchor="end">${fmtNum(Math.round(max * f))}</text>`;
  }).join("");
}

function renderActivity() {
  const el = $("#activity");
  const rows = state.report.activity;
  if (!rows.length) { el.innerHTML = `<p class="muted">No dated follows in this export.</p>`; return; }

  // Fill gaps between the first and last month, then roll up to years if the range is long.
  const byMonth = new Map(rows.map((r) => [r.month, r]));
  let [y, m] = rows[0].month.split("-").map(Number);
  const end = rows[rows.length - 1].month;
  let buckets = [];
  for (;;) {
    const key = `${y}-${String(m).padStart(2, "0")}`;
    const r = byMonth.get(key);
    buckets.push({ key, label: new Date(y, m - 1).toLocaleDateString(undefined, { month: "short", year: "2-digit" }),
      followers: r?.followers || 0, following: r?.following || 0 });
    if (key === end) break;
    if (++m > 12) { m = 1; y++; }
  }
  if (buckets.length > 36) {
    const years = new Map();
    for (const b of buckets) {
      const yr = b.key.slice(0, 4);
      const acc = years.get(yr) || { key: yr, label: yr, followers: 0, following: 0 };
      acc.followers += b.followers; acc.following += b.following;
      years.set(yr, acc);
    }
    buckets = [...years.values()];
  }

  const width = el.clientWidth || 800, height = 240;
  const pad = { top: 12, right: 8, bottom: 28, left: 44 };
  const max = niceMax(Math.max(...buckets.map((b) => Math.max(b.followers, b.following))));
  const slot = (width - pad.left - pad.right) / buckets.length;
  const barW = Math.max(1, Math.min(18, slot * 0.38));
  const plotH = height - pad.top - pad.bottom;
  const labelEvery = Math.ceil(buckets.length / Math.max(1, Math.floor((width - pad.left) / 56)));

  const items = [];
  const bars = buckets.map((b, i) => {
    const cx = pad.left + slot * (i + 0.5);
    const hF = (b.followers / max) * plotH, hG = (b.following / max) * plotH;
    items.push({ x0: cx - slot / 2, x1: cx + slot / 2, top: pad.top + plotH - Math.max(hF, hG),
      html: `<b>${escapeHtml(b.label)}</b><br>${fmtNum(b.followers)} followed you · ${fmtNum(b.following)} you followed` });
    return `<rect class="bar followers" x="${cx - barW - 1}" y="${pad.top + plotH - hF}" width="${barW}" height="${hF}" rx="2"/>
      <rect class="bar following" x="${cx + 1}" y="${pad.top + plotH - hG}" width="${barW}" height="${hG}" rx="2"/>
      ${i % labelEvery === 0 ? `<text x="${cx}" y="${height - 8}" text-anchor="middle">${escapeHtml(b.label)}</text>` : ""}`;
  }).join("");

  el.innerHTML = `<svg viewBox="0 0 ${width} ${height}" height="${height}">${axis(width, height, pad, max)}${bars}</svg>`;
  attachTooltip(el, el.querySelector("svg"), items);
}

function renderHistory() {
  const snaps = state.report.snapshots;
  $("#history-panel").hidden = snaps.length < 2;
  if (snaps.length < 2) return;
  const el = $("#history");
  const width = el.clientWidth || 800, height = 220;
  const pad = { top: 12, right: 16, bottom: 28, left: 52 };
  const series = ["followers", "following", "mutuals"];
  const max = niceMax(Math.max(...snaps.flatMap((s) => series.map((k) => s[k] || 0))));
  const plotH = height - pad.top - pad.bottom;
  const step = (width - pad.left - pad.right) / (snaps.length - 1);
  const x = (i) => pad.left + step * i;
  const y = (v) => pad.top + plotH * (1 - (v || 0) / max);

  const lines = series.map((k) => `
    <polyline class="line ${k}" points="${snaps.map((s, i) => `${x(i)},${y(s[k])}`).join(" ")}"/>
    ${snaps.map((s, i) => `<circle class="dot ${k}" cx="${x(i)}" cy="${y(s[k])}" r="3.5"/>`).join("")}`).join("");
  const labelEvery = Math.ceil(snaps.length / Math.max(1, Math.floor(width / 90)));
  const labels = snaps.map((s, i) => (i % labelEvery === 0 || i === snaps.length - 1
    ? `<text x="${x(i)}" y="${height - 8}" text-anchor="middle">${escapeHtml(s.export_date)}</text>` : "")).join("");
  const items = snaps.map((s, i) => ({ x0: x(i) - step / 2, x1: x(i) + step / 2, top: y(Math.max(s.followers, s.following)),
    html: `<b>${escapeHtml(s.export_date)}</b><br>${fmtNum(s.followers)} followers · ${fmtNum(s.following)} following · ${fmtNum(s.mutuals)} mutuals` }));

  el.innerHTML = `<svg viewBox="0 0 ${width} ${height}" height="${height}">${axis(width, height, pad, max)}${lines}${labels}</svg>`;
  attachTooltip(el, el.querySelector("svg"), items);
}

// ---------- events ----------

$("#tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-tab]");
  if (!btn) return;
  state.tab = btn.dataset.tab;
  renderTabs();
  renderList();
});

$("#search").addEventListener("input", (e) => { state.query = e.target.value; renderList(); });
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; renderList(); });
$("#show-ignored").addEventListener("change", (e) => { state.showIgnored = e.target.checked; renderList(); });
$("#snapshot").addEventListener("change", (e) => { state.snapshot = Number(e.target.value); load(); });

$("#list").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-ignore]");
  if (!btn) return;
  btn.disabled = true;
  await api("/api/ignore", { username: btn.dataset.ignore, ignored: btn.dataset.value === "true" });
  await load();
});

$("#rescan").addEventListener("click", async () => {
  const btn = $("#rescan");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  try {
    const result = await api("/api/ingest", {});
    const lines = result.messages.length ? result.messages : ["No new exports found in the exports folder."];
    toast(lines.join("\n"), 6000);
    if (result.imported) state.snapshot = null; // jump to the newest export
    await load();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Rescan exports";
  }
});

// The server imports new exports on its own; refresh when one shows up.
async function checkForNewExports() {
  if (!state.report) return;
  try {
    const { latest } = await api("/api/status");
    if (!latest || latest === state.newestImport) return;
    const snaps = state.report.snapshots;
    if (!snaps.length || state.snapshot === snaps[snaps.length - 1].id) state.snapshot = null;
    await load();
    toast("New export imported. Dashboard updated.");
  } catch {
    // Server not reachable right now; try again next tick.
  }
}
setInterval(checkForNewExports, 4000);

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.report?.snapshots.length) { renderActivity(); renderHistory(); } }, 150);
});

load().catch((err) => toast(err.message));
