/* ══════════════════════════════════════════════
   Lobbying Signals — Dashboard v2
   Two-panel: ranked signal list + always-visible detail
   ══════════════════════════════════════════════ */

const DATA_PATH = "data";

const MODES = {
    topics:      { label: "Topics",      isSignal: true,  supportsWindow: true },
    entities:    { label: "Entities",    isSignal: true,  supportsWindow: true },
    domains:     { label: "Domains",     isSignal: true,  supportsWindow: true },
    legislation: { label: "Legislation", isSignal: true,  supportsWindow: true },
    recent:      { label: "Filings",     isSignal: false, supportsWindow: false }
};

const COMPARE = {
    yoy:  { key: "shareDeltaYoy",  baselineCountKey: "yoyCount",  baselineShareKey: "yoyShare",  label: "same period last year" },
    prev: { key: "shareDeltaPrev", baselineCountKey: "prevCount",  baselineShareKey: "prevShare", label: "prior period" }
};

const SORT = {
    impact: "impact",
    count: "mentions",
    yoy: "year-ago delta",
    prev: "prior-period delta",
    name: "name"
};

const CATEGORY = {
    topics:      { label: "Topic",       badgeClass: "topic",       clientsKey: "topic_clients",       examplesKey: "topic_examples" },
    entities:    { label: "Entity",      badgeClass: "entity",      clientsKey: "entity_clients",      examplesKey: "entity_examples" },
    domains:     { label: "Domain",      badgeClass: "domain",      clientsKey: "domain_clients",      examplesKey: "domain_examples" },
    legislation: { label: "Legislation", badgeClass: "legislation", clientsKey: "legislation_clients", examplesKey: "legislation_examples" }
};

const DISPLAY_LABELS = {
    entities: {
        "VA": "Department of Veterans Affairs"
    },
    legislation: {
        "H.R. 1": "H.R. 1 / One Big Beautiful Bill Act",
        "and Related Agencies Appropriations": "Related Agencies Appropriations"
    }
};

/* ─── State ─── */

let state = {
    trends: null,
    stats: null,
    filings: [],
    timeseries: null,
    view: {
        mode: "topics",
        window: "90d",
        compare: "yoy",
        query: "",
        minCount: 1,
        sort: "impact"
    },
    selected: null  // { kind: "signal"|"filing", id, data }
};

/* ─── Utilities ─── */

function parseDateValue(value) {
    if (!value) return null;
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
        const [y, m, d] = value.split("-").map(Number);
        return new Date(y, m - 1, d);
    }
    const dt = new Date(value);
    return Number.isNaN(dt.getTime()) ? null : dt;
}

const fmt = {
    num: n => {
        const x = Number(n || 0);
        if (x >= 1e6) return (x / 1e6).toFixed(1) + "M";
        if (x >= 1e3) return (x / 1e3).toFixed(1) + "K";
        return Math.round(x).toLocaleString("en-US");
    },
    pct: n => (n == null ? "\u2014" : `${Number(n).toFixed(2)}%`),
    pp: n => {
        if (n == null || Number.isNaN(Number(n))) return "\u2014";
        const x = Number(n);
        return `${x > 0 ? "+" : ""}${x.toFixed(2)} pp`;
    },
    money: n => {
        const x = Number(n || 0);
        if (x >= 1e9) return `$${(x / 1e9).toFixed(1)}B`;
        if (x >= 1e6) return `$${(x / 1e6).toFixed(1)}M`;
        if (x >= 1e3) return `$${(x / 1e3).toFixed(0)}K`;
        return `$${Math.round(x)}`;
    },
    dateShort: d => {
        const dt = parseDateValue(d);
        return dt ? dt.toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "\u2014";
    },
    dateLong:  d => {
        const dt = parseDateValue(d);
        return dt ? dt.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) : "\u2014";
    },
    ago: d => {
        if (!d) return "\u2014";
        const m = Math.max(0, Math.floor((Date.now() - new Date(d).getTime()) / 60000));
        if (m < 60)   return `${m}m ago`;
        if (m < 1440) return `${Math.floor(m / 60)}h ago`;
        return `${Math.floor(m / 1440)}d ago`;
    },
    esc: t => { const d = document.createElement("div"); d.textContent = t || ""; return d.innerHTML; }
};

function toNum(v) { const x = Number(v); return Number.isFinite(x) ? x : 0; }
function getQuarterContext() { return state.timeseries?.context || null; }

function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
}

function bindAction(node, onAction, ariaLabel) {
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    if (ariaLabel) node.setAttribute("aria-label", ariaLabel);
    node.onclick = onAction;
    node.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onAction();
        }
    };
}

