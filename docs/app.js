/* ══════════════════════════════════════════════
   Lobbying Signals — Editorial dashboard
   Single-page flow with command palette + drawer
   ══════════════════════════════════════════════ */

const DATA_PATH = "data";

const CATEGORIES = {
    topics:      { label: "Topic",   plural: "Topics",      tagClass: "topics",      shortLabel: "Topic" },
    entities:    { label: "Agency",  plural: "Agencies",    tagClass: "entities",    shortLabel: "Agency" },
    legislation: { label: "Bill",    plural: "Bills",       tagClass: "legislation", shortLabel: "Bill" },
    domains:     { label: "Domain",  plural: "Domains",     tagClass: "domains",     shortLabel: "Domain" }
};

const SIGNAL_MODES = ["topics", "entities", "legislation", "domains"];

const COMPARE = {
    yoy:  { baselineCountKey: "yoy_count",  baselineLabel: "year-ago period",  shortLabel: "vs year ago" },
    prev: { baselineCountKey: "prev_count", baselineLabel: "prior period",      shortLabel: "vs prior period" }
};

const DISPLAY_OVERRIDES = {
    entities: {
        "VA": "Department of Veterans Affairs",
        "EPA": "Environmental Protection Agency",
        "HHS": "Health and Human Services",
        "CMS": "Centers for Medicare & Medicaid Services",
        "FDA": "Food and Drug Administration",
        "FAA": "Federal Aviation Administration",
        "FCC": "Federal Communications Commission",
        "FTC": "Federal Trade Commission",
        "SEC": "Securities and Exchange Commission",
        "DHS": "Department of Homeland Security",
        "DOJ": "Department of Justice",
        "DOT": "Department of Transportation",
        "DOE": "Department of Energy",
        "OSHA": "Occupational Safety and Health Administration",
        "OMB": "Office of Management and Budget",
        "USDA": "Department of Agriculture",
        "USTR": "U.S. Trade Representative",
        "NIH": "National Institutes of Health",
        "CDC": "Centers for Disease Control"
    },
    legislation: {
        "H.R. 1": "H.R. 1 / One Big Beautiful Bill Act",
        "One Big Beautiful Bill Act": "H.R. 1 / One Big Beautiful Bill Act",
        "P.L. 119-21": "H.R. 1 / One Big Beautiful Bill Act"
    }
};

/* ─── State ─── */

const state = {
    trends: null,
    stats: null,
    filings: [],
    timeseries: null,
    clientIndex: new Map(),

    view: {
        window: "90d",
        compare: "yoy",
        cat: "all"   // all | topics | entities | legislation | domains | recent
    },

    drawer: null,        // current drawer view
    drawerStack: [],     // breadcrumb history within drawer
    palette: { open: false, query: "", focusIdx: 0, results: [] }
};

/* ─── Utilities ─── */

const fmt = {
    int: n => Math.round(Number(n) || 0).toLocaleString("en-US"),
    num: n => {
        const x = Number(n) || 0;
        if (x >= 1e6) return (x / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
        if (x >= 1e3) return (x / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
        return Math.round(x).toLocaleString("en-US");
    },
    pct: (n, digits = 2) => (n == null ? "—" : `${Number(n).toFixed(digits)}%`),
    pp: n => {
        if (n == null || Number.isNaN(Number(n))) return "—";
        const x = Number(n);
        return `${x > 0 ? "+" : ""}${x.toFixed(2)} pp`;
    },
    money: n => {
        const x = Number(n) || 0;
        if (x >= 1e9) return `$${(x / 1e9).toFixed(1)}B`;
        if (x >= 1e6) return `$${(x / 1e6).toFixed(1)}M`;
        if (x >= 1e3) return `$${(x / 1e3).toFixed(0)}K`;
        return `$${Math.round(x)}`;
    },
    dateShort: d => {
        const dt = parseDate(d);
        return dt ? dt.toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—";
    },
    dateLong: d => {
        const dt = parseDate(d);
        return dt ? dt.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) : "—";
    },
    ago: d => {
        const dt = parseDate(d);
        if (!dt) return "—";
        const m = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 60000));
        if (m < 60) return `${m}m ago`;
        if (m < 1440) return `${Math.floor(m / 60)}h ago`;
        return `${Math.floor(m / 1440)}d ago`;
    },
    titleCase: s => {
        if (!s) return "";
        return String(s).split(/\s+/).map(w => {
            if (w.length <= 2 && w === w.toUpperCase()) return w;
            return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
        }).join(" ");
    },
    esc: t => {
        const d = document.createElement("div");
        d.textContent = t == null ? "" : String(t);
        return d.innerHTML;
    }
};

function parseDate(value) {
    if (!value) return null;
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
        const [y, m, d] = value.split("-").map(Number);
        return new Date(y, m - 1, d);
    }
    const dt = new Date(value);
    return Number.isNaN(dt.getTime()) ? null : dt;
}

function dataAsOfDate() {
    return parseDate(state.stats?.date_range?.end) || parseDate(state.stats?.generated_at) || new Date();
}

function dateMinusDays(date, days) {
    const d = new Date(date.getTime());
    d.setDate(d.getDate() - days);
    return d;
}

