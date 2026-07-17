/* ══════════════════════════════════════════════
   Lobbying Signals — Editorial dashboard
   Single-page flow with command palette + drawer
   ══════════════════════════════════════════════ */

const DATA_PATH = "data";

const CATEGORIES = {
    clients:     { label: "Org",     plural: "Organizations", tagClass: "clients",   shortLabel: "Org" },
    topics:      { label: "Topic",   plural: "Topics",      tagClass: "topics",      shortLabel: "Topic" },
    entities:    { label: "Agency",  plural: "Agencies",    tagClass: "entities",    shortLabel: "Agency" },
    legislation: { label: "Bill",    plural: "Bills",       tagClass: "legislation", shortLabel: "Bill" },
    domains:     { label: "Domain",  plural: "Domains",     tagClass: "domains",     shortLabel: "Domain" }
};

// Tag-mention categories driven by trends.json's window/compare toggles.
// Organizations ("clients") are a separate flagship view — fixed-quarter
// dollar comparisons from clients.json, not part of this signal system.
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
        // The exporter canonicalizes bill numbers and P.L. references into act
        // names (Congress-aware), so tags arrive as e.g. "One Big Beautiful
        // Bill Act". This map only adds familiar-number context for display.
        "One Big Beautiful Bill Act": "H.R. 1 / One Big Beautiful Bill Act"
    }
};

/* ─── State ─── */

const state = {
    trends: null,
    stats: null,
    filings: [],
    timeseries: null,
    clients: null,       // clients.json — org spend movers; null if absent/failed to load
    clientIndex: new Map(),

    view: {
        window: "90d",
        compare: "yoy",
        cat: "all"   // all | clients | topics | entities | legislation | domains | recent
    },

    drawer: null,        // current drawer view
    drawerStack: [],     // breadcrumb history within drawer
    palette: { open: false, query: "", focusIdx: 0, results: [] }
};

/* ─── Utilities ─── */

/* Display-casing rules, mirroring clients_norm.py's display_client_name so
   client/registrant names (and any other ALL-CAPS source string) render
   readably instead of "Chamber OF Commerce OF The U.s.a."-style bugs:
   small words lowercase mid-name, a short acronym allowlist plus a
   no-vowel heuristic stay uppercase, and dotted tokens (U.S.A.) stay as-is. */
const TITLECASE_SMALL_WORDS = new Set([
    "of", "the", "and", "for", "on", "in", "at", "to", "by", "or", "a", "an", "d/b/a"
]);
const TITLECASE_ACRONYM_ALLOWLIST = new Set([
    "USA", "US", "LLC", "LLP", "AARP", "AFLCIO", "AFL-CIO", "PG&E", "IBM",
    "AT&T", "CTIA", "HCA", "NACDS", "AHIP"
]);
const TITLECASE_DOTTED_RE = /^[A-Za-z](\.[A-Za-z])+\.?$/;

function hasVowel(s) { return /[AEIOU]/.test(s.toUpperCase()); }

function titleCaseHyphenSegment(seg) {
    if (!seg) return seg;
    const bare = seg.replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9]+$/g, "");
    if (!bare) return seg;
    const bareUpper = bare.toUpperCase();
    if (TITLECASE_ACRONYM_ALLOWLIST.has(bareUpper)) return seg.toUpperCase();
    if (bare.length <= 4 && /^[A-Za-z]+$/.test(bare) && !hasVowel(bare)) return seg.toUpperCase();
    return seg.charAt(0).toUpperCase() + seg.slice(1).toLowerCase();
}

function titleCaseWord(word, isFirst) {
    if (!word) return word;

    const bare = word.replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9]+$/g, "");
    if (!bare) return word;

    // Check the dotted-acronym pattern against the bare token too, so a
    // parenthesized acronym like "(N.A.C.H.)" is recognized, not just a
    // bare "U.S.A."
    if (TITLECASE_DOTTED_RE.test(word) || TITLECASE_DOTTED_RE.test(bare)) return word.toUpperCase();
    const bareUpper = bare.toUpperCase();

    if (TITLECASE_ACRONYM_ALLOWLIST.has(bareUpper)) return word.toUpperCase();

    // Pragmatic heuristic: short, all-consonant tokens are almost always
    // acronyms/initialisms ("PBC", "NV", "GMBH"), not ordinary words.
    if (bare.length <= 4 && /^[A-Za-z]+$/.test(bare) && !hasVowel(bare)) return word.toUpperCase();

    if (!isFirst && TITLECASE_SMALL_WORDS.has(bare.toLowerCase())) return word.toLowerCase();

    // Hyphenated compounds ("CTIA-The Wireless Association") re-check each
    // segment rather than blind-capitalizing.
    if (word.includes("-")) {
        return word.split("-").map(titleCaseHyphenSegment).join("-");
    }
    return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
}

function titleCaseName(s) {
    if (!s) return "";
    return String(s).split(/\s+/).map((w, i) => {
        if (w === "&") return "&";
        return titleCaseWord(w, i === 0);
    }).join(" ");
}

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
    titleCase: s => titleCaseName(s),
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

function coverageStartDate() {
    return parseDate(state.stats?.date_range?.start);
}

/* "vs year ago" only means something when the year-ago baseline window falls
   inside tracked coverage. If tracking started later than that window, every
   yoy baseline is an artificial zero: every item reads "first time", the
   share-delta ranking degrades to plain size, and every trend line is the same
   0-to-now diagonal. This re-enables itself once a full year has accumulated. */