/* ─── Data helpers ─── */

function compareConfig()        { return COMPARE[state.view.compare] || COMPARE.yoy; }
function signalDelta(s)         { return toNum(s[compareConfig().key]); }
function signalBaselineCount(s) { return toNum(s[compareConfig().baselineCountKey]); }
function signalBaselineShare(s) { return toNum(s[compareConfig().baselineShareKey]); }
function sortLabel()            { return SORT[state.view.sort] || SORT.impact; }

function confidenceClass(s) {
    const r = String(s.confidence || "").toLowerCase();
    return ["high", "medium", "low"].includes(r) ? r : "medium";
}

function confidenceLabel(s) {
    const c = confidenceClass(s);
    return c[0].toUpperCase() + c.slice(1);
}

function displayName(itemOrMode, maybeName) {
    const mode = typeof itemOrMode === "string" ? itemOrMode : itemOrMode?.mode;
    const name = typeof itemOrMode === "string" ? maybeName : itemOrMode?.name;
    return DISPLAY_LABELS[mode]?.[name] || name || "";
}

function signalItems(mode = state.view.mode, windowKey = state.view.window) {
    const meta = CATEGORY[mode];
    if (!meta) return [];
    const items    = state.trends?.[mode]?.[windowKey] || [];
    const clients  = state.trends?.[meta.clientsKey]?.[windowKey] || {};
    const examples = state.trends?.[meta.examplesKey]?.[windowKey] || {};
    return items.map(item => ({
        id: `${mode}:${windowKey}:${item.name}`,
        mode,
        name: item.name,
        count: toNum(item.count),
        prevCount: toNum(item.prev_count),
        yoyCount: toNum(item.yoy_count),
        currentShare: toNum(item.current_share_pct),
        prevShare: toNum(item.prev_share_pct),
        yoyShare: toNum(item.yoy_share_pct),
        shareDeltaPrev: toNum(item.share_delta_prev_pp),
        shareDeltaYoy: toNum(item.share_delta_yoy_pp),
        confidence: item.confidence || "medium",
        topClients: clients[item.name] || [],
        examples: examples[item.name] || []
    }));
}

function filterSignals(items, { applyQuery = true } = {}) {
    const q = state.view.query.trim().toLowerCase();
    return items
        .filter(i => i.count >= state.view.minCount)
        .filter(i => {
            if (!applyQuery || !q) return true;
            return i.name.toLowerCase().includes(q);
        })
        .sort((a, b) => {
            const byName = () => a.name.localeCompare(b.name);
            const byCount = () => b.count - a.count;
            const byImpact = () => Math.abs(signalDelta(b)) - Math.abs(signalDelta(a));
            const byYoy = () => b.shareDeltaYoy - a.shareDeltaYoy;
            const byPrev = () => b.shareDeltaPrev - a.shareDeltaPrev;

            let d = 0;
            if (state.view.sort === "count") d = byCount();
            else if (state.view.sort === "yoy") d = byYoy();
            else if (state.view.sort === "prev") d = byPrev();
            else if (state.view.sort === "name") d = byName();
            else d = byImpact();

            if (Math.abs(d) > 0.0001) return d;
            d = byCount();
            if (Math.abs(d) > 0.0001) return d;
            return byName();
        });
}

function getListItems() {
    if (state.view.mode === "recent") {
        const q = state.view.query.trim().toLowerCase();
        let items = state.filings || [];
        if (q) {
            items = items.filter(f =>
                [f.client, f.registrant, f.domain, ...(f.domains || []), ...(f.topics || []), ...(f.entities || []), ...(f.legislation || [])]
                    .join(" ").toLowerCase().includes(q)
            );
        }
        return items;
    }
    return filterSignals(signalItems());
}

async function loadJSON(file) {
    try {
        const r = await fetch(`${DATA_PATH}/${file}?v=${Date.now()}`);
        if (!r.ok) return null;
        return await r.json();
    }
    catch { return null; }
}

/* ─── SVG Spark Bars ─── */

function createSparkBars(values, color) {
    const w = 48, h = 16;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "signal-spark");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("preserveAspectRatio", "none");

    if (!values || values.length < 1) return svg;

    const max = Math.max(...values, 1);
    const step = w / values.length;
    const barWidth = Math.max(0.8, step - 0.7);
    for (let i = 0; i < values.length; i++) {
        const v = toNum(values[i]);
        const barH = Math.max(1, (v / max) * (h - 2));
        const rect = document.createElementNS(ns, "rect");
        rect.setAttribute("x", (i * step).toFixed(2));
        rect.setAttribute("y", (h - 1 - barH).toFixed(2));
        rect.setAttribute("width", barWidth.toFixed(2));
        rect.setAttribute("height", barH.toFixed(2));
        rect.setAttribute("rx", "0.4");
        rect.setAttribute("fill", color);
        rect.setAttribute("fill-opacity", "0.85");
        svg.appendChild(rect);
    }

    return svg;
}