function fmtMonthDay(d) {
    if (!d) return "—";
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function fmtMonthDayYear(d) {
    if (!d) return "—";
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function windowDates(windowKey) {
    const days = windowKey === "30d" ? 30 : 90;
    const end = dataAsOfDate();
    const start = dateMinusDays(end, days);
    return { start, end, days };
}

function baselineDates(windowKey, compareKey) {
    const { start, end, days } = windowDates(windowKey);
    if (compareKey === "yoy") {
        const yEnd = new Date(end.getTime()); yEnd.setFullYear(yEnd.getFullYear() - 1);
        const yStart = new Date(start.getTime()); yStart.setFullYear(yStart.getFullYear() - 1);
        return { start: yStart, end: yEnd, days };
    }
    // prev: the days immediately before the current window
    const pEnd = new Date(start.getTime() - 1);
    const pStart = dateMinusDays(pEnd, days - 1);
    return { start: pStart, end: pEnd, days };
}

function rangeLabel(windowKey) {
    const { start, end } = windowDates(windowKey);
    return `${fmtMonthDay(start)} – ${fmtMonthDayYear(end)}`;
}

function baselineRangeLabel(windowKey, compareKey) {
    const { start, end } = baselineDates(windowKey, compareKey);
    return `${fmtMonthDay(start)} – ${fmtMonthDayYear(end)}`;
}

function toNum(v) { const x = Number(v); return Number.isFinite(x) ? x : 0; }

function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
}

function displayName(mode, name) {
    if (!name) return "";
    return DISPLAY_OVERRIDES[mode]?.[name] || name;
}

function clientDisplay(name) {
    if (!name) return "";
    // Many client names come in ALL CAPS — gentler title case for readability
    if (name === name.toUpperCase() && name.length > 4) return fmt.titleCase(name);
    return name;
}

/* ─── Plain-English headlines ─── */

// Map magnitude → intensity suffix for color/weight scaling on delta text
function deltaIntensity(ratio) {
    if (ratio == null || !Number.isFinite(ratio)) return "-x";
    const r = ratio >= 1 ? ratio : (ratio === 0 ? Infinity : 1 / ratio);
    if (r >= 5)   return "-x";       // extreme (5x+ either direction)
    if (r >= 2)   return "-strong";  // strong (2x-5x)
    if (r >= 1.25) return "";        // normal
    return "-mild";                  // small (<25% change)
}

function buildHeadline(item, compareKey) {
    const baselineKey = COMPARE[compareKey].baselineCountKey;
    const current = toNum(item.count);
    const baseline = toNum(item[baselineKey]);
    const baselineLabel = COMPARE[compareKey].baselineLabel;

    const noun = current === 1 ? "mention" : "mentions";

    if (baseline === 0 && current > 0) {
        return {
            html: `<strong>${fmt.int(current)}</strong> ${noun} — <span class="delta delta-up-x">first time</span> in tracked ${baselineLabel}.`,
            dir: "up"
        };
    }
    if (current === 0 && baseline > 0) {
        return {
            html: `Quiet — <strong>0</strong> mentions vs <strong>${fmt.int(baseline)}</strong> in ${baselineLabel}.`,
            dir: "down"
        };
    }
    if (baseline === 0 && current === 0) {
        return { html: `No activity in this window.`, dir: "flat" };
    }

    const ratio = current / baseline;
    const pctChange = (ratio - 1) * 100;
    const intensity = deltaIntensity(ratio);

    if (ratio >= 2) {
        const xLabel = ratio >= 10 ? `${Math.round(ratio)}×` : `${ratio.toFixed(1)}×`;
        return {
            html: `<strong>${fmt.int(current)}</strong> ${noun} — <span class="delta delta-up${intensity}">${xLabel}</span> the ${baselineLabel} (${fmt.int(baseline)}).`,
            dir: "up"
        };
    }
    if (ratio <= 0.5) {
        const halfLabel = `${Math.round((1 - ratio) * 100)}% lower`;
        return {
            html: `<strong>${fmt.int(current)}</strong> ${noun} — <span class="delta delta-down${intensity}">${halfLabel}</span> than ${baselineLabel} (${fmt.int(baseline)}).`,
            dir: "down"
        };
    }
    if (Math.abs(pctChange) >= 5) {
        const sign = pctChange > 0 ? "+" : "";
        const dirCls = pctChange > 0 ? `delta-up${intensity}` : `delta-down${intensity}`;
        return {
            html: `<strong>${fmt.int(current)}</strong> ${noun} — <span class="delta ${dirCls}">${sign}${pctChange.toFixed(0)}%</span> vs ${baselineLabel} (${fmt.int(baseline)}).`,
            dir: pctChange > 0 ? "up" : "down"
        };
    }
    return {
        html: `<strong>${fmt.int(current)}</strong> ${noun} — <span class="delta delta-flat">steady</span> vs ${baselineLabel} (${fmt.int(baseline)}).`,
        dir: "flat"
    };
}

/* ─── Mover items ─── */

function getKeyMap(mode) {
    // trends.json key names: topic_*, entity_*, domain_*, legislation_*
    if (mode === "topics") return { c: "topic_clients", e: "topic_examples", i: "topic_income" };
    if (mode === "entities") return { c: "entity_clients", e: "entity_examples", i: "entity_income" };
    if (mode === "legislation") return { c: "legislation_clients", e: "legislation_examples", i: "legislation_income" };
    if (mode === "domains") return { c: "domain_clients", e: "domain_examples", i: "domain_income" };
    return null;
}

function getCategoryItemsFixed(mode, windowKey) {
    const meta = CATEGORIES[mode];
    const keys = getKeyMap(mode);
    if (!meta || !keys) return [];
    const items = state.trends?.[mode]?.[windowKey] || [];
    const clients = state.trends?.[keys.c]?.[windowKey] || {};
    const examples = state.trends?.[keys.e]?.[windowKey] || {};
    const income = state.trends?.[keys.i]?.[windowKey] || {};

    return items.map(item => ({
        mode,
        name: item.name,
        count: toNum(item.count),
        prev_count: toNum(item.prev_count),
        yoy_count: toNum(item.yoy_count),
        current_share_pct: toNum(item.current_share_pct),
        prev_share_pct: toNum(item.prev_share_pct),
        yoy_share_pct: toNum(item.yoy_share_pct),
        share_delta_prev_pp: toNum(item.share_delta_prev_pp),
        share_delta_yoy_pp: toNum(item.share_delta_yoy_pp),
        score: toNum(item.score),
        confidence: item.confidence || "medium",
        topClients: clients[item.name] || [],
        examples: examples[item.name] || [],
        income: toNum(income[item.name])
    }));
}

function canonicalKey(mode, name) {
    // Collapse aliased names (display overrides + raw) to one bucket.
    const display = displayName(mode, name).toLowerCase();
    // Bills like "H.R. 1" → "H.R. 1 / One Big Beautiful Bill Act" both collapse here.
    return `${mode}::${display}`;
}

function buildMovers(catFilter, windowKey, compareKey) {
    const modes = (catFilter === "all" || !catFilter) ? SIGNAL_MODES : [catFilter];
    const all = [];
    const seen = new Map();
    for (const mode of modes) {
        for (const it of getCategoryItemsFixed(mode, windowKey)) {
            const baselineKey = COMPARE[compareKey].baselineCountKey;
            const baseline = toNum(it[baselineKey]);
            const ratio = baseline === 0 ? (it.count > 0 ? 999 : 0) : it.count / baseline;
            const enriched = { ...it, _ratio: ratio, _baseline: baseline };

            // Dedupe by canonical display name; keep the higher-count entry
            const key = canonicalKey(mode, it.name);
            const existing = seen.get(key);
            if (!existing) {
                seen.set(key, enriched);
                all.push(enriched);
            } else if (enriched.count > existing.count) {
                // Replace existing with this one
                const idx = all.indexOf(existing);
                if (idx >= 0) all[idx] = enriched;
                seen.set(key, enriched);
            }
        }
    }
    // Sort by absolute share-delta (impact) — same idea as 'impact' before
    const deltaKey = compareKey === "yoy" ? "share_delta_yoy_pp" : "share_delta_prev_pp";
    all.sort((a, b) => Math.abs(b[deltaKey]) - Math.abs(a[deltaKey]));
    return all;
}

/* ─── Hero ─── */

function renderHero() {
    const headlineEl = document.getElementById("hero-headline");
    const statsEl = document.getElementById("hero-stats");
    if (!headlineEl || !statsEl) return;

    const stats = state.stats || {};
    const quarters = state.timeseries?.quarters || [];
    const partialN = partialTrailingCount(quarters);
    const latest = quarters[quarters.length - 1];
    // Quarter-over-quarter change compares the two most recent COMPLETE quarters;
    // a partial-vs-full comparison would read as a false collapse.
    const complete = partialN > 0 ? quarters.slice(0, quarters.length - partialN) : quarters;
    const cmpLatest = complete[complete.length - 1];
    const cmpPrev = complete[complete.length - 2];

    // Compose a one-line synthesis: pick top movers from different categories,
    // with positive deltas, deduped by canonical name.
    const allMovers = buildMovers("all", "90d", "yoy")
        .filter(m => m.share_delta_yoy_pp > 0 && m.count >= 100);
    const seenCats = new Set();
    const picks = [];
    for (const m of allMovers) {
        if (seenCats.has(m.mode)) continue;
        picks.push(m);
        seenCats.add(m.mode);
        if (picks.length === 3) break;
    }
    // Fall back to top-3 (still deduped) if we didn't find 3 distinct categories
    if (picks.length < 3) {
        for (const m of allMovers) {
            if (picks.includes(m)) continue;
            picks.push(m);
            if (picks.length === 3) break;
        }
    }
    const moverNames = picks.map(m => `<em>${fmt.esc(displayName(m.mode, m.name))}</em>`);

    let headline;
    if (moverNames.length >= 2) {
        const last = moverNames.pop();
        const head = moverNames.length === 1
            ? `${moverNames[0]} and ${last}`
            : `${moverNames.join(", ")}, and ${last}`;
        headline = `${head} are gaining ground in federal lobbying activity.`;
    } else if (moverNames.length === 1) {
        headline = `${moverNames[0]} is the biggest emerging signal in federal lobbying.`;
    } else {
        headline = `Tracking ${fmt.num(stats.total_filings || 0)} federal lobbying filings.`;
    }
    headlineEl.innerHTML = headline;

    // Stats line
    statsEl.replaceChildren();
    const statItems = [];

    statItems.push({
        value: fmt.num(stats.total_filings || 0),
        label: "filings tracked"
    });
    statItems.push({
        value: fmt.num(stats.total_extracted || stats.total_activities || 0),
        label: "activity tags"
    });
    if (latest) {
        const isPartial = partialN > 0 && latest === quarters[quarters.length - 1];
        statItems.push({
            value: `${latest.year} Q${latest.quarter}`,
            label: isPartial
                ? `${fmt.num(latest.filings)} filings so far · partial quarter`
                : `${fmt.num(latest.filings)} filings · ${fmt.money(latest.income)}`
        });
    }
    if (cmpLatest && cmpPrev) {
        const change = ((cmpLatest.filings - cmpPrev.filings) / cmpPrev.filings) * 100;
        const dir = change > 0 ? "up" : change < 0 ? "down" : "";
        const sign = change > 0 ? "↑ " : change < 0 ? "↓ " : "";
        statItems.push({
            value: `${sign}${Math.abs(change).toFixed(1)}%`,
            label: `${cmpLatest.year} Q${cmpLatest.quarter} vs ${cmpPrev.year} Q${cmpPrev.quarter}`,
            trend: dir
        });
    }

    for (const s of statItems) {
        const li = el("li");
        const v = el("span", `stat-value ${s.trend ? `stat-trend ${s.trend}` : ""}`.trim(), s.value);
        const l = el("span", "stat-label", s.label);
        li.appendChild(v);
        li.appendChild(l);
        statsEl.appendChild(li);
    }
}

/* ─── Movers feed ─── */

function renderMovers() {
    const list = document.getElementById("mover-list");
    const sub = document.getElementById("movers-sub");
    if (!list || !sub) return;
    list.replaceChildren();

    const cat = state.view.cat;
    const win = state.view.window;
    const cmp = state.view.compare;

    if (cat === "recent") {
        sub.textContent = `${state.filings.length} latest filings, most recent first`;
        renderRecentList(list);
        return;
    }

    const movers = buildMovers(cat, win, cmp).slice(0, 50);
    const catLabel = cat === "all" ? "across topics, agencies, bills, and domains" : `in ${CATEGORIES[cat].plural.toLowerCase()}`;
    const winLabel = win === "90d" ? "last 90 days" : "last 30 days";
    sub.innerHTML = `Top movers ${fmt.esc(catLabel)} — <span class="window-range">${fmt.esc(winLabel)}</span> <span class="window-range-dates">(${fmt.esc(rangeLabel(win))})</span> ${fmt.esc(COMPARE[cmp].shortLabel)} <span class="window-range-dates">(${fmt.esc(baselineRangeLabel(win, cmp))})</span>`;

    if (!movers.length) {
        const empty = el("div", "mover-empty");
        empty.textContent = "No signals match this window.";
        list.appendChild(empty);
        return;
    }

    for (const m of movers) {
        list.appendChild(buildMoverCard(m, cmp));
    }
}

function renderRecentList(list) {
    const filings = state.filings.slice(0, 60);
    if (!filings.length) {
        const empty = el("div", "mover-empty", "No recent filings available.");
        list.appendChild(empty);
        return;
    }
    for (const f of filings) {
        list.appendChild(buildFilingCard(f));
    }
}

function buildMoverCard(m, compareKey) {
    const card = el("li", "mover");
    card.setAttribute("role", "button");
    card.tabIndex = 0;
    card.setAttribute("aria-label", `Open ${CATEGORIES[m.mode].label} ${displayName(m.mode, m.name)}`);
    card.onclick = () => openSignal(m.mode, m.name);
    card.onkeydown = e => {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openSignal(m.mode, m.name);
        }
    };

    // Category tag as its own grid column (left)
    card.appendChild(el("span", `cat-tag ${CATEGORIES[m.mode].tagClass}`, CATEGORIES[m.mode].label));

    const main = el("div", "mover-main");
    main.appendChild(el("div", "mover-name", displayName(m.mode, m.name)));

    const head = buildHeadline(m, compareKey);
    const headlineEl = el("div", "mover-headline");
    headlineEl.innerHTML = head.html;
    main.appendChild(headlineEl);

    if (m.topClients?.length) {
        const cli = el("div", "mover-clients");
        const display = m.topClients.slice(0, 3).map(c => clientDisplay(c));
        const remaining = m.topClients.length - 3;
        const parts = display.map(d => `<span class="pivot" data-client="${fmt.esc(d)}">${fmt.esc(d)}</span>`);
        const tail = remaining > 0 ? ` · +${remaining} more` : "";
        cli.innerHTML = parts.join(" · ") + tail;
        cli.querySelectorAll(".pivot").forEach(node => {
            node.addEventListener("click", e => {
                e.stopPropagation();
                openClient(node.dataset.client);
            });
        });
        main.appendChild(cli);
    }

    card.appendChild(main);

    // Universal trajectory chart: line of values over time with the
    // current period and baseline period highlighted as colored bands.
    const trendSlot = el("div", "mover-trend");
    const accent = head.dir === "up"
        ? getCSSVar("--up", "#1f7a4d")
        : head.dir === "down"
            ? getCSSVar("--down", "#b53a3a")
            : getCSSVar("--accent", "#b8420f");
    trendSlot.innerHTML = makeTrendChart(m, m.mode, state.view.window, compareKey, { accent });
    card.appendChild(trendSlot);

    const arrow = el("div", "mover-arrow", "→");
    card.appendChild(arrow);

    return card;
}