function yoySupported(windowKey) {
    const start = coverageStartDate();
    if (!start) return true; // no coverage info — trust the data
    const days = windowKey === "30d" ? 30 : 90;
    const DAY_MS = 86400000;
    const baseStart = dataAsOfDate().getTime() - (365 + days) * DAY_MS;
    return start.getTime() <= baseStart;
}

function defaultCompare(windowKey) {
    return yoySupported(windowKey) ? "yoy" : "prev";
}

/* Keep the compare toggle honest: disable "vs year ago" (with an explanation)
   while coverage can't back it, and repair state if it points at it. */
function resolveCompareMode() {
    const seg = document.getElementById("compare-seg");
    if (!seg) return;
    const yoyBtn = seg.querySelector('[data-compare="yoy"]');
    const ok = yoySupported(state.view.window);
    if (!ok && state.view.compare === "yoy") state.view.compare = "prev";
    if (yoyBtn) {
        yoyBtn.disabled = !ok;
        yoyBtn.title = ok ? "" :
            `Needs a year of history — tracking started ${fmtMonthDayYear(coverageStartDate())}.`;
    }
    seg.querySelectorAll(".seg-btn").forEach(x =>
        x.classList.toggle("active", x.dataset.compare === state.view.compare));
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
            html: `<strong>${fmt.int(current)}</strong> ${noun} — <span class="delta delta-up-x">new</span>, none in the ${baselineLabel}.`,
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

// Secondary line under a tag mover's headline: how many distinct
// organizations are actually behind the mention count, so a reader isn't
// left thinking "1,303 mentions" came from one filer.
function buildMobilizationLine(item, compareKey) {
    const current = toNum(item.client_count);
    if (!current) return null;
    const baseline = compareKey === "yoy" ? toNum(item.yoy_client_count) : toNum(item.prev_client_count);
    const baselineLabel = compareKey === "yoy" ? "a year ago" : "in the prior period";
    const orgWord = current === 1 ? "organization" : "organizations";
    return `${fmt.int(current)} ${orgWord} active (${fmt.int(baseline)} ${baselineLabel})`;
}

/* ─── Organization movers (clients.json) ───
   A separate flagship view: dollar spend per organization, comparing the
   latest COMPLETE report quarter to the same quarter a year ago. Fixed
   quarter framing — the window/compare toggles don't apply here. */

function orgMoversAvailable() {
    const c = state.clients;
    return !!(c && (c.risers?.length || c.fallers?.length || c.new_entrants?.length));
}

function tagOrgMover(m, kind) {
    return {
        ...m,
        mode: "clients",
        entrantKind: kind,
        current: toNum(m.current),
        baseline: toNum(m.baseline),
        delta: toNum(m.delta),
        _absDelta: Math.abs(toNum(m.delta))
    };
}

function allOrgMovers() {
    if (!orgMoversAvailable()) return [];
    const c = state.clients;
    return [
        ...(c.risers || []).map(m => tagOrgMover(m, "riser")),
        ...(c.fallers || []).map(m => tagOrgMover(m, "faller")),
        ...(c.new_entrants || []).map(m => tagOrgMover(m, "new")),
    ];
}

// Risers + new entrants only (no fallers), ranked by dollar delta — used for
// both the hero headline synthesis and the "Everything" interleave, where
// org movers should read as ramp-ups, not a mix of ups and downs.
function topOrgRisersAndNewEntrants(limit) {
    if (!orgMoversAvailable()) return [];
    const c = state.clients;
    const pool = [
        ...(c.risers || []).map(m => tagOrgMover(m, "riser")),
        ...(c.new_entrants || []).map(m => tagOrgMover(m, "new")),
    ];
    return pool.sort((a, b) => b.delta - a.delta).slice(0, limit);
}

function orgQuarterLabels() {
    return {
        cq: state.clients?.current_quarter?.label || "the latest quarter",
        bq: state.clients?.baseline_quarter?.label || "the year-ago quarter"
    };
}

// Last calendar day of a report quarter (quarter is 1-indexed).
function quarterEndDate(year, quarter) {
    return new Date(year, quarter * 3, 0);
}

// The statutory LDA filing deadline for a report quarter: the 20th of the
// following month.
function reportsDueDate(year, quarter) {
    const end = quarterEndDate(year, quarter);
    return new Date(end.getFullYear(), end.getMonth() + 1, 20);
}

// "reports due Apr 20" — only while that deadline is still relevant (up to
// ~10 days past it); null once it's stale so the hero doesn't nag forever.
function reportsDueLabel(year, quarter) {
    const deadline = reportsDueDate(year, quarter);
    const graceEnd = new Date(deadline.getTime() + 10 * 86400000);
    if (new Date() >= graceEnd) return null;
    return deadline.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function buildOrgHeadline(m) {
    const { cq, bq } = orgQuarterLabels();
    const current = toNum(m.current);
    const baseline = toNum(m.baseline);

    if (baseline === 0 && current > 0) {
        return {
            html: `<strong>${fmt.money(current)}</strong> in ${cq} — <span class="delta delta-up-x">new</span> — no lobbying in ${bq}.`,
            dir: "up"
        };
    }
    if (current === 0 && baseline > 0) {
        return {
            html: `Quiet in ${cq} — <strong>$0</strong> vs <strong>${fmt.money(baseline)}</strong> in ${bq}.`,
            dir: "down"
        };
    }

    const ratio = current / baseline;
    const pctChange = (ratio - 1) * 100;
    const intensity = deltaIntensity(ratio);

    if (ratio >= 2) {
        const xLabel = ratio >= 10 ? `${Math.round(ratio)}×` : `${ratio.toFixed(1)}×`;
        return {
            html: `<strong>${fmt.money(current)}</strong> in ${cq} — <span class="delta delta-up${intensity}">${xLabel}</span> ${bq} (${fmt.money(baseline)}).`,
            dir: "up"
        };
    }
    const sign = pctChange >= 0 ? "+" : "";
    const dirCls = pctChange >= 0 ? `delta-up${intensity}` : `delta-down${intensity}`;
    return {
        html: `<strong>${fmt.money(current)}</strong> in ${cq} — <span class="delta ${dirCls}">${sign}${pctChange.toFixed(0)}%</span> vs ${bq} (${fmt.money(baseline)}).`,
        dir: pctChange >= 0 ? "up" : "down"
    };
}

function makeOrgTrendChart(current, baseline, dir) {
    const W = 200, H = 48;
    const padT = 12, padB = 11;
    const plotH = H - padT - padB;
    const baseLine = H - padB;

    const accent = dir === "up"
        ? getCSSVar("--up", "#1f7a4d")
        : dir === "down"
            ? getCSSVar("--down", "#b53a3a")
            : getCSSVar("--accent", "#b8420f");
    const muted = getCSSVar("--ink-4", "#a39c87");
    const labelColor = getCSSVar("--ink-3", "#7a7565");
    const valueColor = getCSSVar("--ink-2", "#4a4a4a");

    const baseVal = toNum(baseline), nowVal = toNum(current);
    const yMax = Math.max(baseVal, nowVal, 1);
    const barW = 38;
    const bx = W * 0.32, nx = W * 0.68;

    const bar = (cx, val, fill, opacity) => {
        const h = Math.max(val > 0 ? 2 : 0, (val / yMax) * plotH);
        const y = baseLine - h;
        return `<rect x="${(cx - barW / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${barW}" height="${h.toFixed(1)}" rx="2" fill="${fill}" fill-opacity="${opacity}"/>`;
    };
    const valueText = (cx, val) =>
        `<text x="${cx.toFixed(1)}" y="${(baseLine - Math.max(val > 0 ? 2 : 0, (val / yMax) * plotH) - 3).toFixed(1)}" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="9" font-weight="600" fill="${valueColor}">${fmt.money(val)}</text>`;
    const periodText = (cx, label, color, weight) =>
        `<text x="${cx.toFixed(1)}" y="${H - 2}" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="8" fill="${color}" font-weight="${weight}" letter-spacing="0.05em">${label}</text>`;

    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" class="trend-svg" role="img" aria-label="year ago: ${fmt.esc(fmt.money(baseVal))}, now: ${fmt.esc(fmt.money(nowVal))}">
        <line x1="12" y1="${baseLine}" x2="${W - 12}" y2="${baseLine}" stroke="${muted}" stroke-opacity="0.35" stroke-width="1"/>
        ${bar(bx, baseVal, muted, 0.45)}
        ${bar(nx, nowVal, accent, 0.9)}
        ${valueText(bx, baseVal)}
        ${valueText(nx, nowVal)}
        ${periodText(bx, "yr ago", labelColor, 400)}
        ${periodText(nx, "now", accent, 500)}
    </svg>`;
}

function openOrg(key, name) {
    pushDrawer({ kind: "org", key, name });
}

function buildOrgMoverCard(m) {
    const card = el("li", "mover");
    card.setAttribute("role", "button");
    card.tabIndex = 0;
    card.setAttribute("aria-label", `Open organization ${m.name}`);
    card.onclick = () => openOrg(m.key, m.name);
    card.onkeydown = e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openOrg(m.key, m.name); }
    };

    card.appendChild(el("span", "cat-tag clients", "Org"));

    const main = el("div", "mover-main");
    main.appendChild(el("div", "mover-name", m.name));

    const head = buildOrgHeadline(m);
    const headlineEl = el("div", "mover-headline");
    headlineEl.innerHTML = head.html;
    main.appendChild(headlineEl);

    if (m.topics?.length) {
        main.appendChild(el("div", "mover-clients", m.topics.slice(0, 3).join(" · ")));
    }

    card.appendChild(main);

    const trendSlot = el("div", "mover-trend");
    trendSlot.innerHTML = makeOrgTrendChart(m.current, m.baseline, head.dir);
    card.appendChild(trendSlot);

    card.appendChild(el("div", "mover-arrow", "→"));
    return card;
}

function buildAnyMoverCard(m, compareKey) {
    if (m.mode === "clients") return buildOrgMoverCard(m);
    return buildMoverCard(m, compareKey);
}

function interleaveOrgMovers(tagMovers, orgMovers, everyN = 3) {
    if (!orgMovers.length) return tagMovers;
    const out = [];
    let oi = 0;
    for (let i = 0; i < tagMovers.length; i++) {
        if (oi < orgMovers.length && i > 0 && i % everyN === 0) {
            out.push(orgMovers[oi++]);
        }
        out.push(tagMovers[i]);
    }
    while (oi < orgMovers.length) out.push(orgMovers[oi++]);
    return out;
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
        income: toNum(income[item.name]),
        client_count: toNum(item.client_count),
        prev_client_count: toNum(item.prev_client_count),
        yoy_client_count: toNum(item.yoy_client_count)
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
    const heroCmp = defaultCompare("90d");
    const heroDeltaKey = heroCmp === "yoy" ? "share_delta_yoy_pp" : "share_delta_prev_pp";
    const allMovers = buildMovers("all", "90d", heroCmp)
        .filter(m => m[heroDeltaKey] > 0 && m.count >= 100);
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

    // Organizations are the flagship story — lead with the biggest dollar
    // ramp-ups when clients.json is available, falling back to the
    // tag-mention synthesis above otherwise.
    const orgPicks = topOrgRisersAndNewEntrants(3);
    let headline;
    if (orgPicks.length >= 2) {
        const cq = state.clients?.current_quarter?.label || "this quarter";
        const orgNames = orgPicks.map(m => `<em>${fmt.esc(m.name)}</em>`);
        const last = orgNames.pop();
        const head = orgNames.length === 1
            ? `${orgNames[0]} and ${last}`
            : `${orgNames.join(", ")}, and ${last}`;
        headline = `${head} posted the biggest lobbying ramp-ups in ${cq}.`;
    } else if (orgPicks.length === 1) {
        const cq = state.clients?.current_quarter?.label || "this quarter";
        headline = `<em>${fmt.esc(orgPicks[0].name)}</em> posted the biggest lobbying ramp-up in ${cq}.`;
    } else if (moverNames.length >= 2) {
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
        let partialLabel = `${fmt.num(latest.filings)} filings so far · partial quarter`;
        if (isPartial) {
            const due = reportsDueLabel(latest.year, latest.quarter);
            if (due) partialLabel += ` · reports due ${due}`;
        }
        statItems.push({
            value: `${latest.year} Q${latest.quarter}`,
            label: isPartial ? partialLabel : `${fmt.num(latest.filings)} filings · ${fmt.money(latest.income)}`
        });
    }

    // Latest COMPLETE quarter, dollars, year-over-year — the real headline
    // number ("$1.63B reported, +10.7% vs the same quarter last year").
    const lcq = stats.latest_complete_quarter;
    if (lcq && lcq.income_change_pct != null) {
        const change = lcq.income_change_pct;
        const dir = change > 0 ? "up" : change < 0 ? "down" : "";
        const sign = change > 0 ? "↑ " : change < 0 ? "↓ " : "";
        statItems.push({
            value: `${sign}${Math.abs(change).toFixed(1)}%`,
            label: `${lcq.label} vs Q${lcq.quarter} ${lcq.year - 1} · ${fmt.money(lcq.income)} reported`,
            trend: dir
        });
    } else if (cmpLatest && cmpPrev) {
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

    updateControlsForCat(cat);

    if (cat === "recent") {
        sub.textContent = `${state.filings.length} latest filings, most recent first`;
        renderRecentList(list);
        return;
    }

    if (cat === "clients") {
        renderOrgMovers(list, sub);
        return;
    }

    let movers = buildMovers(cat, win, cmp).slice(0, 50);
    if (cat === "all" && orgMoversAvailable()) {
        // Org movers are the flagship story — surface a handful near the top
        // of the combined feed, not just under their own pill.
        movers = interleaveOrgMovers(movers, topOrgRisersAndNewEntrants(6));
    }

    const catLabel = cat === "all" ? "across organizations, topics, agencies, bills, and domains" : `in ${CATEGORIES[cat].plural.toLowerCase()}`;
    const winLabel = win === "90d" ? "last 90 days" : "last 30 days";
    sub.innerHTML = `Top movers ${fmt.esc(catLabel)} — <span class="window-range">${fmt.esc(winLabel)}</span> <span class="window-range-dates">(${fmt.esc(rangeLabel(win))})</span> ${fmt.esc(COMPARE[cmp].shortLabel)} <span class="window-range-dates">(${fmt.esc(baselineRangeLabel(win, cmp))})</span>`;

    if (!movers.length) {
        const empty = el("div", "mover-empty");
        empty.textContent = "No signals match this window.";
        list.appendChild(empty);
        return;
    }

    // Lead with a scannable top set; the long tail expands on demand.
    const VISIBLE = 20;
    for (const m of movers.slice(0, VISIBLE)) {
        list.appendChild(buildAnyMoverCard(m, cmp));
    }
    if (movers.length > VISIBLE) {
        const more = el("button", "mover-more");
        more.type = "button";
        more.textContent = `Show all ${movers.length} movers`;
        more.onclick = () => {
            more.remove();
            for (const m of movers.slice(VISIBLE)) {
                list.appendChild(buildAnyMoverCard(m, cmp));
            }
        };
        list.appendChild(more);
    }
}

function renderOrgMovers(list, sub) {
    if (!orgMoversAvailable()) {
        sub.textContent = "Organization spend data isn't available right now.";
        list.appendChild(el("div", "mover-empty", "Organization spend data isn't available in this build."));
        return;
    }

    const { cq, bq } = orgQuarterLabels();
    sub.textContent = `${cq} vs ${bq} · complete quarters`;

    const movers = allOrgMovers().sort((a, b) => b._absDelta - a._absDelta);
    if (!movers.length) {
        list.appendChild(el("div", "mover-empty", "No organizations clear the reporting floor this quarter."));
        return;
    }

    const VISIBLE = 20;
    for (const m of movers.slice(0, VISIBLE)) {
        list.appendChild(buildOrgMoverCard(m));
    }
    if (movers.length > VISIBLE) {
        const more = el("button", "mover-more");
        more.type = "button";
        more.textContent = `Show all ${movers.length} organizations`;
        more.onclick = () => {
            more.remove();
            for (const m of movers.slice(VISIBLE)) {
                list.appendChild(buildOrgMoverCard(m));
            }
        };
        list.appendChild(more);
    }
}

// Organizations have a fixed quarter-vs-quarter framing (no rolling window),
// so the window/compare segmented controls don't apply — hide them rather
// than let them silently do nothing.
function updateControlsForCat(cat) {
    const windowSeg = document.getElementById("window-seg");
    const compareSeg = document.getElementById("compare-seg");
    const hide = cat === "clients";
    if (windowSeg) windowSeg.classList.toggle("controls-hidden", hide);
    if (compareSeg) compareSeg.classList.toggle("controls-hidden", hide);
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

    const mobLine = buildMobilizationLine(m, compareKey);
    if (mobLine) {
        main.appendChild(el("div", "mover-clients", mobLine));
    }

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
    card.onclick = () => openFiling(f.id, f);
    card.onkeydown = e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFiling(f.id, f); }
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

function makeTrendChart(item, mode, windowKey, compareKey, options = {}) {
    // Two periods, two numbers → two labeled bars. Both values are printed, so
    // the chart carries its own axis; a line through two points implied a time
    // axis that didn't exist. Quarter-level history lives in the drawer.
    const W = 200, H = 48;
    const padT = 12;   // room for value labels above bars
    const padB = 11;   // room for period labels below
    const plotH = H - padT - padB;
    const baseline = H - padB;

    const accent = options.accent || getCSSVar("--accent", "#b8420f");
    const muted  = getCSSVar("--ink-4", "#a39c87");
    const labelColor = getCSSVar("--ink-3", "#7a7565");
    const valueColor = getCSSVar("--ink-2", "#4a4a4a");

    const baseVal = compareKey === "yoy" ? toNum(item.yoy_count) : toNum(item.prev_count);
    const nowVal = toNum(item.count);
    const yMax = Math.max(baseVal, nowVal, 1);
    const barW = 38;
    const bx = W * 0.32, nx = W * 0.68; // bar centers
    const baseLabel = compareKey === "yoy" ? "yr ago" : "prior";

    const bar = (cx, val, fill, opacity) => {
        const h = Math.max(val > 0 ? 2 : 0, (val / yMax) * plotH);
        const y = baseline - h;
        return `<rect x="${(cx - barW / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${barW}" height="${h.toFixed(1)}" rx="2" fill="${fill}" fill-opacity="${opacity}"/>`;
    };
    const valueText = (cx, val) =>
        `<text x="${cx.toFixed(1)}" y="${(baseline - Math.max(val > 0 ? 2 : 0, (val / yMax) * plotH) - 3).toFixed(1)}" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="9" font-weight="600" fill="${valueColor}">${fmt.num(val)}</text>`;
    const periodText = (cx, label, color, weight) =>
        `<text x="${cx.toFixed(1)}" y="${H - 2}" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="8" fill="${color}" font-weight="${weight}" letter-spacing="0.05em">${label}</text>`;

    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" class="trend-svg" role="img" aria-label="${fmt.esc(baseLabel)}: ${fmt.int(baseVal)}, now: ${fmt.int(nowVal)}">
        <line x1="12" y1="${baseline}" x2="${W - 12}" y2="${baseline}" stroke="${muted}" stroke-opacity="0.35" stroke-width="1"/>
        ${bar(bx, baseVal, muted, 0.45)}
        ${bar(nx, nowVal, accent, 0.9)}
        ${valueText(bx, baseVal)}
        ${valueText(nx, nowVal)}
        ${periodText(bx, baseLabel, labelColor, 400)}
        ${periodText(nx, "now", accent, 500)}
    </svg>`;
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
    const fmtY = options.percent ? v => `${v.toFixed(v >= 10 ? 0 : 1)}%`
        : options.money ? v => fmt.money(v)
        : v => fmt.num(v);

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
        const label = periods[i]?.label || periods[i]?.short || "";
        const valueLabel = options.money ? fmt.money(v) : `${fmt.int(v)} mentions`;
        const hover = `<title>${fmt.esc(label)} · ${valueLabel}${i >= partialFrom ? " (still reporting)" : ""}</title>`;
        // Partial (still-reporting) quarters render hollow so a low bar doesn't
        // read as a real drop; the newest complete quarter gets full weight.
        if (i >= partialFrom) {
            return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${accent}" fill-opacity="0.12" stroke="${accent}" stroke-opacity="0.5" stroke-width="1" stroke-dasharray="2 1.5">${hover}</rect>`;
        }
        const emphasis = i === partialFrom - 1 ? 1 : 0.55;
        return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${accent}" fill-opacity="${emphasis}">${hover}</rect>`;
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

function openFiling(id, ctx) {
    // ctx carries what we already know about the filing (uuid, client, date…)
    // so filings outside the recent sample still render a useful drawer.
    pushDrawer({ kind: "filing", id: Number(id), ctx: ctx || null });
}

/* Official Senate LDA record for a filing — the primary source. */
function ldaFilingURL(uuid) {
    return uuid ? `https://lda.senate.gov/filings/public/filing/${uuid}/print/` : null;
}

function officialLink(uuid, cls = "official-link") {
    const url = ldaFilingURL(uuid);
    if (!url) return null;
    const a = el("a", cls, "Official record ↗");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.title = "Open this filing on the Senate Lobbying Disclosure site";
    a.onclick = e => e.stopPropagation();
    return a;
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
    } else if (state.drawer.kind === "org") {
        renderOrgDetail(body, state.drawer);
    } else if (state.drawer.kind === "client") {
        renderClientDetail(body, state.drawer);
    } else if (state.drawer.kind === "filing") {
        renderFilingDetail(body, state.drawer);
    }
}

function drawerLabel(view) {
    if (view.kind === "signal") return `${CATEGORIES[view.mode].label}: ${displayName(view.mode, view.name)}`;
    if (view.kind === "org") return `Org: ${view.name}`;
    if (view.kind === "client") return `Client: ${clientDisplay(view.name)}`;
    if (view.kind === "filing") return `Filing #${view.id}`;
    return "";
}

/* Organization detail (clients.json — dollar spend, fixed quarter framing) */

function renderOrgDetail(body, view) {
    const all = allOrgMovers();
    const m = all.find(x => x.key === view.key);
    const { cq, bq } = orgQuarterLabels();

    const eyebrow = el("div", "detail-eyebrow");
    eyebrow.appendChild(el("span", "cat-tag clients", "Org"));
    body.appendChild(eyebrow);

    body.appendChild(el("h2", "detail-name", (m && m.name) || view.name || "Organization"));

    if (!m) {
        body.appendChild(el("div", "detail-sub", `${cq} vs ${bq} · complete quarters`));
        body.appendChild(el("p", "detail-empty",
            "This organization doesn't clear the reporting floor for the movers list. It may still appear in recent filings or tag signals."));
        return;
    }

    body.appendChild(el("div", "detail-sub", `${cq} vs ${bq} · complete quarters`));

    const head = buildOrgHeadline(m);
    const summary = el("div", "detail-summary");
    summary.innerHTML = head.html;
    body.appendChild(summary);

    const stats = el("div", "detail-stats");
    const statCells = [
        { value: fmt.money(m.current), label: cq },
        { value: fmt.money(m.baseline), label: bq },
        { value: m.ratio != null ? `${m.ratio.toFixed(2)}×` : "—", label: "Ratio" },
        { value: fmt.int(m.filings_current), label: `Filings, ${cq}` },
    ];
    for (const s of statCells) {
        const stat = el("div", "detail-stat");
        stat.appendChild(el("span", "detail-stat-value", s.value));
        stat.appendChild(el("span", "detail-stat-label", s.label));
        stats.appendChild(stat);
    }
    body.appendChild(stats);

    // Quarterly spend chart, aligned to the shared quarters array.
    const quarters = (state.clients?.quarters || []).map(label => {
        const parts = label.match(/^(\d{4})\s+Q(\d)$/);
        return { label, short: parts ? `${parts[1].slice(2)}Q${parts[2]}` : label };
    });
    if (m.series?.length && quarters.length > 1) {
        const sec = el("div", "detail-section");
        sec.appendChild(el("div", "detail-section-title", "Reported spend by quarter"));
        const box = el("div", "detail-chart-box");
        box.innerHTML = makeBarChart(m.series, quarters, { money: true });
        sec.appendChild(box);
        sec.appendChild(el("p", "detail-chart-note",
            "Quarterly LDA filing income, not issue-allocated — the whole filing's income counts toward this organization's total."));
        body.appendChild(sec);
    }

    // Top topics — pivot into the matching topic signal if it's tracked there.
    if (m.topics?.length) {
        const sec = el("div", "detail-section");
        sec.appendChild(el("div", "detail-section-title", "Top topics"));
        const tags = el("div", "detail-tags");
        const tracked = new Set((state.trends?.topics?.["90d"] || []).map(t => t.name));
        for (const topic of m.topics) {
            const tag = el("button", "detail-tag", topic);
            if (tracked.has(topic)) {
                tag.onclick = () => openSignal("topics", topic);
            } else {
                tag.style.cursor = "default";
                tag.title = "Not currently a tracked signal";
            }
            tags.appendChild(tag);
        }
        sec.appendChild(tags);
        body.appendChild(sec);
    }

    // Registrants
    if (m.registrants?.length) {
        const sec = el("div", "detail-section");
        sec.appendChild(el("div", "detail-section-title", "Registrants"));
        const tags = el("div", "detail-tags");
        for (const r of m.registrants) tags.appendChild(el("span", "detail-tag", r));
        sec.appendChild(tags);
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
        for (const ex of m.examples) {
            const li = el("div", "filing-row");
            const left = el("div");
            left.appendChild(el("div", "filing-row-client", ex.registrant || "Unknown registrant"));
            left.appendChild(el("div", "filing-row-registrant", fmt.dateShort(ex.date)));
            li.appendChild(left);
            const right = el("div", "filing-row-right");
            right.appendChild(el("div", "filing-row-income", fmt.money(ex.income)));
            const link = officialLink(ex.uuid, "official-link small");
            if (link) right.appendChild(link);
            li.appendChild(right);
            list.appendChild(li);
        }
        sec.appendChild(list);
        body.appendChild(sec);
    }
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
        topClients: [], examples: [], income: 0,
        client_count: 0, prev_client_count: 0, yoy_client_count: 0
    };
    const m = items.find(i => i.name === view.name) || fallback;
    const meta = CATEGORIES[m.mode];

    // Eyebrow
    const eyebrow = el("div", "detail-eyebrow");
    eyebrow.appendChild(el("span", `cat-tag ${meta.tagClass}`, meta.label));
    const confChip = el("span", `detail-conf ${m.confidence}`, `${m.confidence} confidence`);
    confChip.title = "Rule-based signal strength: how much volume sits behind this and how consistently it moved against both the prior-period and year-ago baselines.";
    eyebrow.appendChild(confChip);
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
        { value: fmt.int(m.count),                label: "Mentions",
          tip: "Lobbying activity descriptions that reference this in the selected window. One filing can contribute several mentions." },
        { value: fmt.pct(m.current_share_pct),    label: "Share",
          tip: "Share of all tagged mentions in the selected window." },
        { value: fmt.pp(delta),                    label: "Δ Share", cls: deltaDir,
          tip: "Change in share versus the baseline period, in percentage points." },
        { value: fmt.int(baseline),                label: "Baseline",
          tip: "Mentions in the comparison period (year-ago or prior window)." },
        { value: fmt.pct(baselineShare),          label: "Base share",
          tip: "Share of all tagged mentions in the comparison period." },
        { value: m.income > 0 ? fmt.money(m.income) : "—", label: "Assoc. filing income",
          tip: "Combined reported income of filings whose activities mention this. Filings usually cover several issues, so this is NOT spend attributable to this item alone." },
        { value: m.client_count ? fmt.int(m.client_count) : "—", label: "Orgs active",
          tip: "Distinct organizations (name variants folded) whose filings mention this in the selected window." }
    ];
    for (const s of statCells) {
        const stat = el("div", "detail-stat");
        if (s.tip) stat.title = s.tip;
        stat.appendChild(el("span", `detail-stat-value ${s.cls || ""}`.trim(), s.value));
        stat.appendChild(el("span", "detail-stat-label", s.label));
        stats.appendChild(stat);
    }
    body.appendChild(stats);

    // Quarterly history chart (every category)
    {
        const seriesKeyByMode = {
            topics: "topic_series",
            entities: "entity_series",
            legislation: "legislation_series",
            domains: "domain_series"
        };
        const series = state.timeseries?.[seriesKeyByMode[m.mode]]?.[m.name];
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
            li.onclick = () => openFiling(ex.id, ex);
            li.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFiling(ex.id, ex); } };
            const left = el("div");
            left.appendChild(el("div", "filing-row-client", clientDisplay(ex.client) || "Unknown client"));
            if (ex.registrant) left.appendChild(el("div", "filing-row-registrant", clientDisplay(ex.registrant)));
            li.appendChild(left);
            const right = el("div", "filing-row-right");
            right.appendChild(el("div", "filing-row-date", fmt.dateShort(ex.date)));
            const link = officialLink(ex.uuid, "official-link small");
            if (link) right.appendChild(link);
            li.appendChild(right);
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
        // Not in the recent sample — render what the opener told us and always
        // hand off to the primary source.
        const ctx = view.ctx || {};
        const eyebrow = el("div", "detail-eyebrow");
        eyebrow.appendChild(el("span", "cat-tag filing", "Filing"));
        body.appendChild(eyebrow);
        body.appendChild(el("h2", "detail-name", clientDisplay(ctx.client) || `Filing #${view.id}`));
        if (ctx.date || ctx.registrant) {
            body.appendChild(el("div", "detail-sub",
                `${ctx.date ? `Filed ${fmt.dateLong(ctx.date)}` : ""}${ctx.date && ctx.registrant ? " · " : ""}${clientDisplay(ctx.registrant) || ""}`));
        }
        if (ctx.income) {
            const stats = el("div", "detail-stats");
            const stat = el("div", "detail-stat");
            stat.appendChild(el("span", "detail-stat-value", fmt.money(ctx.income)));
            stat.appendChild(el("span", "detail-stat-label", "Income"));
            stats.appendChild(stat);
            body.appendChild(stats);
        }
        const link = officialLink(ctx.uuid, "official-link block");
        if (link) {
            const sec = el("div", "detail-section");
            sec.appendChild(link);
            body.appendChild(sec);
        }
        body.appendChild(el("p", "detail-empty", "Full tag detail is kept for the most recent filings; the official record above has the complete disclosure."));
        return;
    }

    const eyebrow = el("div", "detail-eyebrow");
    eyebrow.appendChild(el("span", "cat-tag filing", "Filing"));
    body.appendChild(eyebrow);

    body.appendChild(el("h2", "detail-name", clientDisplay(f.client) || "Filing"));
    body.appendChild(el("div", "detail-sub", `Filed ${fmt.dateLong(f.date)} · ${clientDisplay(f.registrant) || "Unknown registrant"}`));

    const officialTop = officialLink(f.uuid || view.ctx?.uuid, "official-link block");
    if (officialTop) body.appendChild(officialTop);

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