/* ─── Detail Bar Chart ─── */

function drawDetailChart(values, periods, options = {}) {
    const W = 600, H = 160;
    const pad = { top: 10, right: 12, bottom: 32, left: 44 };
    const max = Math.max(...values, 1);
    const fmtY = options.percent ? (v => `${v.toFixed(v >= 10 ? 1 : 2)}%`) : fmt.num;

    // Y-axis tick values
    const niceMax = niceNum(max);
    const ticks = [0, niceMax * 0.25, niceMax * 0.5, niceMax * 0.75, niceMax];
    const plotBottom = H - pad.bottom;

    // Grid lines
    const grid = ticks.map(v => {
        const y = pad.top + (1 - v / niceMax) * (plotBottom - pad.top);
        return `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="rgba(126,142,163,0.1)" stroke-width="1" />`;
    }).join("");

    // Y-axis labels
    const yLabels = ticks.map(v => {
        const y = pad.top + (1 - v / niceMax) * (plotBottom - pad.top);
        return `<text x="${pad.left - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end" fill="#4b5c6e" font-family="'IBM Plex Mono',monospace" font-size="9">${fmtY(v)}</text>`;
    }).join("");

    // Data points and bars
    const n = values.length;
    const plotWidth = W - pad.left - pad.right;
    const step = n > 0 ? (plotWidth / n) : plotWidth;
    const barW = Math.max(2, Math.min(11, step * 0.74));
    const points = values.map((v, i) => {
        const x = pad.left + (i * step) + ((step - barW) / 2) + (barW / 2);
        const y = pad.top + (1 - v / niceMax) * (plotBottom - pad.top);
        return { x, y };
    });
    const bars = values.map((v, i) => {
        const x = pad.left + (i * step) + ((step - barW) / 2);
        const y = pad.top + (1 - v / niceMax) * (plotBottom - pad.top);
        const h = Math.max(1, plotBottom - y);
        return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="1.2" fill="#4e96d4" fill-opacity="0.78" />`;
    }).join("");

    // X-axis: date labels at ~5 evenly spaced points
    const xIndices = pickXIndices(n, 5);
    const xLabels = xIndices.map(idx => {
        const x = pad.left + (idx * step) + (step / 2);
        const label = periods[idx]?.short || periods[idx]?.label || "";
        return `<line x1="${x.toFixed(1)}" y1="${plotBottom}" x2="${x.toFixed(1)}" y2="${(plotBottom + 4).toFixed(1)}" stroke="#4b5c6e" stroke-width="1" />`
             + `<text x="${x.toFixed(1)}" y="${(plotBottom + 16).toFixed(1)}" text-anchor="middle" fill="#4b5c6e" font-family="'IBM Plex Mono',monospace" font-size="9">${label}</text>`;
    }).join("");

    // Baseline axis line
    const axisLine = `<line x1="${pad.left}" y1="${plotBottom}" x2="${W - pad.right}" y2="${plotBottom}" stroke="rgba(126,142,163,0.2)" stroke-width="1" />`;

    // Peak marker
    const peakIdx = values.indexOf(Math.max(...values));
    const peakPt = points[peakIdx];
    const peakMarker = peakPt ? `<circle cx="${peakPt.x.toFixed(1)}" cy="${peakPt.y.toFixed(1)}" r="2.5" fill="#7ab0e0" />
        <text x="${peakPt.x.toFixed(1)}" y="${(peakPt.y - 8).toFixed(1)}" text-anchor="middle" fill="#7c8d9f" font-family="'IBM Plex Mono',monospace" font-size="9">${fmtY(max)}</text>` : "";

    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
        ${grid}
        ${axisLine}
        ${yLabels}
        ${xLabels}
        ${bars}
        ${peakMarker}
    </svg>`;
}

function pickXIndices(n, count) {
    if (n <= count) return Array.from({ length: n }, (_, i) => i);
    const indices = [0];
    for (let i = 1; i < count - 1; i++) {
        indices.push(Math.round(i * (n - 1) / (count - 1)));
    }
    indices.push(n - 1);
    return [...new Set(indices)];
}

function niceNum(val) {
    const exp = Math.floor(Math.log10(Math.max(val, 1)));
    const frac = val / Math.pow(10, exp);
    let nice;
    if (frac <= 1.5) nice = 1.5;
    else if (frac <= 3) nice = 3;
    else if (frac <= 5) nice = 5;
    else if (frac <= 7) nice = 7;
    else nice = 10;
    return nice * Math.pow(10, exp);
}

/* ─── Render: List Panel ─── */

function renderList() {
    const panel = document.getElementById("list-panel");
    panel.replaceChildren();

    const items = getListItems();
    const isRecent = state.view.mode === "recent";

    // Header
    const header = el("div", "list-header");
    const summary = el("span", "list-summary");
    if (isRecent) {
        summary.textContent = `${items.length} recent filings`;
    } else {
        summary.textContent = `${items.length} tags · ${state.view.window} vs ${compareConfig().label}`;
    }
    header.appendChild(summary);

    const controls = el("div", "list-actions");
    if (!isRecent) {
        const sort = document.createElement("select");
        sort.className = "list-sort";
        sort.setAttribute("aria-label", "Sort signal list");
        const opts = [
            ["impact", "Impact"],
            ["count", "Mentions"],
            ["yoy", "YoY Δ"],
            ["prev", "Prior Δ"],
            ["name", "Name"]
        ];
        for (const [value, label] of opts) {
            const opt = document.createElement("option");
            opt.value = value;
            opt.textContent = label;
            sort.appendChild(opt);
        }
        sort.value = state.view.sort;
        sort.onchange = e => setSort(e.target.value);
        controls.appendChild(sort);
    }

    if (state.view.query || state.view.minCount !== 1 || state.view.compare !== "yoy") {
        const reset = el("button", "list-reset", "Reset");
        reset.type = "button";
        reset.onclick = resetFilters;
        controls.appendChild(reset);
    }
    if (controls.childElementCount) header.appendChild(controls);
    panel.appendChild(header);

    // Empty state
    if (!items.length) {
        const empty = el("div", "list-empty");
        empty.appendChild(el("div", "", isRecent ? "No filings match current filters." : "No signals match current filters."));
        const btn = el("button", "", "Reset filters");
        btn.type = "button";
        btn.onclick = resetFilters;
        empty.appendChild(btn);
        panel.appendChild(empty);
        return;
    }

    const topicSeries = state.timeseries?.topic_series || {};

    for (const item of items) {
        if (isRecent) {
            renderFilingListRow(panel, item);
        } else {
            renderSignalListRow(panel, item, topicSeries);
        }
    }
}

function renderSignalListRow(panel, item, topicSeries) {
    const delta = signalDelta(item);
    const dir = delta > 0.01 ? "up" : delta < -0.01 ? "down" : "flat";

    const row = el("div", "signal-row");
    if (state.selected?.kind === "signal" && state.selected?.id === item.id) row.classList.add("active");
    bindAction(row, () => selectSignal(item), `Open signal ${item.name}`);

    // Top line: name + sparkline
    const top = el("div", "signal-top");
    top.appendChild(el("span", "signal-name", displayName(item)));

    if (item.mode === "topics" && topicSeries[item.name]) {
        const color = dir === "up" ? "#3da85c" : dir === "down" ? "#cf5454" : "#4b5c6e";
        top.appendChild(createSparkBars(topicSeries[item.name], color));
    }

    row.appendChild(top);

    // Bottom line: delta + count + confidence
    const bottom = el("div", "signal-bottom");
    bottom.appendChild(el("span", `signal-delta ${dir}`, fmt.pp(delta)));
    bottom.appendChild(el("span", "signal-count", `${fmt.num(item.count)} mentions`));
    bottom.appendChild(el("span", "signal-conf", confidenceLabel(item)));
    row.appendChild(bottom);

    panel.appendChild(row);
}

function renderFilingListRow(panel, item) {
    const row = el("div", "filing-row");
    if (state.selected?.kind === "filing" && state.selected?.id === item.id) row.classList.add("active");
    bindAction(row, () => selectFiling(item), `Open filing ${item.client || "Unknown client"}`);

    const top = el("div", "filing-top");
    top.appendChild(el("span", "filing-client", item.client || "Unknown client"));
    top.appendChild(el("span", "filing-date", fmt.dateShort(item.date)));
    row.appendChild(top);

    row.appendChild(el("div", "filing-meta", `${item.registrant || "\u2014"} \u00b7 ${item.domain || "\u2014"}`));
    panel.appendChild(row);
}

/* ─── Render: Detail Panel ─── */

function renderDetail() {
    const panel = document.getElementById("detail-panel");
    panel.replaceChildren();

    if (!state.selected) {
        const empty = el("div", "detail-empty");
        empty.textContent = state.view.mode === "recent"
            ? "Select a filing to view details"
            : "Select a signal to view details";
        panel.appendChild(empty);
        return;
    }

    if (state.selected.kind === "signal") {
        renderSignalDetail(panel, state.selected.data);
    } else if (state.selected.kind === "filing") {
        renderFilingDetail(panel, state.selected.data);
    }
}

function renderSignalDetail(panel, signal) {
    const inner = el("div", "detail-inner");
    const meta = CATEGORY[signal.mode];

    // Head
    const head = el("div", "detail-head");
    const topline = el("div", "detail-topline");
    topline.appendChild(el("span", `detail-badge ${meta.badgeClass}`, meta.label));
    const conf = el("span", `detail-conf ${confidenceClass(signal)}`);
    conf.textContent = `${confidenceLabel(signal)} confidence`;
    topline.appendChild(conf);
    head.appendChild(topline);

    head.appendChild(el("h2", "detail-name", displayName(signal)));
    head.appendChild(el("div", "detail-subtitle", `${state.view.window} window vs ${compareConfig().label}`));
    inner.appendChild(head);

    const note = el("div", "method-note");
    note.textContent = "Source: Senate LDA filings. Mentions are activity-level tags, not unique filings; comparisons are directional signals, not causal claims.";
    inner.appendChild(note);

    // Stats grid
    const delta = signalDelta(signal);
    const deltaDir = delta > 0.01 ? "up" : delta < -0.01 ? "down" : "";
    const stats = el("div", "detail-stats");
    const statItems = [
        { value: fmt.num(signal.count),                label: "Mentions",   cls: "" },
        { value: fmt.pct(signal.currentShare),         label: "Share",      cls: "" },
        { value: fmt.pp(delta),                        label: "Delta",      cls: deltaDir },
        { value: fmt.num(signalBaselineCount(signal)),  label: "Baseline",   cls: "" },
        { value: fmt.pct(signalBaselineShare(signal)),  label: "Base share", cls: "" },
        { value: confidenceLabel(signal),               label: "Confidence", cls: "" }
    ];
    for (const s of statItems) {
        const stat = el("div", "detail-stat");
        stat.appendChild(el("span", `detail-stat-value ${s.cls}`.trim(), s.value));
        stat.appendChild(el("span", "detail-stat-label", s.label));
        stats.appendChild(stat);
    }
    inner.appendChild(stats);


    const topicSeries = state.timeseries?.topic_series || {};
    const quarters = state.timeseries?.quarters || [];
    const series = topicSeries[signal.name];
    const context = getQuarterContext();
    const contextNote = context?.reporting_note || "Each point is a report quarter from filing metadata.";

    if (signal.mode === "topics") {
        appendQuarterContext(inner, series || []);
        if (series && series.length > 1 && quarters.length > 1) {
            appendChart(
                inner,
                "Mentions by report quarter",
                series,
                quarters,
                { percent: false, note: contextNote }
            );
        } else {
            inner.appendChild(el("div", "detail-chart-missing", "No report-quarter series is available for this topic."));
        }
    }

    // Clients + Evidence in two columns
    const hasClients = signal.topClients.length > 0;
    const hasExamples = signal.examples.length > 0;

    if (hasClients || hasExamples) {
        const columns = el("div", (hasClients && hasExamples) ? "detail-columns" : "");

        if (hasClients) {
            const section = el("div", "detail-section");
            section.appendChild(el("div", "detail-section-title", `Top clients (${signal.topClients.length})`));
            const list = el("div", "client-list");
            for (const c of signal.topClients.slice(0, 10)) {
                const item = el("div", "client-item clickable");
                item.textContent = c;
                bindAction(item, () => openClient(c), `Filter filings for client ${c}`);
                list.appendChild(item);
            }
            section.appendChild(list);
            columns.appendChild(section);
        }

        if (hasExamples) {
            const section = el("div", "detail-section");
            section.appendChild(el("div", "detail-section-title", `Example filings (${signal.examples.length})`));
            for (const ex of signal.examples.slice(0, 8)) {
                const item = el("div", "evidence-item");
                bindAction(item, () => selectFilingFromExample(ex), `Open example filing ${ex.client || "Unknown client"}`);

                const top = el("div", "evidence-top");
                top.appendChild(el("span", "evidence-client", ex.client || "Unknown"));
                top.appendChild(el("span", "evidence-date", fmt.dateShort(ex.date)));
                item.appendChild(top);

                if (ex.registrant) {
                    item.appendChild(el("div", "evidence-registrant", ex.registrant));
                }
                section.appendChild(item);
            }
            columns.appendChild(section);
        }

        inner.appendChild(columns);
    }

    panel.appendChild(inner);
}

function renderFilingDetail(panel, filing) {
    const inner = el("div", "detail-inner");

    // Head
    const head = el("div", "detail-head");
    const topline = el("div", "detail-topline");
    topline.appendChild(el("span", "detail-badge filing", "Filing"));
    head.appendChild(topline);

    head.appendChild(el("h2", "detail-name", filing.client || "Filing"));
    head.appendChild(el("div", "detail-subtitle",
        `${filing.registrant || "Unknown registrant"} \u00b7 ${fmt.dateLong(filing.date)}`));
    inner.appendChild(head);

    // Stats
    const stats = el("div", "detail-stats");
    const tagsCount = (filing.topics || []).length + (filing.entities || []).length + (filing.legislation || []).length;
    const statItems = [
        { value: fmt.dateLong(filing.date),                                     label: "Filed" },
        { value: filing.domain || "\u2014",                                     label: "Domain" },
        { value: filing.year && filing.quarter ? `${filing.year} Q${filing.quarter}` : "\u2014", label: "Quarter" },
        { value: filing.registrant || "\u2014",                                 label: "Registrant" },
        { value: String(tagsCount),                                             label: "Tags" },
        { value: filing.income ? fmt.money(filing.income) : "\u2014",           label: "Income" }
    ];
    for (const s of statItems) {
        const stat = el("div", "detail-stat");
        stat.appendChild(el("span", "detail-stat-value", s.value));
        stat.appendChild(el("span", "detail-stat-label", s.label));
        stats.appendChild(stat);
    }
    inner.appendChild(stats);

    // Tags
    const tags = [
        ...(filing.domain ? [{ kind: "domains", value: filing.domain }] : []),
        ...(filing.topics || []).slice(0, 15).map(x => ({ kind: "topics", value: x })),
        ...(filing.entities || []).slice(0, 15).map(x => ({ kind: "entities", value: x })),
        ...(filing.legislation || []).slice(0, 15).map(x => ({ kind: "legislation", value: x }))
    ];

    if (tags.length) {
        const section = el("div", "detail-section");
        section.appendChild(el("div", "detail-section-title", "Extracted tags \u2014 click to view trend"));
        const wrap = el("div", "tag-wrap");
        for (const t of tags) {
            const tag = el("span", "topic-tag", t.value);
            bindAction(tag, () => openTag(t.kind, t.value), `Open trend for ${t.value}`);
            wrap.appendChild(tag);
        }
        section.appendChild(wrap);
        inner.appendChild(section);
    } else {
        const section = el("div", "detail-section");
        section.appendChild(el("div", "detail-section-title", "No extracted tags"));
        inner.appendChild(section);
    }

    panel.appendChild(inner);
}


function appendChart(container, label, values, periods, options = {}) {
    const chart = el("div", "detail-chart");
    chart.appendChild(el("div", "detail-chart-label", label));
    if (options.note) chart.appendChild(el("div", "detail-chart-note", options.note));
    const box = el("div", "detail-chart-box");
    box.innerHTML = drawDetailChart(values, periods, options);
    chart.appendChild(box);
    container.appendChild(chart);
}

function seriesTailSum(values, start, end) {
    if (!values || !values.length) return 0;
    return values.slice(start, end).reduce((sum, v) => sum + toNum(v), 0);
}

function pctDelta(current, baseline) {
    if (!baseline) return null;
    return ((current - baseline) / baseline) * 100;
}

function appendQuarterContext(container, series = []) {
    const context = getQuarterContext();
    if (!context) return;

    const wrap = el("div", "detail-section cadence-context");
    wrap.appendChild(el("div", "detail-section-title", "Quarterly reporting context"));

    const pills = el("div", "cadence-pills");
    const range = el("div", "cadence-pill");
    range.appendChild(el("span", "cadence-pill-value", `${context.start_label || "—"} → ${context.end_label || "—"}`));
    range.appendChild(el("span", "cadence-pill-label", `${context.period_count || 0} quarters in view`));
    pills.appendChild(range);

    const median = el("div", "cadence-pill");
    median.appendChild(el("span", "cadence-pill-value", fmt.num(context.quarterly_filings_median)));
    median.appendChild(el("span", "cadence-pill-label", "median filings / quarter"));
    pills.appendChild(median);

    const peak = el("div", "cadence-pill");
    peak.appendChild(el("span", "cadence-pill-value", fmt.num(context.quarterly_filings_max)));
    peak.appendChild(el("span", "cadence-pill-label", "max filings / quarter"));
    pills.appendChild(peak);

    wrap.appendChild(pills);

    if (series.length >= 8) {
        const topicLatest4 = seriesTailSum(series, -4);
        const topicPrior4 = seriesTailSum(series, -8, -4);
        const topicDelta = pctDelta(topicLatest4, topicPrior4);

        const agg = el("div", "cadence-pills");
        const a = el("div", "cadence-pill");
        a.appendChild(el("span", "cadence-pill-value", fmt.num(topicLatest4)));
        a.appendChild(el("span", "cadence-pill-label", "topic mentions in latest 4Q"));
        agg.appendChild(a);

        const b = el("div", "cadence-pill");
        b.appendChild(el("span", "cadence-pill-value", fmt.num(topicPrior4)));
        b.appendChild(el("span", "cadence-pill-label", "topic mentions in prior 4Q"));
        agg.appendChild(b);

        const c = el("div", "cadence-pill");
        c.appendChild(el("span", "cadence-pill-value", topicDelta == null ? "—" : `${topicDelta > 0 ? "+" : ""}${topicDelta.toFixed(1)}%`));
        c.appendChild(el("span", "cadence-pill-label", "latest 4Q vs prior 4Q"));
        agg.appendChild(c);

        wrap.appendChild(agg);
    }

    const topQuarter = context.top_report_quarters?.[0];
    if (topQuarter?.label) {
        wrap.appendChild(el("div", "detail-chart-note", `Peak filing quarter in view: ${topQuarter.label} (${fmt.num(topQuarter.filings)} filings).`));
    }

    container.appendChild(wrap);
}

/* ─── Selection ─── */

function selectSignal(signal) {
    state.selected = { kind: "signal", id: signal.id, data: signal };
    renderList();
    renderDetail();
}

function selectFiling(filing) {
    state.selected = { kind: "filing", id: filing.id, data: filing };
    renderList();
    renderDetail();
}

function selectFilingFromExample(example) {
    const filing = (state.filings || []).find(x => x.id === example.id) || {
        id: example.id, date: example.date, client: example.client,
        registrant: example.registrant, domain: null,
        topics: [], entities: [], legislation: []
    };
    updateModeControls("recent");
    state.view.mode = "recent";
    state.selected = { kind: "filing", id: filing.id, data: filing };
    renderList();
    renderDetail();
}

/* ─── Cross-navigation ─── */

function openTag(kind, value) {
    const mode = kind === "domains" ? "domains" : kind;
    if (!MODES[mode]) return;
    updateModeControls(mode);
    state.view.mode = mode;
    state.view.query = "";
    document.getElementById("nav-search").value = "";

    const found = signalItems(mode, state.view.window).find(x => x.name === value);
    const signal = found || {
        id: `${mode}:${state.view.window}:${value}:fallback`, mode, name: value,
        count: 0, prevCount: 0, yoyCount: 0,
        currentShare: 0, prevShare: 0, yoyShare: 0,
        shareDeltaPrev: 0, shareDeltaYoy: 0,
        confidence: "low", topClients: [], examples: []
    };
    state.selected = { kind: "signal", id: signal.id, data: signal };
    renderList();
    renderDetail();
}

/* ─── Client navigation ─── */

function openClient(clientName) {
    updateModeControls("recent");
    state.view.mode = "recent";
    state.view.query = clientName;
    document.getElementById("nav-search").value = clientName;
    const items = getListItems();
    if (items.length) {
        state.selected = { kind: "filing", id: items[0].id, data: items[0] };
    } else {
        state.selected = null;
    }
    renderList();
    renderDetail();
}

/* ─── Auto-select helper ─── */

function autoSelectFirst() {
    const items = getListItems();
    if (!items.length) {
        state.selected = null;
        return;
    }
    if (state.view.mode === "recent") {
        state.selected = { kind: "filing", id: items[0].id, data: items[0] };
    } else {
        state.selected = { kind: "signal", id: items[0].id, data: items[0] };
    }
}

function tryReselect() {
    const items = getListItems();
    if (!items.length) { state.selected = null; return; }
    if (state.view.mode === "recent") {
        const currentId = state.selected?.kind === "filing" ? state.selected.id : null;
        const found = currentId ? items.find(i => i.id === currentId) : null;
        const pick = found || items[0];
        state.selected = { kind: "filing", id: pick.id, data: pick };
        return;
    }
    const currentName = state.selected?.kind === "signal" ? state.selected?.data?.name : null;
    const found = currentName ? items.find(i => i.name === currentName) : null;
    const pick = found || items[0];
    state.selected = { kind: "signal", id: pick.id, data: pick };
}

/* ─── Controls ─── */

function updateModeControls(modeKey) {
    const sup = MODES[modeKey].supportsWindow;
    document.getElementById("window-toggle").style.display = sup ? "inline-flex" : "none";
    document.getElementById("compare-select").disabled  = !MODES[modeKey].isSignal;
    document.getElementById("volume-select").disabled   = !MODES[modeKey].isSignal;
    const sel = document.getElementById("mode-select");
    if (sel.value !== modeKey) sel.value = modeKey;
}

function setMode(modeKey) {
    if (!MODES[modeKey]) return;
    state.view.mode = modeKey;
    updateModeControls(modeKey);
    autoSelectFirst();
    renderList();
    renderDetail();
}

function setWindow(windowKey) {
    state.view.window = windowKey;
    document.querySelectorAll("#window-toggle .seg-btn").forEach(b =>
        b.classList.toggle("active", b.dataset.window === windowKey)
    );
    tryReselect();
    renderList();
    renderDetail();
}

function setCompare(v) {
    if (!COMPARE[v]) return;
    state.view.compare = v;
    tryReselect();
    renderList();
    renderDetail();
}

function setMinCount(v) {
    state.view.minCount = Number(v || 0);
    tryReselect();
    renderList();
    renderDetail();
}

function setSort(v) {
    if (!SORT[v]) return;
    state.view.sort = v;
    tryReselect();
    renderList();
    renderDetail();
}

function setQuery(q) {
    state.view.query = q || "";
    tryReselect();
    renderList();
    renderDetail();
}

function resetFilters() {
    state.view.compare = "yoy";
    state.view.minCount = 1;
    state.view.query = "";
    state.view.sort = "impact";
    document.getElementById("compare-select").value = "yoy";
    document.getElementById("volume-select").value  = "1";
    document.getElementById("nav-search").value     = "";
    tryReselect();
    renderList();
    renderDetail();
}

/* ─── Init ─── */

function renderLoading() {
    const list = document.getElementById("list-panel");
    const detail = document.getElementById("detail-panel");
    if (list) {
        list.replaceChildren();
        list.appendChild(el("div", "list-empty", "Loading dashboard data..."));
    }
    if (detail) {
        detail.replaceChildren();
        detail.appendChild(el("div", "detail-empty", "Loading dashboard data..."));
    }
}

async function init() {
    renderLoading();
    const [stats, trends, recent, timeseries] = await Promise.all([
        loadJSON("stats.json"),
        loadJSON("trends.json"),
        loadJSON("recent.json"),
        loadJSON("timeseries.json")
    ]);

    state.stats      = stats || null;
    state.trends     = trends || null;
    state.filings    = recent?.filings || [];
    state.timeseries = timeseries || null;

    const windows = Object.keys(state.trends?.topics || {});
    if (!windows.includes(state.view.window)) {
        state.view.window = windows.includes("90d") ? "90d" : (windows[0] || state.view.window);
    }

    // Update time
    const generatedAt = trends?.generated_at || stats?.generated_at;
    const meta = document.getElementById("last-updated");
    if (generatedAt) {
        meta.textContent = `Updated ${fmt.ago(generatedAt)}`;
        meta.title = new Date(generatedAt).toLocaleString();
    }

    // Bind controls
    document.getElementById("mode-select").onchange    = e => setMode(e.target.value);
    document.getElementById("compare-select").onchange = e => setCompare(e.target.value);
    document.getElementById("volume-select").onchange  = e => setMinCount(e.target.value);
    document.getElementById("nav-search").oninput      = e => setQuery(e.target.value);

    document.querySelectorAll("#window-toggle .seg-btn").forEach(b => {
        b.onclick = () => setWindow(b.dataset.window);
    });

    // Set initial control states
    document.getElementById("mode-select").value    = state.view.mode;
    document.getElementById("compare-select").value = state.view.compare;
    document.getElementById("volume-select").value  = String(state.view.minCount);
    document.querySelectorAll("#window-toggle .seg-btn").forEach(b =>
        b.classList.toggle("active", b.dataset.window === state.view.window)
    );
    updateModeControls(state.view.mode);

    // Auto-select first signal and render
    autoSelectFirst();
    renderList();
    renderDetail();
}

document.addEventListener("DOMContentLoaded", init);