function buildFilingCard(f) {
    const card = el("li", "mover filing-card");
    card.setAttribute("role", "button");
    card.tabIndex = 0;
    card.onclick = () => openFiling(f.id);
    card.onkeydown = e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFiling(f.id); }
    };

    card.appendChild(el("span", "cat-tag filing", "Filing"));

    const main = el("div", "mover-main");
    main.appendChild(el("div", "mover-name", clientDisplay(f.client) || "Unknown client"));

    const tags = [
        ...(f.topics || []).slice(0, 2),
        ...(f.entities || []).slice(0, 2),
        ...(f.legislation || []).slice(0, 2)
    ];
    const headlineHtml = `${f.registrant ? `<strong>${fmt.esc(clientDisplay(f.registrant))}</strong> · ` : ""}${tags.length ? tags.map(t => fmt.esc(t)).join(" · ") : "No tags extracted"}`;
    const headlineEl = el("div", "mover-headline");
    headlineEl.innerHTML = headlineHtml;
    main.appendChild(headlineEl);

    card.appendChild(main);

    const meta = el("div", "mover-meta");
    meta.appendChild(el("span", "mover-meta-line", fmt.dateShort(f.date)));
    if (f.income) {
        meta.appendChild(el("span", "mover-meta-line", fmt.money(f.income)));
    } else if (f.year && f.quarter) {
        meta.appendChild(el("span", "mover-meta-line", `${f.year} Q${f.quarter}`));
    }
    card.appendChild(meta);

    card.appendChild(el("div", "mover-arrow", "→"));
    return card;
}

/* ─── Sparklines ─── */

function getCSSVar(name, fallback) {
    if (typeof window === "undefined") return fallback;
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
}