/* ─── About-the-data modal ─── */

function openAbout() {
    const modal = document.getElementById("about");
    if (!modal) return;
    // Coverage line from live data, so it never goes stale
    const covEl = document.getElementById("about-coverage");
    const start = coverageStartDate();
    const end = dataAsOfDate();
    if (covEl && start) covEl.textContent = `${fmtMonthDayYear(start)} through ${fmtMonthDayYear(end)}`;
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
}

function closeAbout() {
    const modal = document.getElementById("about");
    if (!modal) return;
    modal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
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
    // Query string carries the view (window/compare/category) so any state of
    // the dashboard is a shareable link; the hash carries the open drawer.
    const params = new URLSearchParams();
    if (state.view.window !== "90d") params.set("w", state.view.window);
    if (state.view.compare !== defaultCompare(state.view.window)) params.set("cmp", state.view.compare);
    if (state.view.cat !== "all") params.set("cat", state.view.cat);
    const query = params.toString() ? `?${params.toString()}` : "";

    let hash = "";
    if (state.drawer) {
        if (state.drawer.kind === "signal") {
            hash = `#${state.drawer.mode}/${encodeURIComponent(state.drawer.name)}`;
        } else if (state.drawer.kind === "org") {
            hash = `#org/${encodeURIComponent(state.drawer.key)}`;
        } else if (state.drawer.kind === "client") {
            hash = `#client/${encodeURIComponent(state.drawer.name)}`;
        } else if (state.drawer.kind === "filing") {
            hash = `#filing/${state.drawer.id}`;
        }
    }
    const url = `${window.location.pathname}${query}${hash}`;
    if (url !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
        history.replaceState(null, "", url);
    }
}