function makeSparkBars(values, color) {
    const w = 120, h = 38;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("preserveAspectRatio", "none");
    if (!values?.length) return svg;
    const max = Math.max(...values, 1);
    const step = w / values.length;
    const barW = Math.max(1.5, step - 1.4);
    for (let i = 0; i < values.length; i++) {
        const v = toNum(values[i]);
        const barH = Math.max(1, (v / max) * (h - 2));
        const rect = document.createElementNS(ns, "rect");
        rect.setAttribute("x", (i * step + (step - barW) / 2).toFixed(2));
        rect.setAttribute("y", (h - 1 - barH).toFixed(2));
        rect.setAttribute("width", barW.toFixed(2));
        rect.setAttribute("height", barH.toFixed(2));
        rect.setAttribute("rx", "1");
        rect.setAttribute("fill", color);
        rect.setAttribute("fill-opacity", i === values.length - 1 ? "1" : "0.55");
        svg.appendChild(rect);
    }
    return svg;
}

/* ─── Trajectory chart (line over time, current + baseline highlighted) ─── */

function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

/* Count trailing report quarters that are still filling in. LDA filings for a
   period keep arriving for weeks after it closes, so the newest quarter is
   under-counted — comparing or charting it against full quarters reads as a
   crash. A quarter counts as partial when its filing volume falls well below a
   typical full quarter (median of the series). */
function partialTrailingCount(quarters) {
    if (!quarters || quarters.length < 2) return 0;
    const ctx = state.timeseries?.context;
    const full = ctx?.quarterly_filings_median
        || [...quarters.map(q => toNum(q.filings))].sort((a, b) => a - b)[Math.floor(quarters.length / 2)]
        || 0;
    if (full <= 0) return 0;
    const threshold = full * 0.5;
    let count = 0;
    for (let i = quarters.length - 1; i >= 0; i--) {
        if (toNum(quarters[i].filings) < threshold) count++;
        else break;
    }
    return Math.min(count, quarters.length - 1); // never flag every quarter
}

function buildTrendSeries(item, mode, windowKey) {
    const today = dataAsOfDate();
    const todayMs = today.getTime();
    const days = windowKey === "30d" ? 30 : 90;
    const DAY_MS = 86400000;

    if (mode === "topics" && state.timeseries?.topic_series?.[item.name]?.length && state.timeseries.quarters?.length) {
        const qs = state.timeseries.quarters;
        const vals = state.timeseries.topic_series[item.name];
        // Drop trailing partial quarters so the line ends on a complete quarter
        // instead of cliff-diving into an under-reported one.
        const end = qs.length - partialTrailingCount(qs);
        if (end >= 2) {
            // Last 8 quarters keeps both compared windows visible at meaningful scale
            const start = Math.max(0, end - 8);
            return qs.slice(start, end).map((q, i) => ({
                x: new Date(q.year, (q.quarter - 1) * 3 + 1, 15).getTime(),
                y: toNum(vals[start + i])
            }));
        }
        // Too few complete quarters to chart — fall through to window trajectory.
    }
    // Universal fallback: 3-point trajectory across the full year of data we have
    const yoyMid = todayMs - 365 * DAY_MS - (days / 2) * DAY_MS;
    const prevMid = todayMs - (days * 1.5) * DAY_MS;
    const nowMid = todayMs - (days / 2) * DAY_MS;
    return [
        { x: yoyMid,  y: toNum(item.yoy_count) },
        { x: prevMid, y: toNum(item.prev_count) },
        { x: nowMid,  y: toNum(item.count) }
    ];
}

function periodBands(windowKey, compareKey) {
    const today = dataAsOfDate();
    const todayMs = today.getTime();
    const DAY_MS = 86400000;
    const days = windowKey === "30d" ? 30 : 90;
    const currentEnd = todayMs;
    const currentStart = currentEnd - days * DAY_MS;
    let baseStart, baseEnd;
    if (compareKey === "yoy") {
        baseEnd = currentEnd - 365 * DAY_MS;
        baseStart = currentStart - 365 * DAY_MS;
    } else {
        baseEnd = currentStart - 1;
        baseStart = baseEnd - days * DAY_MS;
    }
    return { currentStart, currentEnd, baseStart, baseEnd };
}

function makeTrendChart(item, mode, windowKey, compareKey, options = {}) {
    const W = 200, H = 48;
    const padL = 4, padR = 36, padT = 5, padB = 12;

    const accent = options.accent || getCSSVar("--accent", "#b8420f");
    const muted  = getCSSVar("--ink-4", "#a39c87");
    const labelColor = getCSSVar("--ink-3", "#7a7565");
    const valueColor = getCSSVar("--ink-2", "#4a4a4a");

    const series = buildTrendSeries(item, mode, windowKey);
    const { currentStart, currentEnd, baseStart, baseEnd } = periodBands(windowKey, compareKey);

    const xMin = Math.min(series[0].x, baseStart);
    const xMax = currentEnd;
    const yMax = Math.max(...series.map(p => p.y), 1);
    const xRange = Math.max(1, xMax - xMin);
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const xS = x => padL + ((x - xMin) / xRange) * plotW;
    const yS = y => padT + (1 - y / yMax) * plotH;

    // ─ Period bands (background)
    const bandTop = padT - 1;
    const bandH = plotH + 1;
    const xLeft = padL, xRight = padL + plotW;
    const baseX  = clamp(xS(baseStart),  xLeft, xRight);
    const baseR  = clamp(xS(baseEnd),    xLeft, xRight);
    const baseW  = Math.max(2, baseR - baseX);
    const currX  = clamp(xS(currentStart), xLeft, xRight);
    const currR  = clamp(xS(currentEnd),   xLeft, xRight);
    const currW  = Math.max(2, currR - currX);
    const bands = `
        <rect x="${baseX.toFixed(1)}" y="${bandTop}" width="${baseW.toFixed(1)}" height="${bandH}" fill="${muted}" fill-opacity="0.10"/>
        <rect x="${currX.toFixed(1)}" y="${bandTop}" width="${currW.toFixed(1)}" height="${bandH}" fill="${accent}" fill-opacity="0.14"/>
    `;

    // ─ Area + line
    const ground = (padT + plotH).toFixed(1);
    const linePts = series.map(p => `${xS(p.x).toFixed(1)},${yS(p.y).toFixed(1)}`);
    const area = `<polygon points="${xS(series[0].x).toFixed(1)},${ground} ${linePts.join(" ")} ${xS(series[series.length-1].x).toFixed(1)},${ground}" fill="${accent}" fill-opacity="0.07"/>`;
    const line = `<polyline points="${linePts.join(" ")}" fill="none" stroke="${accent}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>`;

    // ─ Latest point: halo + dot + value label
    const last = series[series.length - 1];
    const lx = xS(last.x), ly = yS(last.y);
    const halo = `<circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="5.5" fill="${accent}" fill-opacity="0.18"/>`;
    const dot  = `<circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="2.4" fill="${accent}"/>`;
    const valY = clamp(ly + 3.5, padT + 8, padT + plotH - 1);
    const valLabel = `<text x="${(W - 2).toFixed(1)}" y="${valY.toFixed(1)}" text-anchor="end" font-family="JetBrains Mono,monospace" font-size="10" font-weight="600" fill="${valueColor}">${fmt.num(last.y)}</text>`;

    // ─ Period labels
    const labelY = H - 2;
    const baseLabelX = (baseX + baseR) / 2;
    const currLabelX = (currX + currR) / 2;
    const baseLabel = compareKey === "yoy" ? "yr ago" : "prior";
    const labels = `
        <text x="${baseLabelX.toFixed(1)}" y="${labelY}" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="8" fill="${labelColor}" letter-spacing="0.05em">${baseLabel}</text>
        <text x="${currLabelX.toFixed(1)}" y="${labelY}" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="8" fill="${accent}" font-weight="500" letter-spacing="0.05em">now</text>
    `;

    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" class="trend-svg">${bands}${area}${line}${halo}${dot}${valLabel}${labels}</svg>`;
}

/* ─── Detail chart ─── */

function makeBarChart(values, periods, options = {}) {
    const W = 540, H = 180;
    const pad = { top: 10, right: 10, bottom: 28, left: 38 };
    const max = Math.max(...values, 1);
    const niceMax = niceNum(max);
    const ticks = [0, niceMax * 0.5, niceMax];
    const plotBottom = H - pad.bottom;
    const plotTop = pad.top;
    const plotW = W - pad.left - pad.right;
    const plotH = plotBottom - plotTop;
    const fmtY = options.percent ? v => `${v.toFixed(v >= 10 ? 0 : 1)}%` : v => fmt.num(v);

    const grid = ticks.map(v => {
        const y = plotTop + (1 - v / niceMax) * plotH;
        return `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="currentColor" stroke-opacity="0.08" stroke-width="1" />`;
    }).join("");

    const yLabels = ticks.map(v => {
        const y = plotTop + (1 - v / niceMax) * plotH;
        return `<text x="${pad.left - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-family="JetBrains Mono,monospace" font-size="9" fill="currentColor" fill-opacity="0.55">${fmtY(v)}</text>`;
    }).join("");

    const n = values.length;
    const step = n > 0 ? plotW / n : plotW;
    const barW = Math.max(2, Math.min(16, step * 0.72));

    const accent = getCSSVar("--accent", "#b8420f");
    const partialFrom = n - Math.max(0, options.partialCount || 0);

    const bars = values.map((v, i) => {
        const x = pad.left + i * step + (step - barW) / 2;
        const y = plotTop + (1 - v / niceMax) * plotH;
        const h = Math.max(1, plotBottom - y);
        // Partial (still-reporting) quarters render hollow so a low bar doesn't
        // read as a real drop; the newest complete quarter gets full weight.
        if (i >= partialFrom) {
            return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${accent}" fill-opacity="0.12" stroke="${accent}" stroke-opacity="0.5" stroke-width="1" stroke-dasharray="2 1.5" />`;
        }
        const emphasis = i === partialFrom - 1 ? 1 : 0.55;
        return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${accent}" fill-opacity="${emphasis}" />`;
    }).join("");

    const xIdx = pickXIndices(n, 5);
    const xLabels = xIdx.map(idx => {
        const x = pad.left + idx * step + step / 2;
        const label = periods[idx]?.short || periods[idx]?.label || "";
        return `<text x="${x.toFixed(1)}" y="${(plotBottom + 14).toFixed(1)}" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="9" fill="currentColor" fill-opacity="0.55">${label}</text>`;
    }).join("");

    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="color: var(--ink-3);">${grid}${yLabels}${bars}${xLabels}</svg>`;
}

function pickXIndices(n, count) {
    if (n <= count) return Array.from({ length: n }, (_, i) => i);
    const out = [0];
    for (let i = 1; i < count - 1; i++) out.push(Math.round(i * (n - 1) / (count - 1)));
    out.push(n - 1);
    return [...new Set(out)];
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

/* ─── Drawer routing ─── */

function pushDrawer(view) {
    if (state.drawer) {
        state.drawerStack.push(state.drawer);
    }
    state.drawer = view;
    syncURL();
    renderDrawer();
    showDrawer();
}

function replaceDrawer(view) {
    state.drawer = view;
    syncURL();
    renderDrawer();
    showDrawer();
}

function closeDrawer() {
    state.drawer = null;
    state.drawerStack = [];
    document.getElementById("drawer").setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    syncURL();
}

function popDrawer() {
    if (state.drawerStack.length === 0) {
        closeDrawer();
        return;
    }
    state.drawer = state.drawerStack.pop();
    syncURL();
    renderDrawer();
}

function showDrawer() {
    document.getElementById("drawer").setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
}

function openSignal(mode, name) {
    pushDrawer({ kind: "signal", mode, name });
}

function openClient(name) {
    pushDrawer({ kind: "client", name });
}

function openFiling(id) {
    pushDrawer({ kind: "filing", id: Number(id) });
}

/* ─── Drawer rendering ─── */

function renderDrawer() {
    const body = document.getElementById("drawer-body");
    const trail = document.getElementById("drawer-trail");
    if (!state.drawer) return;
    body.replaceChildren();
    trail.replaceChildren();

    // Build breadcrumb from stack
    const items = [...state.drawerStack, state.drawer];
    items.forEach((view, i) => {
        if (i > 0) trail.appendChild(el("span", "drawer-trail-sep", "›"));
        const btn = el("button", "drawer-trail-step", drawerLabel(view));
        if (i === items.length - 1) btn.classList.add("current");
        else {
            btn.onclick = () => {
                state.drawerStack = state.drawerStack.slice(0, i);
                state.drawer = view;
                syncURL();
                renderDrawer();
            };
        }
        trail.appendChild(btn);
    });

    if (state.drawer.kind === "signal") {
        renderSignalDetail(body, state.drawer);
    } else if (state.drawer.kind === "client") {
        renderClientDetail(body, state.drawer);
    } else if (state.drawer.kind === "filing") {
        renderFilingDetail(body, state.drawer);
    }
}

function drawerLabel(view) {
    if (view.kind === "signal") return `${CATEGORIES[view.mode].label}: ${displayName(view.mode, view.name)}`;
    if (view.kind === "client") return `Client: ${clientDisplay(view.name)}`;
    if (view.kind === "filing") return `Filing #${view.id}`;
    return "";
}

/* Signal detail */