function readURL() {
    // View state from the query string
    const q = new URLSearchParams(window.location.search);
    if (["30d", "90d"].includes(q.get("w"))) state.view.window = q.get("w");
    if (["yoy", "prev"].includes(q.get("cmp"))) state.view.compare = q.get("cmp");
    if (q.get("cat") && (q.get("cat") === "recent" || q.get("cat") === "clients" || SIGNAL_MODES.includes(q.get("cat")))) state.view.cat = q.get("cat");

    // Drawer from the hash
    const hash = window.location.hash.replace(/^#/, "");
    if (!hash) return;
    const parts = hash.split("/").map(decodeURIComponent);
    const [kind, ...rest] = parts;
    if (SIGNAL_MODES.includes(kind)) {
        state.drawer = { kind: "signal", mode: kind, name: rest.join("/") };
    } else if (kind === "org") {
        const key = rest.join("/");
        state.drawer = { kind: "org", key, name: key };
    } else if (kind === "client") {
        state.drawer = { kind: "client", name: rest.join("/") };
    } else if (kind === "filing") {
        state.drawer = { kind: "filing", id: Number(rest[0]) };
    }
}

/* Reflect state.view in the three control groups (used after readURL restores
   a shared link's view). */
function syncControlsUI() {
    document.querySelectorAll("#window-seg .seg-btn").forEach(x =>
        x.classList.toggle("active", x.dataset.window === state.view.window));
    document.querySelectorAll("#compare-seg .seg-btn").forEach(x =>
        x.classList.toggle("active", x.dataset.compare === state.view.compare));
    document.querySelectorAll("#cat-row .cat-pill").forEach(x =>
        x.classList.toggle("active", x.dataset.cat === state.view.cat));
}

/* ─── Initialization ─── */

function bindControls() {
    document.getElementById("search-trigger").onclick = openPalette;

    document.querySelectorAll("#window-seg .seg-btn").forEach(b => {
        b.onclick = () => {
            state.view.window = b.dataset.window;
            document.querySelectorAll("#window-seg .seg-btn").forEach(x =>
                x.classList.toggle("active", x === b));
            resolveCompareMode(); // yoy support depends on the window length
            syncURL();
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
            syncURL();
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
            syncURL();
            renderMovers();
        };
    });

    document.querySelectorAll("[data-close-drawer]").forEach(n =>
        n.onclick = () => closeDrawer());

    document.querySelectorAll("[data-close-palette]").forEach(n =>
        n.onclick = () => closePalette());

    document.querySelectorAll("[data-open-about]").forEach(n =>
        n.onclick = () => openAbout());
    document.querySelectorAll("[data-close-about]").forEach(n =>
        n.onclick = () => closeAbout());

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
            if (document.getElementById("about")?.getAttribute("aria-hidden") === "false") { closeAbout(); return; }
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
    const [stats, trends, recent, timeseries, clients] = await Promise.all([
        loadJSON("stats.json"),
        loadJSON("trends.json"),
        loadJSON("recent.json"),
        loadJSON("timeseries.json"),
        loadJSON("clients.json")
    ]);

    state.stats = stats || null;
    state.trends = trends || null;
    state.filings = recent?.filings || [];
    state.timeseries = timeseries || null;
    state.clients = clients || null;

    // clients.json is new and may not exist yet on a live deploy mid-rollout
    // (new app.js, old data). Hide the Organizations pill and skip it
    // everywhere else rather than show a broken/empty view.
    const clientsPill = document.getElementById("cat-pill-clients");
    if (clientsPill) {
        if (orgMoversAvailable()) {
            clientsPill.style.display = "";
        } else {
            clientsPill.style.display = "none";
            if (state.view.cat === "clients") state.view.cat = "all";
        }
    }

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
    readURL();            // restore shared view + drawer state from URL
    resolveCompareMode(); // may override compare if coverage can't back it
    syncControlsUI();
    renderHero();
    renderMovers();

    if (state.drawer) {
        renderDrawer();
        showDrawer();
    }
}

document.addEventListener("DOMContentLoaded", init);