function renderSignalDetail(body, view) {
    const items = getCategoryItemsFixed(view.mode, state.view.window);
    const fallback = {
        mode: view.mode,
        name: view.name,
        count: 0, prev_count: 0, yoy_count: 0,
        current_share_pct: 0, prev_share_pct: 0, yoy_share_pct: 0,
        share_delta_prev_pp: 0, share_delta_yoy_pp: 0,
        score: 0, confidence: "low",
        topClients: [], examples: [], income: 0
    };
    const m = items.find(i => i.name === view.name) || fallback;
    const meta = CATEGORIES[m.mode];

    // Eyebrow
    const eyebrow = el("div", "detail-eyebrow");
    eyebrow.appendChild(el("span", `cat-tag ${meta.tagClass}`, meta.label));
    eyebrow.appendChild(el("span", `detail-conf ${m.confidence}`, `${m.confidence} confidence`));
    body.appendChild(eyebrow);

    // Name + sub
    body.appendChild(el("h2", "detail-name", displayName(m.mode, m.name)));
    const subEl = el("div", "detail-sub");
    const winName = state.view.window === "90d" ? "Last 90 days" : "Last 30 days";
    subEl.innerHTML = `${winName} <span class="detail-sub-dates">(${fmt.esc(rangeLabel(state.view.window))})</span> · ${fmt.esc(COMPARE[state.view.compare].shortLabel)} <span class="detail-sub-dates">(${fmt.esc(baselineRangeLabel(state.view.window, state.view.compare))})</span>`;
    body.appendChild(subEl);

    // Plain-English summary
    const head = buildHeadline(m, state.view.compare);
    const summary = el("div", "detail-summary");
    summary.innerHTML = head.html;
    body.appendChild(summary);

    // Stats grid
    const stats = el("div", "detail-stats");
    const baseline = COMPARE[state.view.compare].baselineCountKey === "yoy_count" ? m.yoy_count : m.prev_count;
    const baselineShare = COMPARE[state.view.compare].baselineCountKey === "yoy_count" ? m.yoy_share_pct : m.prev_share_pct;
    const delta = state.view.compare === "yoy" ? m.share_delta_yoy_pp : m.share_delta_prev_pp;
    const deltaDir = delta > 0.01 ? "up" : delta < -0.01 ? "down" : "";
    const statCells = [
        { value: fmt.int(m.count),                label: "Mentions" },
        { value: fmt.pct(m.current_share_pct),    label: "Share" },
        { value: fmt.pp(delta),                    label: "Δ Share", cls: deltaDir },
        { value: fmt.int(baseline),                label: "Baseline" },
        { value: fmt.pct(baselineShare),          label: "Base share" },
        { value: m.income > 0 ? fmt.money(m.income) : "—", label: "Linked income" }
    ];
    for (const s of statCells) {
        const stat = el("div", "detail-stat");
        stat.appendChild(el("span", `detail-stat-value ${s.cls || ""}`.trim(), s.value));
        stat.appendChild(el("span", "detail-stat-label", s.label));
        stats.appendChild(stat);
    }
    body.appendChild(stats);

    // Quarterly chart (topics only)
    if (m.mode === "topics") {
        const series = state.timeseries?.topic_series?.[m.name];
        const quarters = state.timeseries?.quarters || [];
        if (series?.length && quarters.length > 1) {
            const sec = el("div", "detail-section");
            sec.appendChild(el("div", "detail-section-title", "Mentions by quarter"));
            const box = el("div", "detail-chart-box");
            const partialCount = partialTrailingCount(quarters);
            box.innerHTML = makeBarChart(series, quarters, { partialCount });
            sec.appendChild(box);
            const ctx = state.timeseries?.context;
            const notes = [];
            if (partialCount > 0) {
                const pq = quarters[quarters.length - 1];
                notes.push(`${pq.year} Q${pq.quarter} is still being reported (shown dashed) and will keep rising.`);
            }
            if (ctx?.reporting_note) notes.push(ctx.reporting_note);
            if (notes.length) {
                sec.appendChild(el("p", "detail-chart-note", notes.join(" ")));
            }
            body.appendChild(sec);
        }
    }

    // Top clients
    if (m.topClients?.length) {
        const sec = el("div", "detail-section");
        const title = el("div", "detail-section-title");
        title.appendChild(el("span", "", "Top clients"));
        title.appendChild(el("span", "count", `${m.topClients.length} listed`));
        sec.appendChild(title);
        const list = el("ul", "detail-list");
        for (const c of m.topClients.slice(0, 12)) {
            const li = el("li", "detail-list-item");
            li.tabIndex = 0;
            li.onclick = () => openClient(c);
            li.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openClient(c); } };
            li.appendChild(el("span", "detail-list-item-name", clientDisplay(c)));
            li.appendChild(el("span", "detail-list-item-meta", "→"));
            list.appendChild(li);
        }
        sec.appendChild(list);
        body.appendChild(sec);
    }

    // Example filings
    if (m.examples?.length) {
        const sec = el("div", "detail-section");
        const title = el("div", "detail-section-title");
        title.appendChild(el("span", "", "Example filings"));
        title.appendChild(el("span", "count", `${m.examples.length} shown`));
        sec.appendChild(title);
        const list = el("ul", "detail-list");
        for (const ex of m.examples.slice(0, 8)) {
            const li = el("div", "filing-row");
            li.tabIndex = 0;
            li.onclick = () => openFiling(ex.id);
            li.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFiling(ex.id); } };
            const left = el("div");
            left.appendChild(el("div", "filing-row-client", clientDisplay(ex.client) || "Unknown client"));
            if (ex.registrant) left.appendChild(el("div", "filing-row-registrant", clientDisplay(ex.registrant)));
            li.appendChild(left);
            li.appendChild(el("div", "filing-row-date", fmt.dateShort(ex.date)));
            list.appendChild(li);
        }
        sec.appendChild(list);
        body.appendChild(sec);
    }
}

/* Client detail */

function renderClientDetail(body, view) {
    const name = view.name;
    const filings = (state.filings || []).filter(f =>
        (f.client && f.client.toUpperCase() === name.toUpperCase()) ||
        (clientDisplay(f.client) === name) ||
        (clientDisplay(f.client) === clientDisplay(name))
    );

    // Aggregate from trends.*_clients to find which signals this client appears in
    const appearances = []; // {mode, name, position}
    for (const mode of SIGNAL_MODES) {
        const keys = getKeyMap(mode);
        const clientsByName = state.trends?.[keys.c]?.[state.view.window] || {};
        for (const sigName of Object.keys(clientsByName)) {
            const list = clientsByName[sigName] || [];
            const idx = list.findIndex(c => c.toUpperCase() === name.toUpperCase());
            if (idx >= 0) appearances.push({ mode, name: sigName, position: idx });
        }
    }
    // Group by mode
    const byMode = {};
    for (const a of appearances) {
        (byMode[a.mode] = byMode[a.mode] || []).push(a);
    }
    for (const m of Object.keys(byMode)) byMode[m].sort((a, b) => a.position - b.position);

    // Eyebrow
    const eyebrow = el("div", "detail-eyebrow");
    eyebrow.appendChild(el("span", "cat-tag client", "Client"));
    body.appendChild(eyebrow);

    // Name
    body.appendChild(el("h2", "detail-name", clientDisplay(name)));
    body.appendChild(el("div", "detail-sub", `${state.view.window === "90d" ? "Last 90 days" : "Last 30 days"} · activity across ${appearances.length} tracked signals`));

    if (!filings.length && !appearances.length) {
        const empty = el("p", "detail-empty");
        empty.textContent = "This client doesn't appear in the current window's top-clients or recent filings sample. Older activity may exist in the underlying database.";
        body.appendChild(empty);
        return;
    }

    // Stats
    const totalIncome = filings.reduce((s, f) => s + toNum(f.income), 0);
    const stats = el("div", "detail-stats");
    const cells = [
        { value: fmt.int(filings.length || "—"), label: "Recent filings" },
        { value: appearances.length ? fmt.int(appearances.length) : "—", label: "Top-N signals" },
        { value: filings.length ? fmt.dateShort(filings[0].date) : "—", label: "Most recent" },
        { value: totalIncome > 0 ? fmt.money(totalIncome) : "—", label: "Recent income" }
    ];
    for (const s of cells) {
        const stat = el("div", "detail-stat");
        stat.appendChild(el("span", "detail-stat-value", s.value));
        stat.appendChild(el("span", "detail-stat-label", s.label));
        stats.appendChild(stat);
    }
    body.appendChild(stats);

    // Signals they're in
    for (const mode of SIGNAL_MODES) {
        const list = byMode[mode];
        if (!list?.length) continue;
        const sec = el("div", "detail-section");
        const title = el("div", "detail-section-title");
        title.appendChild(el("span", "", `${CATEGORIES[mode].plural} they appear in`));
        title.appendChild(el("span", "count", `${list.length}`));
        sec.appendChild(title);
        const tags = el("div", "detail-tags");
        for (const a of list.slice(0, 30)) {
            const tag = el("button", "detail-tag", displayName(mode, a.name));
            tag.onclick = () => openSignal(mode, a.name);
            tags.appendChild(tag);
        }
        sec.appendChild(tags);
        body.appendChild(sec);
    }

    // Filings
    if (filings.length) {
        const sec = el("div", "detail-section");
        const title = el("div", "detail-section-title");
        title.appendChild(el("span", "", "Recent filings"));
        title.appendChild(el("span", "count", `${Math.min(filings.length, 12)} of ${filings.length}`));
        sec.appendChild(title);
        for (const f of filings.slice(0, 12)) {
            const row = el("div", "filing-row");
            row.tabIndex = 0;
            row.onclick = () => openFiling(f.id);
            row.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFiling(f.id); } };
            const left = el("div");
            const tags = [...(f.topics || []).slice(0, 2), ...(f.entities || []).slice(0, 2)].join(" · ");
            left.appendChild(el("div", "filing-row-client", tags || (f.domain || "Filing")));
            left.appendChild(el("div", "filing-row-registrant", clientDisplay(f.registrant) || "Unknown registrant"));
            row.appendChild(left);
            const right = el("div");
            right.appendChild(el("div", "filing-row-date", fmt.dateShort(f.date)));
            if (f.income) right.appendChild(el("div", "filing-row-income", fmt.money(f.income)));
            row.appendChild(right);
            sec.appendChild(row);
        }
        body.appendChild(sec);
    }
}

/* Filing detail */

function renderFilingDetail(body, view) {
    const f = (state.filings || []).find(x => x.id === view.id);
    if (!f) {
        const eyebrow = el("div", "detail-eyebrow");
        eyebrow.appendChild(el("span", "cat-tag filing", "Filing"));
        body.appendChild(eyebrow);
        body.appendChild(el("h2", "detail-name", `Filing #${view.id}`));
        body.appendChild(el("p", "detail-empty", "This filing isn't in the recent sample. Older filings live in the database release; the dashboard ships a rolling slice."));
        return;
    }

    const eyebrow = el("div", "detail-eyebrow");
    eyebrow.appendChild(el("span", "cat-tag filing", "Filing"));
    body.appendChild(eyebrow);

    body.appendChild(el("h2", "detail-name", clientDisplay(f.client) || "Filing"));
    body.appendChild(el("div", "detail-sub", `Filed ${fmt.dateLong(f.date)} · ${clientDisplay(f.registrant) || "Unknown registrant"}`));

    const stats = el("div", "detail-stats");
    const tagsCount = (f.topics?.length || 0) + (f.entities?.length || 0) + (f.legislation?.length || 0);
    const cells = [
        { value: f.year && f.quarter ? `${f.year} Q${f.quarter}` : "—", label: "Quarter" },
        { value: f.income ? fmt.money(f.income) : "—", label: "Income" },
        { value: f.domain || "—", label: "Domain" },
        { value: fmt.int(tagsCount), label: "Tags" }
    ];
    for (const s of cells) {
        const stat = el("div", "detail-stat");
        stat.appendChild(el("span", "detail-stat-value", s.value));
        stat.appendChild(el("span", "detail-stat-label", s.label));
        stats.appendChild(stat);
    }
    body.appendChild(stats);

    // Pivot to client
    const pivotSec = el("div", "detail-section");
    pivotSec.appendChild(el("div", "detail-section-title", "Client"));
    const clientBtn = el("button", "detail-tag", clientDisplay(f.client) || "Unknown");
    clientBtn.style.fontFamily = "var(--sans)";
    clientBtn.style.fontSize = "0.92rem";
    clientBtn.onclick = () => openClient(f.client);
    pivotSec.appendChild(clientBtn);
    body.appendChild(pivotSec);

    // Tag groups
    const tagGroups = [
        { mode: "topics", label: "Topics", values: f.topics || [] },
        { mode: "entities", label: "Agencies mentioned", values: f.entities || [] },
        { mode: "legislation", label: "Legislation", values: f.legislation || [] },
        { mode: "domains", label: "Domains", values: f.domains || [] }
    ];
    for (const g of tagGroups) {
        if (!g.values.length) continue;
        const sec = el("div", "detail-section");
        const title = el("div", "detail-section-title");
        title.appendChild(el("span", "", g.label));
        title.appendChild(el("span", "count", `${g.values.length}`));
        sec.appendChild(title);
        const tags = el("div", "detail-tags");
        for (const v of g.values) {
            const tag = el("button", "detail-tag", displayName(g.mode, v));
            tag.onclick = () => openSignal(g.mode, v);
            tags.appendChild(tag);
        }
        sec.appendChild(tags);
        body.appendChild(sec);
    }

    if (tagsCount === 0) {
        body.appendChild(el("p", "detail-empty", "No deterministic tags fired for this filing's activity descriptions."));
    }
}

/* ─── Command palette ─── */

let paletteIndex = null;

function buildPaletteIndex() {
    const idx = [];
    const seen = new Set();
    const add = (kind, mode, name, meta) => {
        const key = `${kind}:${mode || ""}:${name}`.toUpperCase();
        if (seen.has(key)) return;
        seen.add(key);
        idx.push({ kind, mode, name, meta, search: name.toLowerCase() });
    };

    for (const mode of SIGNAL_MODES) {
        const items = state.trends?.[mode]?.["90d"] || [];
        for (const it of items) {
            add("signal", mode, it.name, `${CATEGORIES[mode].label} · ${fmt.int(it.count)} mentions`);
        }
    }

    // Clients from filings + top-clients lists
    const clientCounts = new Map();
    for (const f of state.filings || []) {
        if (f.client) clientCounts.set(f.client, (clientCounts.get(f.client) || 0) + 1);
    }
    for (const mode of SIGNAL_MODES) {
        const keys = getKeyMap(mode);
        const map = state.trends?.[keys.c]?.["90d"] || {};
        for (const list of Object.values(map)) {
            for (const c of list) clientCounts.set(c, clientCounts.get(c) || 0);
        }
    }
    for (const [c, n] of clientCounts.entries()) {
        add("client", null, c, n > 0 ? `Client · ${n} recent filing${n === 1 ? "" : "s"}` : "Client");
    }

    return idx;
}

function searchPalette(query) {
    if (!paletteIndex) paletteIndex = buildPaletteIndex();
    const q = query.trim().toLowerCase();
    if (!q) {
        // Default suggestions: top movers
        const top = buildMovers("all", state.view.window, state.view.compare).slice(0, 8);
        return top.map(m => ({
            kind: "signal",
            mode: m.mode,
            name: m.name,
            meta: `${CATEGORIES[m.mode].label} · ${fmt.int(m.count)} mentions`
        }));
    }

    // Score: name starts with query > word starts with query > contains
    const scored = [];
    for (const item of paletteIndex) {
        const lower = item.search;
        let score = 0;
        if (lower === q) score = 100;
        else if (lower.startsWith(q)) score = 80;
        else {
            const words = lower.split(/[^a-z0-9]+/);
            if (words.some(w => w.startsWith(q))) score = 60;
            else if (lower.includes(q)) score = 30;
        }
        if (score > 0) scored.push({ ...item, score });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, 30);
}

function renderPalette() {
    const container = document.getElementById("palette-results");
    container.replaceChildren();
    const results = state.palette.results;

    if (!results.length) {
        const empty = el("div", "palette-empty", "No matches.");
        container.appendChild(empty);
        return;
    }

    // Group by kind
    const groups = { signal: { topics: [], entities: [], legislation: [], domains: [] }, client: [] };
    for (const r of results) {
        if (r.kind === "signal") groups.signal[r.mode].push(r);
        else if (r.kind === "client") groups.client.push(r);
    }

    let flatIdx = 0;
    const renderGroup = (label, items) => {
        if (!items.length) return;
        container.appendChild(el("div", "palette-group-label", label));
        for (const r of items) {
            const myIdx = flatIdx++;
            const row = el("div", "palette-result");
            if (myIdx === state.palette.focusIdx) row.classList.add("focused");
            row.dataset.idx = myIdx;
            const name = el("div", "palette-result-name");
            name.innerHTML = highlightMatch(displayName(r.mode, r.name), state.palette.query);
            row.appendChild(name);
            row.appendChild(el("div", "palette-result-meta", r.meta || ""));
            row.onclick = () => {
                state.palette.focusIdx = myIdx;
                executePalette();
            };
            row.onmouseenter = () => {
                state.palette.focusIdx = myIdx;
                container.querySelectorAll(".palette-result").forEach(n =>
                    n.classList.toggle("focused", Number(n.dataset.idx) === myIdx));
            };
            container.appendChild(row);
        }
    };

    renderGroup("Topics", groups.signal.topics);
    renderGroup("Agencies", groups.signal.entities);
    renderGroup("Bills", groups.signal.legislation);
    renderGroup("Domains", groups.signal.domains);
    renderGroup("Clients", groups.client);
}

function highlightMatch(name, q) {
    if (!q) return fmt.esc(name);
    const lower = name.toLowerCase();
    const idx = lower.indexOf(q.toLowerCase());
    if (idx < 0) return fmt.esc(name);
    const before = name.slice(0, idx);
    const match = name.slice(idx, idx + q.length);
    const after = name.slice(idx + q.length);
    return `${fmt.esc(before)}<mark>${fmt.esc(match)}</mark>${fmt.esc(after)}`;
}

function openPalette() {
    state.palette.open = true;
    state.palette.query = "";
    state.palette.focusIdx = 0;
    state.palette.results = searchPalette("");
    document.getElementById("palette").setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    const input = document.getElementById("palette-input");
    input.value = "";
    setTimeout(() => input.focus(), 30);
    renderPalette();
}

function closePalette() {
    state.palette.open = false;
    document.getElementById("palette").setAttribute("aria-hidden", "true");
    if (!state.drawer) document.body.style.overflow = "";
}

function executePalette() {
    const flatList = state.palette.results;
    // Need to walk in same group order as renderPalette
    const groups = { signal: { topics: [], entities: [], legislation: [], domains: [] }, client: [] };
    for (const r of flatList) {
        if (r.kind === "signal") groups.signal[r.mode].push(r);
        else if (r.kind === "client") groups.client.push(r);
    }
    const ordered = [
        ...groups.signal.topics,
        ...groups.signal.entities,
        ...groups.signal.legislation,
        ...groups.signal.domains,
        ...groups.client
    ];
    const choice = ordered[state.palette.focusIdx];
    if (!choice) return;
    closePalette();
    if (choice.kind === "signal") openSignal(choice.mode, choice.name);
    else if (choice.kind === "client") openClient(choice.name);
}

/* ─── URL state ─── */

function syncURL() {
    let hash = "";
    if (state.drawer) {
        if (state.drawer.kind === "signal") {
            hash = `#${state.drawer.mode}/${encodeURIComponent(state.drawer.name)}`;
        } else if (state.drawer.kind === "client") {
            hash = `#client/${encodeURIComponent(state.drawer.name)}`;
        } else if (state.drawer.kind === "filing") {
            hash = `#filing/${state.drawer.id}`;
        }
    }
    if (hash !== window.location.hash) {
        history.replaceState(null, "", hash || window.location.pathname);
    }
}

function readURL() {
    const hash = window.location.hash.replace(/^#/, "");
    if (!hash) return;
    const parts = hash.split("/").map(decodeURIComponent);
    const [kind, ...rest] = parts;
    if (SIGNAL_MODES.includes(kind)) {
        state.drawer = { kind: "signal", mode: kind, name: rest.join("/") };
    } else if (kind === "client") {
        state.drawer = { kind: "client", name: rest.join("/") };
    } else if (kind === "filing") {
        state.drawer = { kind: "filing", id: Number(rest[0]) };
    }
}

/* ─── Initialization ─── */

function bindControls() {
    document.getElementById("search-trigger").onclick = openPalette;

    document.querySelectorAll("#window-seg .seg-btn").forEach(b => {
        b.onclick = () => {
            state.view.window = b.dataset.window;
            document.querySelectorAll("#window-seg .seg-btn").forEach(x =>
                x.classList.toggle("active", x === b));
            renderHero();
            renderMovers();
            paletteIndex = null;
            if (state.drawer) renderDrawer();
        };
    });

    document.querySelectorAll("#compare-seg .seg-btn").forEach(b => {
        b.onclick = () => {
            state.view.compare = b.dataset.compare;
            document.querySelectorAll("#compare-seg .seg-btn").forEach(x =>
                x.classList.toggle("active", x === b));
            renderHero();
            renderMovers();
            if (state.drawer) renderDrawer();
        };
    });

    document.querySelectorAll("#cat-row .cat-pill").forEach(b => {
        b.onclick = () => {
            state.view.cat = b.dataset.cat;
            document.querySelectorAll("#cat-row .cat-pill").forEach(x =>
                x.classList.toggle("active", x === b));
            renderMovers();
        };
    });

    document.querySelectorAll("[data-close-drawer]").forEach(n =>
        n.onclick = () => closeDrawer());

    document.querySelectorAll("[data-close-palette]").forEach(n =>
        n.onclick = () => closePalette());

    const paletteInput = document.getElementById("palette-input");
    paletteInput.addEventListener("input", () => {
        state.palette.query = paletteInput.value;
        state.palette.results = searchPalette(state.palette.query);
        state.palette.focusIdx = 0;
        renderPalette();
    });

    document.addEventListener("keydown", e => {
        // Cmd/Ctrl+K opens palette
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
            e.preventDefault();
            if (state.palette.open) closePalette();
            else openPalette();
            return;
        }
        // / opens palette when not in input
        if (e.key === "/" && !state.palette.open
            && document.activeElement?.tagName !== "INPUT"
            && document.activeElement?.tagName !== "TEXTAREA") {
            e.preventDefault();
            openPalette();
            return;
        }
        // Esc closes overlays
        if (e.key === "Escape") {
            if (state.palette.open) { closePalette(); return; }
            if (state.drawer) { closeDrawer(); return; }
        }
        // Palette navigation
        if (state.palette.open) {
            const total = state.palette.results.length;
            if (!total) return;
            if (e.key === "ArrowDown") {
                e.preventDefault();
                state.palette.focusIdx = (state.palette.focusIdx + 1) % total;
                renderPalette();
                scrollFocusedIntoView();
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                state.palette.focusIdx = (state.palette.focusIdx - 1 + total) % total;
                renderPalette();
                scrollFocusedIntoView();
            } else if (e.key === "Enter") {
                e.preventDefault();
                executePalette();
            }
        }
    });

    window.addEventListener("hashchange", () => {
        const before = state.drawer;
        readURL();
        if (state.drawer !== before) {
            if (state.drawer) {
                renderDrawer();
                showDrawer();
            } else {
                document.getElementById("drawer").setAttribute("aria-hidden", "true");
                document.body.style.overflow = "";
            }
        }
    });
}

function scrollFocusedIntoView() {
    const node = document.querySelector(".palette-result.focused");
    if (node) node.scrollIntoView({ block: "nearest" });
}

async function loadJSON(file) {
    try {
        const r = await fetch(`${DATA_PATH}/${file}?v=${Date.now()}`);
        if (!r.ok) return null;
        return await r.json();
    } catch { return null; }
}

async function init() {
    const [stats, trends, recent, timeseries] = await Promise.all([
        loadJSON("stats.json"),
        loadJSON("trends.json"),
        loadJSON("recent.json"),
        loadJSON("timeseries.json")
    ]);

    state.stats = stats || null;
    state.trends = trends || null;
    state.filings = recent?.filings || [];
    state.timeseries = timeseries || null;

    // Top bar meta — show "data through {date} · refreshed Xh ago"
    const meta = document.getElementById("topbar-meta");
    const ts = trends?.generated_at || stats?.generated_at;
    const asOf = dataAsOfDate();
    if (meta) {
        const parts = [];
        if (asOf) parts.push(`data through ${fmtMonthDayYear(asOf)}`);
        if (ts) parts.push(`refreshed ${fmt.ago(ts)}`);
        meta.textContent = parts.join(" · ");
        if (ts) meta.title = `Last refresh: ${new Date(ts).toLocaleString()}`;
    }

    bindControls();
    renderHero();
    renderMovers();

    // Open drawer if URL has a deep link
    readURL();
    if (state.drawer) {
        renderDrawer();
        showDrawer();
    }
}

document.addEventListener("DOMContentLoaded", init);
