/* ══════════════════════════════════════════════
   Lobbying Signals — Editorial dashboard
   Single-page flow with command palette + drawer
   ══════════════════════════════════════════════ */

const DATA_PATH = "data";

const CATEGORIES = {
    clients:     { label: "Org",     plural: "Organizations", tagClass: "clients",   shortLabel: "Org" },
    topics:      { label: "Topic",   plural: "Topics",      tagClass: "topics",      shortLabel: "Topic" },
    entities:    { label: "Agency",  plural: "Agencies",    tagClass: "entities",    shortLabel: "Agency" },
    legislation: { label: "Bill",    plural: "Bills",       tagClass: "legislation", shortLabel: "Bill" }
};

// Tag-mention categories exported per comparison frame in trends.json.
// Organizations ("clients") come from clients.json but share the SAME two
// frames, so one toggle drives every view.
const SIGNAL_MODES = ["topics", "entities", "legislation"];

// Both comparison frames are year-over-year and report-quarter based:
// "quarter" = latest complete report quarter vs the same quarter last year;
// "qtd" = the current partial quarter so far vs the same point last year.
const FRAME_KEYS = ["quarter", "qtd"];

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

    view: {
        frame: "quarter",   // quarter | qtd — the shared comparison frame
        cat: "all",         // all | clients | topics | entities | legislation | recent
        normalized: false   // constant-dollar money + share-of-quarter mentions
    },

    drawer: null,        // current drawer view
    drawerStack: [],     // breadcrumb history within drawer
    histDepth: 0,        // history entries pushed above the page entry
    closingDrawer: false,
    loaded: false,       // every data file has arrived (or failed)
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
    // Safe for both text content and quoted attribute values.
    esc: t => (t == null ? "" : String(t)).replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c])
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

function fmtMonthDay(d) {
    if (!d) return "—";
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function fmtMonthDayYear(d) {
    if (!d) return "—";
    return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

// U+2060 word joiners keep "Apr–Jun" from wrapping at the dash.
const QUARTER_MONTHS = { 1: "Jan\u2060–\u2060Mar", 2: "Apr\u2060–\u2060Jun", 3: "Jul\u2060–\u2060Sep", 4: "Oct\u2060–\u2060Dec" };

// "Q2 2026" / "2026 Q2" / "Q2 2026 so far" -> "Q2 2026 (Apr–Jun)". Returns
// the input unchanged when it isn't a recognizable quarter label.
function quarterWithMonths(label) {
    const s = String(label || "");
    let m = s.match(/Q([1-4])\s+(\d{4})/);
    let year, quarter;
    if (m) { quarter = m[1]; year = m[2]; }
    else {
        m = s.match(/(\d{4})\s+Q([1-4])/);
        if (!m) return s;
        year = m[1]; quarter = m[2];
    }
    return `Q${quarter} ${year} (${QUARTER_MONTHS[quarter]})`;
}

// Legal-form suffixes stripped from organization names in running text
// ("Anthropic, PBC" -> "Anthropic"). Drawer titles keep the full name.
const LEGAL_SUFFIX_RE = /,?\s+(?:Inc\.?|Incorporated|L\.?L\.?C\.?|L\.?L\.?P\.?|L\.?P\.?|Corp\.?|Corporation|Co\.|Ltd\.?|Limited|P\.?B\.?C\.?|PLC|P\.?C\.?|N\.?A\.?)$/i;

function shortOrgName(name) {
    let s = String(name || "").trim();
    for (let i = 0; i < 3; i++) {
        const next = s.replace(LEGAL_SUFFIX_RE, "").trim();
        if (next === s || !next) break;
        s = next;
    }
    return s;
}

/* ─── Comparison frames ───
   Both frames come from trends.json and drive every view through one toggle. */

function frameInfo(frameKey) {
    return state.trends?.frames?.[frameKey] || null;
}

function activeFrameKey() {
    return state.view.frame;
}

/* ─── Normalization ───
   One switch, two effects, both aimed at "what shifted relatively":
   dollars become constant base-year dollars (CPI-U deflators shipped in
   timeseries.json), and mention histories become each quarter's share of
   tagged activity instead of raw counts. Individual filing records stay
   as filed — only aggregates and comparisons are adjusted. */

function deflatorMeta() {
    const d = state.timeseries?.deflators;
    return d && d.factors ? d : null;
}

function normalizedOn() {
    return state.view.normalized && !!deflatorMeta();
}

// Accepts "2026 Q1", "Q1 2026", or "Q1 2026 so far"; 1 when off or unknown.
function deflatorFor(label) {
    if (!normalizedOn() || !label) return 1;
    const s = String(label);
    let year, quarter;
    let m = s.match(/(\d{4})\s+Q([1-4])/);
    if (m) { year = m[1]; quarter = m[2]; }
    else {
        m = s.match(/Q([1-4])\s+(\d{4})/);
        if (m) { year = m[2]; quarter = m[1]; }
    }
    if (!year) return 1;
    return deflatorMeta().factors[`${year} Q${quarter}`] || 1;
}

function normBaseYear() {
    return deflatorMeta()?.base_year || null;
}

const NORMALIZED_MENTIONS_LABEL = "Mentions as a share of all tagged activity in each quarter.";

function normalizationExplanation(cat = state.view.cat) {
    const dollars = `Dollars adjusted for inflation (CPI-U, ${normBaseYear()} dollars).`;
    const mentions = NORMALIZED_MENTIONS_LABEL;
    if (cat === "clients") return [dollars];
    if (SIGNAL_MODES.includes(cat)) return [mentions];
    return [dollars, mentions];
}

function updateNormExplainer() {
    const text = document.getElementById("norm-explainer");
    if (!text || !deflatorMeta()) return;
    text.replaceChildren(...normalizationExplanation().map(line => el("p", "", line)));
}

function bindNormHelp() {
    const info = document.getElementById("norm-info");
    const text = document.getElementById("norm-explainer");
    const control = document.getElementById("norm-control");
    if (!info || !text || !control) return;
    let pinned = false;
    let closeTimer;
    const show = open => {
        clearTimeout(closeTimer);
        text.hidden = !open;
        info.setAttribute("aria-expanded", open ? "true" : "false");
    };
    info.onmouseenter = () => show(true);
    control.onmouseenter = () => clearTimeout(closeTimer);
    control.onmouseleave = () => {
        if (!pinned && document.activeElement !== info) closeTimer = setTimeout(() => show(false), 150);
    };
    info.onfocus = () => show(true);
    info.onblur = () => { pinned = false; show(false); };
    info.onclick = event => {
        event.stopPropagation();
        pinned = !pinned;
        show(pinned);
    };
    document.addEventListener("pointerdown", event => {
        if (!control.contains(event.target)) { pinned = false; show(false); }
    });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && !text.hidden) {
            pinned = false;
            show(false);
            event.preventDefault();
            event.stopPropagation();
        }
    }, true);
}

function buildNormToggle() {
    const btn = document.getElementById("norm-toggle");
    if (!btn) return;
    const meta = deflatorMeta();
    const control = document.getElementById("norm-control");
    if (control) control.hidden = !meta;
    if (!meta) { btn.hidden = true; return; }
    btn.hidden = false;
    btn.querySelector(".norm-label").textContent = "Normalized";
    updateNormExplainer();
    bindNormHelp();
    btn.classList.toggle("active", state.view.normalized);
    btn.setAttribute("aria-pressed", state.view.normalized ? "true" : "false");
    btn.onclick = () => {
        state.view.normalized = !state.view.normalized;
        try { localStorage.setItem("lsNormalized", state.view.normalized ? "1" : "0"); } catch (e) {}
        btn.classList.toggle("active", state.view.normalized);
        btn.setAttribute("aria-pressed", state.view.normalized ? "true" : "false");
        syncURL();
        renderHero();
        renderMovers();
        if (state.drawer) renderDrawer();
    };
}

// Toggle-button label, e.g. "Q1 2026 vs Q1 2025" / "Q2 2026 so far".
function frameToggleLabel(frameKey) {
    const f = frameInfo(frameKey);
    if (!f) return frameKey === "qtd" ? "This quarter so far" : "Latest complete quarter";
    return frameKey === "quarter" ? `${f.label} vs ${f.baseline_label}` : f.label;
}

// Long plain-words description for the movers subtitle.
function frameSubtitle(frameKey) {
    const f = frameInfo(frameKey);
    if (!f) return "";
    if (frameKey === "quarter") {
        return `${quarterWithMonths(f.label)}, latest complete quarter · compared with ${f.baseline_label}`;
    }
    const through = fmtMonthDay(parseDate(f.through));
    const base = quarterWithMonths(f.label.replace(/\s+so far$/i, ""));
    let text = `${base}, reports still arriving · filed through ${through} vs the same point last year`;
    if (f.thin_data) text += " · small sample early in the filing period";
    return text;
}

// Short baseline phrase used inside card headlines, e.g. "Q1 2025" /
// "the same point in Q2 2025".
function frameBaselinePhrase(frameKey) {
    const f = frameInfo(frameKey);
    if (!f) return "a year earlier";
    return frameKey === "quarter" ? f.baseline_label : `the ${f.baseline_label}`;
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

/* Tag mover headline: organizations first, mentions second. "1,211
   organizations lobbied on this" answers the reader's real question (how
   broad is this?) better than a raw mention count does. Falls back to
   mention phrasing when org counts are missing. */
function buildHeadline(item, frameKey) {
    const orgs = toNum(item.client_count);
    if (!orgs) return buildMentionsHeadline(item, frameKey);

    const baseOrgs = toNum(item.baseline_client_count);
    const basePhrase = fmt.esc(frameBaselinePhrase(frameKey));
    // "in Q1 2025" vs "at the same point in Q2 2025"
    const baseIn = frameKey === "qtd" ? `at ${basePhrase}` : `in ${basePhrase}`;
    const soFar = frameKey === "qtd" ? " so far this quarter" : "";

    if (baseOrgs === 0) {
        return {
            html: `<strong>${fmt.int(orgs)}</strong> organizations lobbied on this${soFar} — <span class="delta delta-up-x">new</span>, none ${baseIn}.`,
            dir: "up"
        };
    }

    const ratio = orgs / baseOrgs;
    const intensity = deltaIntensity(ratio);
    let deltaHtml, dir;
    if (ratio >= 1.02) {
        deltaHtml = `<span class="delta delta-up${intensity}">up from ${fmt.int(baseOrgs)}</span>`;
        dir = "up";
    } else if (ratio <= 0.98) {
        deltaHtml = `<span class="delta delta-down${intensity}">down from ${fmt.int(baseOrgs)}</span>`;
        dir = "down";
    } else {
        deltaHtml = `<span class="delta delta-flat">about even with ${fmt.int(baseOrgs)}</span>`;
        dir = "flat";
    }
    const orgWord = orgs === 1 ? "organization" : "organizations";
    return {
        html: `<strong>${fmt.int(orgs)}</strong> ${orgWord} lobbied on this${soFar} — ${deltaHtml} ${baseIn}.`,
        dir
    };
}

// Mentions-only fallback for tags with no organization counts.
function buildMentionsHeadline(item, frameKey) {
    const current = toNum(item.count);
    const baseline = toNum(item.baseline_count);
    const basePhrase = fmt.esc(frameBaselinePhrase(frameKey));
    const baseIn = frameKey === "qtd" ? `at ${basePhrase}` : `in ${basePhrase}`;
    const noun = current === 1 ? "mention" : "mentions";

    if (baseline === 0 && current > 0) {
        return {
            html: `<strong>${fmt.int(current)}</strong> ${noun} — <span class="delta delta-up-x">new</span>, none ${baseIn}.`,
            dir: "up"
        };
    }
    if (current === 0 && baseline > 0) {
        return {
            html: `<strong>0</strong> mentions, vs <strong>${fmt.int(baseline)}</strong> ${baseIn}.`,
            dir: "down"
        };
    }
    if (baseline === 0 && current === 0) {
        return { html: `No activity in this frame.`, dir: "flat" };
    }
    const ratio = current / baseline;
    const pctChange = (ratio - 1) * 100;
    const intensity = deltaIntensity(ratio);
    if (Math.abs(pctChange) >= 5) {
        const word = pctChange > 0 ? "up" : "down";
        const dirCls = pctChange > 0 ? `delta-up${intensity}` : `delta-down${intensity}`;
        return {
            html: `<strong>${fmt.int(current)}</strong> ${noun} — <span class="delta ${dirCls}">${word} ${Math.abs(pctChange).toFixed(0)}%</span> vs ${basePhrase} (${fmt.int(baseline)}).`,
            dir: pctChange > 0 ? "up" : "down"
        };
    }
    return {
        html: `<strong>${fmt.int(current)}</strong> ${noun} — <span class="delta delta-flat">about even</span> vs ${basePhrase} (${fmt.int(baseline)}).`,
        dir: "flat"
    };
}

// Shared baseline and new-signal logic for cards and the drawer stat grid.
function signalComparison(item, frameKey) {
    const current = toNum(item.count);
    const baseline = toNum(item.baseline_count);
    return {
        current, baseline,
        isNew: current > 0 && baseline === 0,
        baselineTiming: frameKey === "qtd" ? "at this point last year" : "a year ago",
        share: item.current_share_pct == null ? null : toNum(item.current_share_pct),
        delta: item.share_delta_pp == null ? null : toNum(item.share_delta_pp)
    };
}

function buildSecondaryLine(item, frameKey) {
    const comparison = signalComparison(item, frameKey);
    const noun = comparison.current === 1 ? "mention" : "mentions";
    const parts = [`${fmt.int(comparison.current)} ${noun}`];
    if (!comparison.isNew) parts[0] += ` vs ${fmt.int(comparison.baseline)} ${comparison.baselineTiming}`;
    if (comparison.share != null) {
        let share = `${comparison.share.toFixed(1)}% of ${comparison.isNew ? "all " : ""}activity`;
        if (!comparison.isNew && comparison.delta != null) {
            share += ` (${comparison.delta > 0 ? "+" : ""}${comparison.delta.toFixed(1)} pp)`;
        }
        parts.push(share);
    }
    if (comparison.isNew) parts.push(`none ${comparison.baselineTiming}`);
    return parts.join(" · ");
}

/* ─── Organization movers (clients.json) ───
   Dollar spend per organization under the SAME two frames as the tag views:
   latest complete quarter vs the same quarter a year ago, or the current
   partial quarter so far vs the same point last year. */

function clientFrame(frameKey) {
    return state.clients?.frames?.[frameKey || activeFrameKey()] || null;
}

function orgMoversAvailable(frameKey) {
    const c = clientFrame(frameKey);
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

function allOrgMovers(frameKey) {
    const c = clientFrame(frameKey);
    if (!c) return [];
    return [
        ...(c.risers || []).map(m => tagOrgMover(m, "riser")),
        ...(c.fallers || []).map(m => tagOrgMover(m, "faller")),
        ...(c.new_entrants || []).map(m => tagOrgMover(m, "new")),
    ];
}

// Movers plus the always-exported top spenders ("majors") — the pool for
// drawer lookups and search, where being a mover shouldn't gate whether an
// organization is inspectable at all. The movers feed itself stays
// movers-only.
function allOrgRecords(frameKey) {
    const c = clientFrame(frameKey);
    if (!c) return [];
    return [
        ...allOrgMovers(frameKey),
        ...(c.majors || []).map(m => tagOrgMover(m, "major")),
    ];
}

// Risers + new entrants only (no fallers), ranked by dollar delta — the
// ramp-up story used by the hero headline.
function topOrgRisersAndNewEntrants(limit, frameKey) {
    const c = clientFrame(frameKey);
    if (!c) return [];
    const pool = [
        ...(c.risers || []).map(m => tagOrgMover(m, "riser")),
        ...(c.new_entrants || []).map(m => tagOrgMover(m, "new")),
    ];
    return pool.sort((a, b) => b.delta - a.delta).slice(0, limit);
}

function orgQuarterLabels(frameKey) {
    const fk = frameKey || activeFrameKey();
    const c = clientFrame(fk);
    const cqRaw = c?.current_quarter?.label || "the latest quarter";
    const bqRaw = c?.baseline_quarter?.label || "the year-ago quarter";
    if (fk === "qtd") {
        const bq = `the same point in ${bqRaw}`;
        return { cq: `${cqRaw} so far`, bq, inBq: `at ${bq}` };
    }
    return { cq: cqRaw, bq: bqRaw, inBq: `in ${bqRaw}` };
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

// "due Apr 20" before the deadline, "were due Apr 20" for ~10 days after
// it; null once it's stale so the hero doesn't repeat it indefinitely.
function reportsDueLabel(year, quarter) {
    const deadline = reportsDueDate(year, quarter);
    const now = new Date();
    const graceEnd = new Date(deadline.getTime() + 10 * 86400000);
    if (now >= graceEnd) return null;
    const day = deadline.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    // The deadline is the end of the 20th, so "were due" starts on the 21st.
    return now >= new Date(deadline.getTime() + 86400000) ? `were due ${day}` : `due ${day}`;
}

// Frame-leg dollar values for an org mover, deflated when normalization is
// on (each leg by its own quarter's factor, so YoY deltas become real).
function orgFrameValues(m, frameKey) {
    const c = clientFrame(frameKey || activeFrameKey());
    return {
        current: toNum(m.current) * deflatorFor(c?.current_quarter?.label),
        baseline: toNum(m.baseline) * deflatorFor(c?.baseline_quarter?.label)
    };
}

function buildOrgHeadline(m, frameKey) {
    const labels = orgQuarterLabels(frameKey);
    const cq = fmt.esc(labels.cq), bq = fmt.esc(labels.bq), inBq = fmt.esc(labels.inBq);
    const { current, baseline } = orgFrameValues(m, frameKey);

    if (baseline === 0 && current > 0) {
        return {
            html: `<strong>${fmt.money(current)}</strong> in ${cq} — <span class="delta delta-up-x">new</span> — no lobbying ${inBq}.`,
            dir: "up"
        };
    }
    if (current === 0 && baseline > 0) {
        return {
            html: `<strong>$0</strong> in ${cq}, vs <strong>${fmt.money(baseline)}</strong> ${inBq}.`,
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
    const word = pctChange >= 0 ? "up" : "down";
    const dirCls = pctChange >= 0 ? `delta-up${intensity}` : `delta-down${intensity}`;
    return {
        html: `<strong>${fmt.money(current)}</strong> in ${cq} — <span class="delta ${dirCls}">${word} ${Math.abs(pctChange).toFixed(0)}%</span> vs ${bq} (${fmt.money(baseline)}).`,
        dir: pctChange >= 0 ? "up" : "down"
    };
}

function makeOrgTrendChart(current, baseline, dir) {
    const W = 200, H = 48;
    const padT = 12, padB = 11;
    const plotH = H - padT - padB;
    const baseLine = H - padB;

    const accent = dir === "up"
        ? getCSSVar("--up", "#23664a")
        : dir === "down"
            ? getCSSVar("--down", "#8f2a2a")
            : getCSSVar("--accent", "#1e3a5f");
    const muted = getCSSVar("--ink-4", "#a29a84");
    const labelColor = getCSSVar("--ink-3", "#6e6a5a");
    const valueColor = getCSSVar("--ink-2", "#44506b");

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
        `<text x="${cx.toFixed(1)}" y="${(baseLine - Math.max(val > 0 ? 2 : 0, (val / yMax) * plotH) - 3).toFixed(1)}" text-anchor="middle" font-family="IBM Plex Mono,monospace" font-size="9" font-weight="600" fill="${valueColor}">${fmt.money(val)}</text>`;
    const periodText = (cx, label, color, weight) =>
        `<text x="${cx.toFixed(1)}" y="${H - 2}" text-anchor="middle" font-family="IBM Plex Mono,monospace" font-size="8" fill="${color}" font-weight="${weight}" letter-spacing="0.05em">${label}</text>`;

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

    const head = buildOrgHeadline(m, activeFrameKey());
    const headlineEl = el("div", "mover-headline");
    headlineEl.innerHTML = head.html;
    main.appendChild(headlineEl);

    if (m.method_changed) appendMethodWarning(main);

    if (m.topics?.length) {
        main.appendChild(el("div", "mover-clients", m.topics.slice(0, 3).join(" · ")));
    }

    card.appendChild(main);

    const trendSlot = el("div", "mover-trend");
    const vals = orgFrameValues(m, activeFrameKey());
    trendSlot.innerHTML = makeOrgTrendChart(vals.current, vals.baseline, head.dir);
    card.appendChild(trendSlot);

    card.appendChild(el("div", "mover-arrow", "→"));
    return card;
}

function buildAnyMoverCard(m, frameKey) {
    if (m.mode === "clients") return buildOrgMoverCard(m);
    return buildMoverCard(m, frameKey);
}

/* ─── Mover items ─── */

function getKeyMap(mode) {
    // trends.json key names: topic_*, entity_*, legislation_*
    if (mode === "topics") return { c: "topic_clients", e: "topic_examples", i: "topic_income" };
    if (mode === "entities") return { c: "entity_clients", e: "entity_examples", i: "entity_income" };
    if (mode === "legislation") return { c: "legislation_clients", e: "legislation_examples", i: "legislation_income" };
    return null;
}

function getCategoryItemsFixed(mode, frameKey) {
    const meta = CATEGORIES[mode];
    const keys = getKeyMap(mode);
    if (!meta || !keys) return [];
    const items = state.trends?.[mode]?.[frameKey] || [];
    const clients = state.trends?.[keys.c]?.[frameKey] || {};
    const examples = state.trends?.[keys.e]?.[frameKey] || {};
    const income = state.trends?.[keys.i]?.[frameKey] || {};

    return items.map(item => ({
        mode,
        name: item.name,
        count: toNum(item.count),
        baseline_count: toNum(item.baseline_count),
        client_count: toNum(item.client_count),
        baseline_client_count: toNum(item.baseline_client_count),
        current_share_pct: toNum(item.current_share_pct),
        baseline_share_pct: toNum(item.baseline_share_pct),
        share_delta_pp: toNum(item.share_delta_pp),
        ratio: item.ratio == null ? null : toNum(item.ratio),
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

function buildMovers(catFilter, frameKey) {
    const modes = (catFilter === "all" || !catFilter) ? SIGNAL_MODES : [catFilter];
    const all = [];
    const seen = new Map();
    for (const mode of modes) {
        for (const it of getCategoryItemsFixed(mode, frameKey)) {
            // Dedupe by canonical display name; keep the higher-count entry
            const key = canonicalKey(mode, it.name);
            const existing = seen.get(key);
            if (!existing) {
                seen.set(key, it);
                all.push(it);
            } else if (it.count > existing.count) {
                const idx = all.indexOf(existing);
                if (idx >= 0) all[idx] = it;
                seen.set(key, it);
            }
        }
    }
    // Rank by |score| — big decliners are stories too, not just gainers.
    all.sort((a, b) => Math.abs(b.score) - Math.abs(a.score));
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
    // with positive deltas, deduped by canonical name. The hero always reads
    // from the complete-quarter frame — the headline comparison.
    const allMovers = buildMovers("all", "quarter")
        .filter(m => m.share_delta_pp > 0 && m.count >= 100);
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

    // Organizations lead when clients.json is available; the tag-mention
    // picks above are the fallback. Always the complete-quarter frame.
    // Names are separated by semicolons because legal names carry commas.
    const orgPicks = topOrgRisersAndNewEntrants(3, "quarter");
    const heroCq = fmt.esc(quarterWithMonths(clientFrame("quarter")?.current_quarter?.label
        || frameInfo("quarter")?.label || ""));
    let headline = null;
    if (orgPicks.length) {
        const names = orgPicks.map(m => `<em>${fmt.esc(shortOrgName(m.name))}</em>`).join("; ");
        const noun = orgPicks.length === 1 ? "increase" : "increases";
        headline = `Largest year-over-year ${noun} in reported lobbying, ${heroCq}: ${names}.`;
    } else if (state.loaded && moverNames.length) {
        const noun = moverNames.length === 1 ? "gain" : "gains";
        headline = `Largest ${noun} in share of lobbying mentions, ${heroCq}: ${moverNames.join("; ")}.`;
    } else if (state.loaded) {
        headline = `${fmt.int(stats.total_filings || 0)} federal lobbying filings tracked.`;
    }
    // Before every file has arrived, keep the loading text rather than
    // flash a fallback headline that the full data would replace.
    if (headline) headlineEl.innerHTML = headline;

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
    // The partial quarter comes from stats.current_partial_quarter (the
    // calendar quarter after the latest complete one) — NOT the newest
    // quarter carrying filings, which early termination reports filed for
    // future periods would skew.
    const partial = stats.current_partial_quarter;
    if (partial) {
        let partialLabel = `${fmt.num(partial.filings)} filings so far · partial quarter`;
        const due = reportsDueLabel(partial.year, partial.quarter);
        if (due) partialLabel += ` · reports ${due}`;
        statItems.push({
            value: `Q${partial.quarter} ${partial.year}`,
            label: partialLabel
        });
    } else if (latest) {
        const isPartial = partialN > 0 && latest === quarters[quarters.length - 1];
        let partialLabel = `${fmt.num(latest.filings)} filings so far · partial quarter`;
        if (isPartial) {
            const due = reportsDueLabel(latest.year, latest.quarter);
            if (due) partialLabel += ` · reports ${due}`;
        }
        statItems.push({
            value: `Q${latest.quarter} ${latest.year}`,
            label: isPartial ? partialLabel : `${fmt.num(latest.filings)} filings · ${fmt.money(latest.income * deflatorFor(`${latest.year} Q${latest.quarter}`))}`
        });
    }

    // Latest COMPLETE quarter, dollars, year-over-year — the real headline
    // number ("$1.63B reported, +10.7% vs the same quarter last year").
    // Normalized mode deflates each leg by its own quarter and recomputes
    // the change, so the arrow reflects real-dollar movement.
    const lcq = stats.latest_complete_quarter;
    if (lcq && lcq.income_change_pct != null) {
        const dCur = deflatorFor(lcq.label);
        const dBase = deflatorFor(`Q${lcq.quarter} ${lcq.year - 1}`);
        const income = lcq.income * dCur;
        const baseIncome = (lcq.yoy_income || 0) * dBase;
        const change = normalizedOn() && baseIncome > 0
            ? (income / baseIncome - 1) * 100
            : lcq.income_change_pct;
        const dir = change > 0 ? "up" : change < 0 ? "down" : "";
        const sign = change > 0 ? "↑ " : change < 0 ? "↓ " : "";
        // Every part of the meaning is visible text (no hover on phones):
        // what the dollars are, which quarter, and the comparison.
        statItems.push({
            value: fmt.money(income),
            label: `reported lobbying, Q${lcq.quarter} ${lcq.year}${normalizedOn() ? ` (${normBaseYear()} dollars)` : ""}`,
            change: `${sign}${Math.abs(change).toFixed(1)}% vs Q${lcq.quarter} ${lcq.year - 1}`,
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
        const v = el("span", `stat-value ${!s.change && s.trend ? `stat-trend ${s.trend}` : ""}`.trim(), s.value);
        const l = el("span", "stat-label", s.label);
        li.appendChild(v);
        li.appendChild(l);
        if (s.change) li.appendChild(el("span", `stat-label stat-change ${s.trend ? `stat-trend ${s.trend}` : ""}`.trim(), s.change));
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
    const frame = state.view.frame;

    updateControlsForCat(cat);

    if (cat === "recent") {
        sub.textContent = `Latest ${fmt.int(state.filings.length)} filings, most recent first`;
        renderRecentList(list);
        return;
    }

    sub.textContent = frameSubtitle(frame);
    // Normalization leaves the subtitle layout unchanged.
    sub.title = normalizedOn()
        ? normalizationExplanation(cat).join("\n")
        : "";

    if (cat === "all") {
        renderEverythingDigest(list, frame);
        return;
    }

    if (cat === "clients") {
        renderOrgMovers(list, frame);
        return;
    }

    const movers = buildMovers(cat, frame).slice(0, 50);
    if (!movers.length) {
        const empty = el("div", "mover-empty");
        empty.textContent = "No signals match this frame.";
        list.appendChild(empty);
        return;
    }

    // Lead with a scannable top set; the long tail expands on demand.
    const VISIBLE = 20;
    for (const m of movers.slice(0, VISIBLE)) {
        list.appendChild(buildMoverCard(m, frame));
    }
    if (movers.length > VISIBLE) {
        const more = el("button", "mover-more");
        more.type = "button";
        more.textContent = `Show all ${movers.length} movers`;
        more.onclick = () => {
            more.remove();
            for (const m of movers.slice(VISIBLE)) {
                list.appendChild(buildMoverCard(m, frame));
            }
        };
        list.appendChild(more);
    }
}

/* "Everything" is a sectioned digest, not an interleaved ranked list: the
   top few movers from each category under their own header, using the same
   cards as the category's own tab. */
function renderEverythingDigest(list, frame) {
    const sections = [];

    if (orgMoversAvailable(frame)) {
        const ups = topOrgRisersAndNewEntrants(50, frame)
            .sort((a, b) => b._absDelta - a._absDelta)
            .slice(0, 5);
        const downs = (clientFrame(frame)?.fallers || [])
            .map(m => tagOrgMover(m, "faller"))
            .sort((a, b) => b._absDelta - a._absDelta)
            .slice(0, 2);
        sections.push({ cat: "clients", label: "Organizations", movers: [...ups, ...downs] });
    }

    sections.push({ cat: "topics", label: "Topics", movers: buildMovers("topics", frame).slice(0, 4) });
    sections.push({ cat: "legislation", label: "Bills", movers: buildMovers("legislation", frame).slice(0, 4) });
    sections.push({ cat: "entities", label: "Agencies", movers: buildMovers("entities", frame).slice(0, 3) });

    let rendered = 0;
    for (const sec of sections) {
        if (!sec.movers.length) continue;
        const head = el("li", "mover-section-head");
        head.appendChild(el("span", "mover-section-title", sec.label));
        const seeAll = el("button", "mover-see-all", "See all →");
        seeAll.type = "button";
        seeAll.onclick = () => switchCat(sec.cat);
        head.appendChild(seeAll);
        list.appendChild(head);
        for (const m of sec.movers) {
            list.appendChild(buildAnyMoverCard(m, frame));
        }
        rendered += sec.movers.length;
    }

    if (!rendered) {
        list.appendChild(el("div", "mover-empty", "No signals match this frame."));
    }
}

function switchCat(cat) {
    state.view.cat = cat;
    syncControlsUI();
    syncURL();
    renderMovers();
    window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderOrgMovers(list, frame) {
    if (!orgMoversAvailable(frame)) {
        list.appendChild(el("div", "mover-empty", "Organization spend data isn't available in this build."));
        return;
    }

    const movers = allOrgMovers(frame).sort((a, b) => b._absDelta - a._absDelta);
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

// The frame toggle applies to every comparison view. On Recent filings —
// a plain chronological list with nothing to compare — it renders disabled
// but stays put, so the controls never move or vanish between tabs.
function updateControlsForCat(cat) {
    updateNormExplainer();
    const frameSeg = document.getElementById("frame-seg");
    if (!frameSeg) return;
    const off = cat === "recent";
    frameSeg.classList.toggle("seg-disabled", off);
    frameSeg.setAttribute("aria-disabled", off ? "true" : "false");
    frameSeg.querySelectorAll(".seg-btn").forEach(b => { b.disabled = off; });
}

function renderRecentList(list) {
    const filings = state.filings;
    if (!filings.length) {
        const empty = el("div", "mover-empty", "No recent filings available.");
        list.appendChild(empty);
        return;
    }
    const VISIBLE = 60;
    for (const f of filings.slice(0, VISIBLE)) {
        list.appendChild(buildFilingCard(f));
    }
    if (filings.length > VISIBLE) {
        const more = el("button", "mover-more");
        more.type = "button";
        more.textContent = `Show all ${fmt.int(filings.length)} filings`;
        more.onclick = () => {
            more.remove();
            for (const f of filings.slice(VISIBLE)) list.appendChild(buildFilingCard(f));
        };
        list.appendChild(more);
    }
}

/* Official titles arrive through the same name string as curated titles.
   Keep links for all eight bill types, with or without an official title. */
const BILL_LINK_RE = /^(H\.R\.|S\.|H\.Res\.|S\.Res\.|H\.J\.Res\.|S\.J\.Res\.|H\.Con\.Res\.|S\.Con\.Res\.)\s*(\d{1,5})\s*\((\d{1,3}(?:st|nd|rd|th))\s+Congress\)(?: — .*)?$/i;

function congressGovURL(name) {
    const m = BILL_LINK_RE.exec(String(name || "").trim());
    if (!m) return null;
    const chamber = {
        "H.R.": "house-bill", "S.": "senate-bill",
        "H.RES.": "house-resolution", "S.RES.": "senate-resolution",
        "H.J.RES.": "house-joint-resolution", "S.J.RES.": "senate-joint-resolution",
        "H.CON.RES.": "house-concurrent-resolution", "S.CON.RES.": "senate-concurrent-resolution"
    }[m[1].toUpperCase()];
    return `https://www.congress.gov/bill/${m[3].toLowerCase()}-congress/${chamber}/${m[2]}`;
}

function congressGovLink(name, cls = "official-link small") {
    const url = congressGovURL(name);
    if (!url) return null;
    const a = el("a", cls, "congress.gov →");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.title = "Open this bill on congress.gov";
    a.onclick = e => e.stopPropagation();
    return a;
}

function buildMoverCard(m, frameKey) {
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

    const head = buildHeadline(m, frameKey);
    const headlineEl = el("div", "mover-headline");
    headlineEl.innerHTML = head.html;
    main.appendChild(headlineEl);

    const secondary = buildSecondaryLine(m, frameKey);
    if (secondary) {
        main.appendChild(el("div", "mover-clients", secondary));
    }

    if (m.mode === "legislation") {
        const link = congressGovLink(m.name);
        if (link) {
            const row = el("div", "mover-clients");
            row.appendChild(link);
            main.appendChild(row);
        }
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

    // Baseline-vs-now bar pair: organization counts when available (the
    // headline metric), mentions otherwise.
    const trendSlot = el("div", "mover-trend");
    const accent = head.dir === "up"
        ? getCSSVar("--up", "#23664a")
        : head.dir === "down"
            ? getCSSVar("--down", "#8f2a2a")
            : getCSSVar("--accent", "#1e3a5f");
    trendSlot.innerHTML = makeTrendChart(m, { accent });
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

function makeTrendChart(item, options = {}) {
    // Two periods, two numbers → two labeled bars. Both values are printed, so
    // the chart carries its own axis; a line through two points implied a time
    // axis that didn't exist. Quarter-level history lives in the drawer.
    // Bars plot organization counts (the headline metric); mentions only when
    // org counts are missing.
    const W = 200, H = 48;
    const padT = 12;   // room for value labels above bars
    const padB = 11;   // room for period labels below
    const plotH = H - padT - padB;
    const baseline = H - padB;

    const accent = options.accent || getCSSVar("--accent", "#1e3a5f");
    const muted  = getCSSVar("--ink-4", "#a29a84");
    const labelColor = getCSSVar("--ink-3", "#6e6a5a");
    const valueColor = getCSSVar("--ink-2", "#44506b");

    const useOrgs = toNum(item.client_count) > 0;
    const baseVal = useOrgs ? toNum(item.baseline_client_count) : toNum(item.baseline_count);
    const nowVal = useOrgs ? toNum(item.client_count) : toNum(item.count);
    const yMax = Math.max(baseVal, nowVal, 1);
    const barW = 38;
    const bx = W * 0.32, nx = W * 0.68; // bar centers
    const baseLabel = "yr ago";

    const bar = (cx, val, fill, opacity) => {
        const h = Math.max(val > 0 ? 2 : 0, (val / yMax) * plotH);
        const y = baseline - h;
        return `<rect x="${(cx - barW / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${barW}" height="${h.toFixed(1)}" rx="2" fill="${fill}" fill-opacity="${opacity}"/>`;
    };
    const valueText = (cx, val) =>
        `<text x="${cx.toFixed(1)}" y="${(baseline - Math.max(val > 0 ? 2 : 0, (val / yMax) * plotH) - 3).toFixed(1)}" text-anchor="middle" font-family="IBM Plex Mono,monospace" font-size="9" font-weight="600" fill="${valueColor}">${fmt.num(val)}</text>`;
    const periodText = (cx, label, color, weight) =>
        `<text x="${cx.toFixed(1)}" y="${H - 2}" text-anchor="middle" font-family="IBM Plex Mono,monospace" font-size="8" fill="${color}" font-weight="${weight}" letter-spacing="0.05em">${label}</text>`;

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

/* Detail charts are drawn in a coordinate system matching the drawer's
   rendered width, so SVG text shows at its nominal pixel size on phones
   instead of being scaled down with a fixed 540-wide viewBox. */
function chartWidth() {
    const body = document.getElementById("drawer-body");
    let w = 0;
    if (body) {
        const cs = getComputedStyle(body);
        // 24 = the chart box's horizontal padding
        w = body.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 24;
    }
    if (!(w > 0)) w = Math.min(window.innerWidth, 560) - 60;
    return Math.round(clamp(w, 260, 540));
}

const CHART_FONT = "IBM Plex Mono,monospace";

function makeBarChart(values, periods, options = {}) {
    const W = chartWidth(), H = 180;
    const compact = W < 420;
    const fs = 11;
    const pad = { top: 10, right: 10, bottom: 30, left: 48 };
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
        return `<text x="${pad.left - 6}" y="${(y + 4).toFixed(1)}" text-anchor="end" font-family="${CHART_FONT}" font-size="${fs}" fill="currentColor" fill-opacity="0.75">${fmt.esc(fmtY(v))}</text>`;
    }).join("");

    const n = values.length;
    const step = n > 0 ? plotW / n : plotW;
    const barW = Math.max(2, Math.min(16, step * 0.72));

    const accent = getCSSVar("--accent", "#1e3a5f");
    const partialFrom = n - Math.max(0, options.partialCount || 0);

    // The frame's two compared quarters carry the weight; with no frame
    // given, the newest complete quarter does.
    const { frameQuarter, currentYear } = options;
    const hasFrame = Number.isFinite(frameQuarter) && Number.isFinite(currentYear);
    const emphasisFor = i => {
        if (!hasFrame) return i === partialFrom - 1 ? 1 : 0.55;
        const yq = periodYearQuarter(periods[i]);
        if (!yq || yq.quarter !== frameQuarter) return 0.38;
        if (yq.year === currentYear) return 1;
        if (yq.year === currentYear - 1) return 0.72;
        return 0.38;
    };

    const bars = values.map((v, i) => {
        const x = pad.left + i * step + (step - barW) / 2;
        const y = plotTop + (1 - v / niceMax) * plotH;
        const h = Math.max(1, plotBottom - y);
        const label = periods[i]?.label || periods[i]?.short || "";
        const valueLabel = options.money ? fmt.money(v)
            : options.percent ? `${v.toFixed(2)}% of tagged activity`
            : `${fmt.int(v)} mentions`;
        const hover = `<title>${fmt.esc(label)} · ${valueLabel}${i >= partialFrom ? " (still reporting)" : ""}</title>`;
        const emphasis = emphasisFor(i);
        // Still-reporting quarters render hollow so a low bar doesn't read
        // as a real drop.
        if (i >= partialFrom) {
            return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${accent}" fill-opacity="0.12" stroke="${accent}" stroke-opacity="${emphasis >= 1 ? 0.9 : 0.5}" stroke-width="1" stroke-dasharray="2 1.5">${hover}</rect>`;
        }
        return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${accent}" fill-opacity="${emphasis}">${hover}</rect>`;
    }).join("");

    const xIdx = pickXIndices(n, compact ? 4 : 5);
    const xLabels = xIdx.map(idx => {
        const x = clamp(pad.left + idx * step + step / 2, pad.left + 14, W - 16);
        const label = periods[idx]?.short || periods[idx]?.label || "";
        return `<text x="${x.toFixed(1)}" y="${(plotBottom + 17).toFixed(1)}" text-anchor="middle" font-family="${CHART_FONT}" font-size="${fs}" fill="currentColor" fill-opacity="0.75">${fmt.esc(label)}</text>`;
    }).join("");

    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="color: var(--ink-3);" role="img" aria-label="Values by quarter">${grid}${yLabels}${bars}${xLabels}</svg>`;
}

/* Year/quarter of a period entry: timeseries quarters carry numeric fields,
   the org history carries "2024 Q1"-style labels. */
function periodYearQuarter(p) {
    if (!p) return null;
    if (Number.isFinite(p.year) && Number.isFinite(p.quarter)) return { year: p.year, quarter: p.quarter };
    const m = String(p.label || "").match(/(\d{4})\s*Q([1-4])/);
    return m ? { year: +m[1], quarter: +m[2] } : null;
}

/* Same-quarter-across-years chart: one bar per year for the frame's report
   quarter — the clean way to ask "is this Q1 bigger than past Q1s?" in a
   seasonal quarterly regime. Few bars, so each carries its own printed
   value; the two years the headline comparison uses get full weight and
   earlier context years sit lighter. */
function makeSeasonalChart(values, periods, options = {}) {
    const { money = false, percent = false, partialCount = 0, frameQuarter, currentYear, currentPartial = false } = options;
    const n = values.length;
    const pts = [];
    for (let i = 0; i < n; i++) {
        const yq = periodYearQuarter(periods[i]);
        if (!yq || yq.quarter !== frameQuarter) continue;
        pts.push({
            year: yq.year,
            value: toNum(values[i]),
            partial: i >= n - Math.max(0, partialCount) || (currentPartial && yq.year === currentYear)
        });
    }
    if (!pts.length) return "";

    const W = chartWidth(), H = 180;
    const compact = W < 420;
    const pad = { top: 22, right: 10, bottom: 30, left: 48 };
    const max = Math.max(...pts.map(p => p.value), 1);
    const niceMax = niceNum(max);
    const ticks = [0, niceMax * 0.5, niceMax];
    const plotBottom = H - pad.bottom;
    const plotTop = pad.top;
    const plotW = W - pad.left - pad.right;
    const plotH = plotBottom - plotTop;
    const fmtVal = money ? v => fmt.money(v)
        : percent ? v => `${v.toFixed(v >= 10 ? 1 : 2)}%`
        : v => fmt.num(v);

    const grid = ticks.map(v => {
        const y = plotTop + (1 - v / niceMax) * plotH;
        return `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="currentColor" stroke-opacity="0.08" stroke-width="1" />`;
    }).join("");

    const yLabels = ticks.map(v => {
        const y = plotTop + (1 - v / niceMax) * plotH;
        return `<text x="${pad.left - 6}" y="${(y + 4).toFixed(1)}" text-anchor="end" font-family="${CHART_FONT}" font-size="11" fill="currentColor" fill-opacity="0.75">${fmt.esc(fmtVal(v))}</text>`;
    }).join("");

    const step = plotW / pts.length;
    const barW = Math.max(10, Math.min(44, step * 0.55));
    const accent = getCSSVar("--accent", "#1e3a5f");

    const bars = pts.map((p, i) => {
        const cx = pad.left + i * step + step / 2;
        const x = cx - barW / 2;
        const y = plotTop + (1 - p.value / niceMax) * plotH;
        const h = Math.max(1, plotBottom - y);
        const prev = i > 0 ? pts[i - 1].value : 0;
        const yoy = i > 0 && prev > 0 ? ` · ${p.value >= prev ? "+" : ""}${((p.value - prev) / prev * 100).toFixed(0)}% vs ${pts[i - 1].year}` : "";
        const hover = `<title>Q${frameQuarter} ${p.year} · ${fmtVal(p.value)}${yoy}${p.partial ? " (still reporting)" : ""}</title>`;
        // The frame's two comparison years carry the visual weight; a
        // still-filling year renders hollow like the all-quarters view.
        const emphasis = p.year === currentYear ? 0.95 : p.year === currentYear - 1 ? 0.6 : 0.3;
        const valueLabel = `<text x="${cx.toFixed(1)}" y="${(y - 5).toFixed(1)}" text-anchor="middle" font-family="${CHART_FONT}" font-size="${compact ? 10 : 11}" font-weight="600" fill="currentColor" fill-opacity="${p.year >= currentYear - 1 ? 0.9 : 0.7}">${fmt.esc(fmtVal(p.value))}</text>`;
        const yearLabel = `<text x="${cx.toFixed(1)}" y="${(plotBottom + 17).toFixed(1)}" text-anchor="middle" font-family="${CHART_FONT}" font-size="11" fill="currentColor" fill-opacity="${p.year >= currentYear - 1 ? 0.9 : 0.75}">${p.year}</text>`;
        const rect = p.partial
            ? `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${accent}" fill-opacity="0.12" stroke="${accent}" stroke-opacity="0.5" stroke-width="1" stroke-dasharray="2 1.5">${hover}</rect>`
            : `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${accent}" fill-opacity="${emphasis}">${hover}</rect>`;
        return rect + valueLabel + yearLabel;
    }).join("");

    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="color: var(--ink-3);" role="img" aria-label="Q${frameQuarter} totals by year">${grid}${yLabels}${bars}</svg>`;
}

/* ─── Drawer history section ───
   One section, two lenses: the full quarter-by-quarter run, and the frame's
   quarter isolated across every year on record. The chosen lens sticks for
   the session so flipping between drawers keeps the reader's view. */
let historyChartMode = "all";

// A report quarter whose calendar period hasn't ended yet (e.g. early
// termination reports filed for next quarter) is not plotted at all.
function periodEnded(p) {
    const yq = periodYearQuarter(p);
    if (!yq) return true;
    return new Date(yq.year, yq.quarter * 3, 1) <= dataAsOfDate();
}

// Trailing quarters still being reported: from stats.current_partial_quarter
// on, plus (for mention series) the low-volume heuristic.
function stillReportingCount(quarters, useVolume) {
    const cpq = state.stats?.current_partial_quarter;
    let n = 0;
    if (cpq) {
        const cut = cpq.year * 4 + cpq.quarter;
        for (let i = quarters.length - 1; i >= 0; i--) {
            const yq = periodYearQuarter(quarters[i]);
            if (yq && yq.year * 4 + yq.quarter >= cut) n++;
            else break;
        }
    }
    return Math.min(Math.max(n, useVolume ? partialTrailingCount(quarters) : 0), Math.max(0, quarters.length - 1));
}

function appendHistorySection(body, opts) {
    const { title, money = false, frameQuarter, currentYear,
            currentPartial = false, extraNote, reportingNote } = opts;
    if (!opts.series?.length || !opts.quarters) return;
    const keep = opts.quarters.map(periodEnded);
    const quarters = opts.quarters.filter((_, i) => keep[i]);
    const series = opts.series.filter((_, i) => keep[i]);
    if (!series.length || quarters.length < 2) return;

    // Normalization: constant dollars for money series, share-of-quarter
    // for mention series (skipped when the export lacks denominators).
    let displaySeries = series;
    let percent = false;
    if (money && normalizedOn()) {
        displaySeries = series.map((v, i) => toNum(v) * deflatorFor(quarters[i]?.label));
    } else if (!money && normalizedOn() && quarters.some(q => toNum(q.mentions) > 0)) {
        displaySeries = series.map((v, i) => {
            const d = toNum(quarters[i]?.mentions);
            return d > 0 ? (toNum(v) / d) * 100 : 0;
        });
        percent = true;
    }

    const sliceYears = quarters
        .map(periodYearQuarter)
        .filter(yq => yq && yq.quarter === frameQuarter)
        .map(yq => yq.year);
    const canSeasonal = Number.isFinite(frameQuarter) && sliceYears.length >= 2;
    const hasCurrentYear = currentYear != null && sliceYears.includes(currentYear);
    const hasBaselineYear = currentYear != null && sliceYears.includes(currentYear - 1);

    const sec = el("div", "detail-section");
    const titleRow = el("div", "detail-section-title");
    titleRow.appendChild(el("span", "", title));
    sec.appendChild(titleRow);
    const box = el("div", "detail-chart-box");
    sec.appendChild(box);
    const noteEl = el("p", "detail-chart-note");
    sec.appendChild(noteEl);
    body.appendChild(sec);

    let seg = null;
    if (canSeasonal) {
        seg = el("div", "chart-seg");
        seg.setAttribute("role", "group");
        seg.setAttribute("aria-label", "History chart lens");
        titleRow.appendChild(seg);
    }

    const partialCount = stillReportingCount(quarters, !money);

    const render = () => {
        const mode = canSeasonal ? historyChartMode : "all";
        if (seg) {
            seg.innerHTML = "";
            for (const [key, label] of [["all", "All quarters"], ["yoy", `Q${frameQuarter} by year`]]) {
                const b = el("button", "chart-seg-btn" + (mode === key ? " active" : ""), label);
                b.type = "button";
                b.setAttribute("aria-pressed", mode === key ? "true" : "false");
                b.onclick = () => { if (historyChartMode !== key) { historyChartMode = key; render(); } };
                seg.appendChild(b);
            }
        }
        const notes = [];
        if (mode === "yoy") {
            box.innerHTML = makeSeasonalChart(displaySeries, quarters, {
                money, percent, partialCount, frameQuarter, currentYear, currentPartial
            });
            if (hasCurrentYear && hasBaselineYear) {
                notes.push(`Q${frameQuarter} totals for every year on record. Highlighted bars: the two years compared above.`);
            } else {
                notes.push(`Q${frameQuarter} totals for every year on record.`);
            }
            if (hasCurrentYear && currentPartial) {
                notes.push(`Q${frameQuarter} ${currentYear} is still being reported (dashed outline); its total will rise as late filings arrive.`);
            }
        } else {
            box.innerHTML = makeBarChart(displaySeries, quarters, { partialCount, money, percent, frameQuarter, currentYear });
            if (hasCurrentYear) {
                notes.push(hasBaselineYear
                    ? `Highlighted bars: Q${frameQuarter} ${currentYear} and Q${frameQuarter} ${currentYear - 1}, the quarters compared above.`
                    : `Highlighted bar: Q${frameQuarter} ${currentYear}, the quarter shown above.`);
            }
            if (partialCount > 0) {
                const still = quarters.slice(quarters.length - partialCount)
                    .map(periodYearQuarter).filter(Boolean).map(yq => `Q${yq.quarter} ${yq.year}`);
                if (still.length) notes.push(`${still.join(" and ")} ${still.length === 1 ? "is" : "are"} still being reported (dashed outline); totals will rise as late filings arrive.`);
            }
            if (reportingNote) notes.push(reportingNote);
        }
        if (money && normalizedOn()) notes.push(`Constant ${normBaseYear()} dollars (CPI-U).`);
        if (percent) notes.push(NORMALIZED_MENTIONS_LABEL);
        if (extraNote) notes.push(extraNote);
        noteEl.textContent = notes.join(" ");
        noteEl.style.display = notes.length ? "" : "none";
    };
    render();
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

/* Each drawer step is its own history entry, so the browser Back button
   walks back through the panel and then closes it instead of leaving the
   site. history.state carries the panel and its breadcrumb stack; `depth`
   counts the entries pushed above the page entry. */
function pushDrawer(view) {
    if (state.drawer) {
        state.drawerStack.push(state.drawer);
    }
    state.drawer = view;
    state.histDepth = (state.histDepth || 0) + 1;
    syncURL(true);
    renderDrawer();
    showDrawer();
}

function closeDrawer() {
    if (state.closingDrawer) return;   // a Back is already in flight
    if (state.histDepth > 0) {
        // Return to the page entry; the popstate handler hides the panel.
        state.closingDrawer = true;
        history.go(-state.histDepth);
        return;
    }
    // Panel opened from a shared link on first load: no entry to go back to.
    state.drawer = null;
    state.drawerStack = [];
    syncURL();
    hideDrawer();
}

// Jump to breadcrumb step i (0 = first panel opened).
function drawerJumpTo(i) {
    const steps = state.drawerStack.length - i;
    if (steps > 0 && steps <= state.histDepth) {
        history.go(-steps);
        return;
    }
    const items = [...state.drawerStack, state.drawer];
    state.drawer = items[i];
    state.drawerStack = state.drawerStack.slice(0, i);
    syncURL();
    renderDrawer();
}

let drawerReturnFocus = null;

// Everything behind an open dialog is inert: no focus, no clicks, hidden
// from assistive technology.
function updatePageInert() {
    const aboutOpen = document.getElementById("about")?.getAttribute("aria-hidden") === "false";
    const drawerOpen = document.getElementById("drawer")?.getAttribute("aria-hidden") === "false";
    for (const node of document.querySelectorAll(".topbar, main.page")) node.inert = aboutOpen || drawerOpen;
    const drawer = document.getElementById("drawer");
    if (drawer) drawer.inert = aboutOpen || !drawerOpen;
}

function showDrawer() {
    const drawer = document.getElementById("drawer");
    if (drawer.getAttribute("aria-hidden") !== "false") {
        const active = document.activeElement;
        drawerReturnFocus = active && active !== document.body ? active : null;
    }
    drawer.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    updatePageInert();
    document.getElementById("drawer-card")?.focus({ preventScroll: true });
}

function hideDrawer() {
    const drawer = document.getElementById("drawer");
    const wasOpen = drawer.getAttribute("aria-hidden") === "false";
    drawer.setAttribute("aria-hidden", "true");
    if (!state.palette.open) document.body.style.overflow = "";
    updatePageInert();
    if (wasOpen && drawerReturnFocus?.isConnected) drawerReturnFocus.focus({ preventScroll: true });
    else if (drawer.contains(document.activeElement)) document.activeElement.blur();
    drawerReturnFocus = null;
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
    return uuid ? `https://lda.gov/filings/public/filing/${uuid}/print/` : null;
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
            btn.type = "button";
            btn.onclick = () => drawerJumpTo(i);
        }
        trail.appendChild(btn);
    });

    const card = document.getElementById("drawer-card");
    if (card) card.setAttribute("aria-label", drawerLabel(state.drawer));
    body.scrollTop = 0;

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

/* Organization detail (clients.json — dollar spend under the active frame) */

function renderOrgDetail(body, view) {
    // Look in the active frame first; an org may only clear the floors in
    // one frame (e.g. a palette hit), so fall back to the other.
    let frameKey = activeFrameKey();
    let m = allOrgRecords(frameKey).find(x => x.key === view.key);
    if (!m) {
        const other = frameKey === "quarter" ? "qtd" : "quarter";
        const found = allOrgRecords(other).find(x => x.key === view.key);
        if (found) { m = found; frameKey = other; }
    }
    if (!m && view.key) {
        // Canonicalization improves over time and only ever shortens keys,
        // so an old shared link's key starts with the org's current key
        // ("META PLATFORMS INC AND VARIOUS..." -> "META PLATFORMS").
        // Salvage it with the longest matching prefix key.
        const stale = String(view.key).toUpperCase();
        for (const fk of FRAME_KEYS) {
            for (const r of allOrgRecords(fk)) {
                if (stale.startsWith(r.key + " ") && (!m || r.key.length > m.key.length)) {
                    m = r; frameKey = fk;
                }
            }
        }
    }
    const { cq, bq } = orgQuarterLabels(frameKey);
    const subLabel = frameKey === "quarter"
        ? `${quarterWithMonths(cq)}, latest complete quarter · compared with ${bq}`
        : `${quarterWithMonths(cq)}, reports still arriving · compared with ${bq}`;

    const eyebrow = el("div", "detail-eyebrow");
    eyebrow.appendChild(el("span", "cat-tag clients", "Org"));
    body.appendChild(eyebrow);

    body.appendChild(el("h2", "detail-name", (m && m.name) || view.name || "Organization"));

    if (!m) {
        body.appendChild(el("div", "detail-sub", subLabel));
        body.appendChild(el("p", "detail-empty",
            "This organization doesn't clear the reporting floor for the movers list. It may still appear in recent filings or tag signals."));
        return;
    }

    body.appendChild(el("div", "detail-sub", subLabel));
    if (m.method_changed) appendMethodWarning(body);

    const head = buildOrgHeadline(m, frameKey);
    const summary = el("div", "detail-summary");
    summary.innerHTML = head.html;
    body.appendChild(summary);

    const stats = el("div", "detail-stats");
    const vals = orgFrameValues(m, frameKey);
    const ratio = normalizedOn()
        ? (vals.baseline > 0 ? vals.current / vals.baseline : null)
        : m.ratio;
    const statCells = [
        { value: fmt.money(vals.current), label: cq },
        { value: fmt.money(vals.baseline), label: bq },
        { value: ratio != null ? `${ratio.toFixed(2)}×` : "—", label: "Ratio" },
        { value: fmt.int(m.filings_current), label: `Filings, ${cq}` },
    ];
    for (const s of statCells) {
        const stat = el("div", "detail-stat");
        stat.appendChild(el("span", "detail-stat-value", s.value));
        stat.appendChild(el("span", "detail-stat-label", s.label));
        stats.appendChild(stat);
    }
    body.appendChild(stats);

    // Quarterly spend chart, aligned to the shared quarters array, with the
    // same all-quarters ⇄ same-quarter-by-year lens as the tag drawers.
    // Org history only covers complete quarters, so no partial handling.
    const quarters = (state.clients?.quarters || []).map(label => {
        const parts = label.match(/^(\d{4})\s+Q(\d)$/);
        return {
            label,
            short: parts ? `${parts[1].slice(2)}Q${parts[2]}` : label,
            year: parts ? +parts[1] : null,
            quarter: parts ? +parts[2] : null
        };
    });
    const orgFrame = state.clients?.frames?.[frameKey];
    appendHistorySection(body, {
        title: "Reported spend by quarter",
        series: m.series,
        quarters,
        money: true,
        frameQuarter: orgFrame?.current_quarter?.quarter,
        currentYear: orgFrame?.current_quarter?.year,
        extraNote: "Total reported income and expenses per quarter, across all filings for this organization."
    });

    // Top topics — pivot into the matching topic signal if it's tracked there.
    if (m.topics?.length) {
        const sec = el("div", "detail-section");
        sec.appendChild(el("div", "detail-section-title", "Top topics"));
        const tags = el("div", "detail-tags");
        for (const topic of m.topics) tags.appendChild(signalTag("topics", topic));
        sec.appendChild(tags);
        body.appendChild(sec);
    }

    appendOrgComparison(body, m, frameKey);
}

const EXPENSE_METHODS = {
    A: "LDA definitions",
    B: "IRC §6033(b)(8) · nonprofits",
    C: "IRC §162(e) · can include state and grassroots lobbying"
};
const METHOD_WARNING = "Reporting method changed between these quarters.";
let clientDetailsRequest = null;

function appendMethodWarning(parent) {
    parent.appendChild(el("p", "org-method-warning", METHOD_WARNING));
}

function orgComparisonSection(parent, title, labels, renderLeg) {
    const section = el("div", "detail-section");
    section.appendChild(el("div", "detail-section-title", title));
    const grid = el("div", "org-comparison");
    for (const [leg, label] of [["current", labels.cq], ["baseline", labels.bq]]) {
        const column = el("div", "org-quarter");
        column.appendChild(el("h4", "org-quarter-label", label));
        renderLeg(column, leg);
        grid.appendChild(column);
    }
    section.appendChild(grid);
    parent.appendChild(section);
}

function appendMoreNote(parent, count) {
    if (count) parent.appendChild(el("p", "detail-chart-note", `${fmt.int(count)} more omitted; see official filings.`));
}

function appendDisclosureText(parent, text, truncated, previewLength = 220) {
    if (text.length <= previewLength && !truncated) {
        parent.appendChild(el("p", "org-disclosure-text", text));
        return;
    }
    const disclosure = el("details", "org-text-expand");
    disclosure.appendChild(el("summary", "", `${text.slice(0, previewLength)}… · Expand`));
    disclosure.appendChild(el("p", "org-disclosure-text", text));
    if (truncated) disclosure.appendChild(el("p", "detail-chart-note", "Excerpt. Full text in the official filing."));
    parent.appendChild(disclosure);
}

/* LDA filing-type codes: "Q3" quarterly report, "3A" amendment, "3T"
   termination, "3@" termination amendment; a trailing "Y" marks a
   no-activity report. Unknown codes are shown as filed. */
function filingTypeText(type) {
    const t = String(type || "").trim().toUpperCase();
    if (!t) return "";
    const m = t.match(/^(?:Q([1-4])|([1-4])([AT@]))(Y?)$/);
    if (!m) return t;
    const q = m[1] || m[2];
    const kind = m[1] ? "report" : { A: "amendment", T: "termination", "@": "termination amendment" }[m[3]];
    return `Q${q} ${kind}${m[4] ? " (no activity)" : ""}`;
}

/* Where an organization filed its own report for a quarter, its outside
   firms' amounts are already inside that report. Those amounts are shown
   muted with this label and are never added to a total. Older exports
   lack the flag, so absent means "not included". */
const INCLUDED_IN_SELF = "included in the organization's own report";

function amountLine(cls, amount, included, extra = "") {
    const text = `${amount == null ? "Amount not yet fetched" : fmt.money(amount)}${extra}${included ? ` · ${INCLUDED_IN_SELF}` : ""}`;
    return el("div", `${cls}${included ? " org-included" : ""}`, text);
}

function appendOrgFilings(parent, filings, more = 0, legacy = false) {
    if (!filings.length) parent.appendChild(el("p", "detail-chart-note", "No filings in this comparison period."));
    for (const filing of filings) {
        const row = el("div", "org-filing");
        row.appendChild(el("div", "filing-row-client", filing.registrant || "Unknown registrant"));
        const typeText = filingTypeText(filing.type || filing.filing_type);
        row.appendChild(el("div", "filing-row-registrant", `${fmt.dateShort(filing.date)}${typeText ? ` · ${typeText}` : ""}`));
        const amount = legacy ? filing.income : filing.amount;
        row.appendChild(amountLine("org-filing-amount", amount, filing.included_in_self_report === true));
        const link = officialLink(filing.uuid, "official-link small");
        if (link) row.appendChild(link);
        parent.appendChild(row);
    }
    appendMoreNote(parent, more);
}

function coveredPositionClassLabel(value) {
    const labels = {
        "member of congress": "Member of Congress",
        "congressional staff": "Congressional staff",
        "executive branch": "Executive branch",
        "military": "Military",
        "other": "Other",
    };
    return String(value || "").split(",").map(kind => labels[kind.trim().toLowerCase()] || kind.trim()).join(", ");
}

function renderOrgComparison(parent, detail, frameKey) {
    const labels = orgQuarterLabels(frameKey);
    orgComparisonSection(parent, "In-house & outside firms", labels, (column, leg) => {
        const q = detail[leg];
        if (!q.filing_count) {
            column.appendChild(el("p", "detail-chart-note", "No filings in this comparison period."));
            return;
        }
        const outsideIncluded = q.split.outside_included_in_self === true;
        if (q.split.total != null) {
            column.appendChild(el("div", "org-split-line org-split-total", `Total: ${fmt.money(q.split.total)}`));
        }
        for (const [key, label] of [["self", "Self-filed expenses"], ["outside", "Outside-firm income"]]) {
            const missing = q.split[`${key}_amounts_missing`];
            const value = q.split[key] == null ? "Not yet fetched" : fmt.money(q.split[key]);
            const included = key === "outside" && outsideIncluded && q.split[key] != null;
            column.appendChild(el("div", `org-split-line${included ? " org-included" : ""}`,
                `${label}: ${value}${missing ? " (amounts missing)" : ""}${included ? ` · ${INCLUDED_IN_SELF}` : ""}`));
        }
        if (q.split.unknown_filings) {
            column.appendChild(el("p", "detail-chart-note", `In-house vs. firm is not available for ${fmt.int(q.split.unknown_filings)} filings (${q.split.unknown == null ? "amount not available" : fmt.money(q.split.unknown)}).`));
        }
        if (q.self_inferred) column.appendChild(el("p", "detail-chart-note", "In-house vs. firm is inferred from names for older filings."));
        column.appendChild(el("p", "org-split-line", `${fmt.int(q.firm_count)} outside firm${q.firm_count === 1 ? "" : "s"}`));
        for (const firm of q.firms) {
            const row = el("div", "org-firm");
            row.appendChild(el("span", "", firm.name));
            if (firm.status) row.appendChild(el("span", `org-firm-status ${firm.status}`, firm.status));
            const amount = amountLine("org-firm-amount", firm.income, firm.included_in_self_report === true, firm.amounts_missing ? " · amounts missing" : "");
            row.appendChild(amount);
            column.appendChild(row);
        }
        appendMoreNote(column, q.firms_more);
    });
    const anyIncluded = ["current", "baseline"].some(leg => detail[leg]?.split?.outside_included_in_self === true);
    parent.appendChild(el("p", "detail-chart-note", anyIncluded
        ? "Amounts as filed. When an organization files its own report, that report already includes what it paid the outside firms listed, so their amounts are not added again; the total counts each dollar once."
        : "Amounts as filed. Self-filed expenses can include fees paid to the firms listed."));
    orgComparisonSection(parent, "Expense-reporting method", labels, (column, leg) => {
        const q = detail[leg];
        if (!q.split.self_filings) {
            column.appendChild(el("p", "detail-chart-note", "No self-filed reports in this period."));
            return;
        }
        for (const method of q.methods) column.appendChild(el("p", "org-split-line", `${method} · ${EXPENSE_METHODS[method]}`));
        if (q.methods_missing) column.appendChild(el("p", "detail-chart-note", `Method not available for ${fmt.int(q.methods_missing)} self-filed report${q.methods_missing === 1 ? "" : "s"}.`));
    });
    orgComparisonSection(parent, "Lobbyists & covered positions", labels, (column, leg) => {
        const q = detail[leg];
        if (!q.filing_count) {
            column.appendChild(el("p", "detail-chart-note", "No filings in this comparison period."));
            return;
        }
        if (q.lobbyist_count == null) {
            column.appendChild(el("p", "detail-chart-note", "Not yet available."));
            return;
        }
        column.appendChild(el("p", "org-split-line", `${fmt.int(q.lobbyist_count)} lobbyist${q.lobbyist_count === 1 ? "" : "s"} · ${fmt.int(q.covered_count)} with a covered position`));
        column.appendChild(el("p", "detail-chart-note", `Details for ${fmt.int(q.details_fetched)} of ${fmt.int(q.filing_count)} filing${q.filing_count === 1 ? "" : "s"}.`));
        const classes = el("div", "org-position-classes");
        for (const [kind, count] of Object.entries(q.position_classes || {})) {
            if (count > 0) classes.appendChild(el("div", "", `${coveredPositionClassLabel(kind)}: ${fmt.int(count)}`));
        }
        if (classes.childElementCount) column.appendChild(classes);
        for (const position of q.positions) {
            const row = el("div", "org-position");
            row.appendChild(el("strong", "", position.name));
            row.appendChild(el("div", "filing-row-registrant", coveredPositionClassLabel(position.class)));
            appendDisclosureText(row, position.text, position.truncated);
            column.appendChild(row);
        }
        appendMoreNote(column, q.positions_more);
    });
    orgComparisonSection(parent, "Specific issues", labels, (column, leg) => {
        const q = detail[leg];
        if (!q.issues.length) column.appendChild(el("p", "detail-chart-note", !q.filing_count ? "No filings in this comparison period." : q.details_fetched < q.filing_count ? "Not yet available." : "No activity descriptions reported."));
        for (const issue of q.issues) {
            const row = el("div", "org-issue");
            row.appendChild(el("span", "detail-tag", issue.code));
            appendDisclosureText(row, issue.text, issue.truncated);
            column.appendChild(row);
        }
        appendMoreNote(column, q.issues_more);
    });
    orgComparisonSection(parent, "Filings · official records", labels, (column, leg) => {
        appendOrgFilings(column, detail[leg].filings, detail[leg].filings_more);
    });
}

function appendOrgComparison(body, mover, frameKey) {
    const host = el("div", "org-detail-comparison");
    host.appendChild(el("p", "detail-chart-note", "Loading filing details…"));
    body.appendChild(host);
    // One request shared across drawer opens, made only on the first org open.
    if (!clientDetailsRequest) clientDetailsRequest = loadJSON("client_details.json");
    clientDetailsRequest.then(data => {
        if (!host.isConnected || state.drawer?.kind !== "org") return;
        host.replaceChildren();
        const sameExport = data?.generated_at === state.clients?.generated_at;
        const detail = sameExport ? data?.frames?.[frameKey]?.[mover.key] : null;
        if (detail) {
            renderOrgComparison(host, detail, frameKey);
        } else {
            host.appendChild(el("p", "detail-empty", "Not yet available."));
            if (mover.examples?.length) {
                host.appendChild(el("div", "detail-section-title", "Current-quarter example filings"));
                appendOrgFilings(host, mover.examples, 0, true);
            }
        }
    });
}

/* A tag links to its signal panel only when the active frame has activity
   for it; otherwise the panel would be empty, so it renders as plain text. */
function signalHasActivity(mode, name, frameKey = activeFrameKey()) {
    const item = (state.trends?.[mode]?.[frameKey] || []).find(i => i.name === name);
    return !!item && (toNum(item.count) > 0 || toNum(item.baseline_count) > 0);
}

function signalTag(mode, name) {
    const label = displayName(mode, name);
    if (!signalHasActivity(mode, name)) return el("span", "detail-tag detail-tag-static", label);
    const tag = el("button", "detail-tag", label);
    tag.type = "button";
    tag.onclick = () => openSignal(mode, name);
    return tag;
}

/* Signal detail */

function renderSignalDetail(body, view) {
    const frameKey = activeFrameKey();
    const items = getCategoryItemsFixed(view.mode, frameKey);
    const fallback = {
        mode: view.mode,
        name: view.name,
        count: 0, baseline_count: 0,
        client_count: 0, baseline_client_count: 0,
        current_share_pct: 0, baseline_share_pct: 0,
        share_delta_pp: 0, ratio: null,
        score: 0, confidence: "low",
        topClients: [], examples: [], income: 0
    };
    const m = items.find(i => i.name === view.name) || fallback;
    const meta = CATEGORIES[m.mode];

    // Eyebrow
    const eyebrow = el("div", "detail-eyebrow");
    eyebrow.appendChild(el("span", `cat-tag ${meta.tagClass}`, meta.label));
    const confChip = el("span", `detail-conf ${m.confidence}`, `${m.confidence} confidence`);
    confChip.title = "Rule-based signal strength: how much volume sits behind this and how far its share moved against the same quarter last year.";
    eyebrow.appendChild(confChip);
    body.appendChild(eyebrow);

    // Name + sub
    body.appendChild(el("h2", "detail-name", displayName(m.mode, m.name)));
    body.appendChild(el("div", "detail-sub", frameSubtitle(frameKey)));

    if (m.mode === "legislation") {
        const link = congressGovLink(m.name, "official-link block");
        if (link) body.appendChild(link);
    }

    // Plain-English summary
    const head = buildHeadline(m, frameKey);
    const summary = el("div", "detail-summary");
    summary.innerHTML = head.html;
    body.appendChild(summary);

    // Stats grid
    const stats = el("div", "detail-stats");
    const comparison = signalComparison(m, frameKey);
    const delta = comparison.delta;
    const deltaDir = delta > 0.01 ? "up" : delta < -0.01 ? "down" : "";
    const statCells = [
        { value: m.client_count ? fmt.int(m.client_count) : "—", label: "Orgs active",
          tip: "Distinct organizations (name variants folded) whose filings mention this in the selected frame." },
        { value: m.baseline_client_count ? fmt.int(m.baseline_client_count) : "—", label: `Orgs, ${comparison.baselineTiming}`,
          tip: "Distinct organizations in the baseline quarter." },
        { value: fmt.int(m.count),                label: "Mentions",
          tip: "Lobbying activity descriptions that reference this in the selected frame. One filing can contribute several mentions." },
        { value: fmt.int(m.baseline_count),        label: `Mentions, ${comparison.baselineTiming}`,
          tip: `Mentions ${comparison.baselineTiming}.` },
        { value: fmt.pct(m.current_share_pct),    label: "Share",
          tip: "Share of all tagged mentions in the selected frame." },
        { value: comparison.isNew ? "new" : fmt.pp(delta), label: "Δ Share", cls: comparison.isNew ? "" : deltaDir,
          tip: comparison.isNew ? `No mentions ${comparison.baselineTiming}.` : `Change in share versus ${comparison.baselineTiming}, in percentage points.` },
        { value: m.income > 0 ? fmt.money(m.income * deflatorFor(frameInfo(frameKey)?.label)) : "—", label: "Assoc. filing income",
          tip: "Combined reported income of filings whose activities mention this. Filings usually cover several issues, so this is NOT spend attributable to this item alone." }
    ];
    for (const s of statCells) {
        const stat = el("div", "detail-stat");
        if (s.tip) stat.title = s.tip;
        stat.appendChild(el("span", `detail-stat-value ${s.cls || ""}`.trim(), s.value));
        stat.appendChild(el("span", "detail-stat-label", s.label));
        stats.appendChild(stat);
    }
    body.appendChild(stats);

    // Quarterly history chart (every category), with an all-quarters ⇄
    // same-quarter-by-year lens toggle. The seasonal slice follows the
    // active frame's quarter: latest complete quarter or the one filling.
    {
        const seriesKeyByMode = {
            topics: "topic_series",
            entities: "entity_series",
            legislation: "legislation_series"
        };
        const fq = frameKey === "qtd"
            ? state.stats?.current_partial_quarter
            : state.stats?.latest_complete_quarter;
        appendHistorySection(body, {
            title: "Mentions by quarter",
            series: state.timeseries?.[seriesKeyByMode[m.mode]]?.[m.name],
            quarters: state.timeseries?.quarters || [],
            frameQuarter: fq?.quarter,
            currentYear: fq?.year,
            currentPartial: frameInfo(frameKey)?.complete === false,
            reportingNote: state.timeseries?.context?.reporting_note
        });
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
            const typeText = filingTypeText(ex.type || ex.filing_type);
            if (typeText) right.appendChild(el("div", "filing-row-date", typeText));
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
        const clientsByName = state.trends?.[keys.c]?.[state.view.frame] || {};
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
    const clientFrameLabel = frameInfo(state.view.frame)?.label || "current frame";
    body.appendChild(el("div", "detail-sub", `${clientFrameLabel} · activity across ${appearances.length} tracked signals`));

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
        { value: filings.length ? fmt.int(filings.length) : "—", label: "Recent filings" },
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
            stat.appendChild(el("span", "detail-stat-label",
                ctx.included_in_self_report === true ? `Income · ${INCLUDED_IN_SELF}` : "Income"));
            stats.appendChild(stat);
            body.appendChild(stats);
        }
        const typeText = filingTypeText(ctx.type || ctx.filing_type);
        if (typeText) body.appendChild(el("div", "detail-sub", typeText));
        const link = officialLink(ctx.uuid, "official-link block");
        if (link) {
            const sec = el("div", "detail-section");
            sec.appendChild(link);
            body.appendChild(sec);
            body.appendChild(el("p", "detail-empty", "Tag detail is kept only for the most recent filings. The official record linked above has the complete disclosure."));
        } else {
            const p = el("p", "detail-empty", "Tag detail is kept only for the most recent filings, and this filing is not among them. Search for it in the ");
            const a = el("a", "", "LDA.gov filing database");
            a.href = "https://lda.gov/filings/public/filing/search/";
            a.target = "_blank";
            a.rel = "noopener";
            p.appendChild(a);
            p.appendChild(document.createTextNode("."));
            body.appendChild(p);
        }
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
        { mode: "legislation", label: "Legislation", values: f.legislation || [] }
    ];
    for (const g of tagGroups) {
        if (!g.values.length) continue;
        const sec = el("div", "detail-section");
        const title = el("div", "detail-section-title");
        title.appendChild(el("span", "", g.label));
        title.appendChild(el("span", "count", `${g.values.length}`));
        sec.appendChild(title);
        const tags = el("div", "detail-tags");
        for (const v of g.values) tags.appendChild(signalTag(g.mode, v));
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
    aboutReturnFocus = document.activeElement;
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    updatePageInert();
    modal.querySelector(".about-card")?.focus({ preventScroll: true });
}

let aboutReturnFocus = null;

function closeAbout() {
    const modal = document.getElementById("about");
    if (!modal) return;
    modal.setAttribute("aria-hidden", "true");
    if (!state.drawer) document.body.style.overflow = "";
    updatePageInert();
    if (aboutReturnFocus?.isConnected) aboutReturnFocus.focus({ preventScroll: true });
    aboutReturnFocus = null;
}

/* ─── Command palette ─── */

let paletteIndex = null;

function buildPaletteIndex() {
    const idx = [];
    const seen = new Set();
    const add = (kind, mode, name, meta, extra) => {
        const key = `${kind}:${mode || ""}:${extra?.orgKey || name}`.toUpperCase();
        if (seen.has(key)) return;
        seen.add(key);
        // Index the display name too, so "H.R. 1" finds the act it names.
        const shown = displayName(mode, name);
        const search = (shown === name ? name : `${shown} ${name}`).toLowerCase();
        idx.push({ kind, mode, name, meta, search, ...(extra || {}) });
    };
    // Organization records and raw client names often describe the same
    // organization; a client entry is dropped when an org already covers it.
    const orgNames = new Set();
    const orgMatchKey = n => shortOrgName(n).toUpperCase().replace(/[^A-Z0-9]+/g, " ").replace(/^THE /, "").trim();

    // Tag signals from both frames, deduped by name (complete-quarter first).
    for (const mode of SIGNAL_MODES) {
        for (const frameKey of FRAME_KEYS) {
            const items = state.trends?.[mode]?.[frameKey] || [];
            for (const it of items) {
                add("signal", mode, it.name, `${CATEGORIES[mode].label} · ${fmt.int(it.count)} mentions`);
            }
        }
    }

    // Organization movers and majors from both frames, deduped by
    // canonical key — selecting one opens the org drawer.
    for (const frameKey of FRAME_KEYS) {
        for (const m of allOrgRecords(frameKey)) {
            add("org", null, m.name, `Org · ${fmt.money(orgFrameValues(m, frameKey).current)} in ${clientFrame(frameKey)?.current_quarter?.label || "latest quarter"}`, { orgKey: m.key });
            orgNames.add(orgMatchKey(m.name));
            if (m.key) orgNames.add(orgMatchKey(m.key));
        }
    }

    // Clients from filings + top-clients lists
    const clientCounts = new Map();
    for (const f of state.filings || []) {
        if (f.client) clientCounts.set(f.client, (clientCounts.get(f.client) || 0) + 1);
    }
    for (const mode of SIGNAL_MODES) {
        const keys = getKeyMap(mode);
        for (const frameKey of FRAME_KEYS) {
            const map = state.trends?.[keys.c]?.[frameKey] || {};
            for (const list of Object.values(map)) {
                for (const c of list) clientCounts.set(c, clientCounts.get(c) || 0);
            }
        }
    }
    for (const [c, n] of clientCounts.entries()) {
        if (orgNames.has(orgMatchKey(c))) continue;
        add("client", null, c, n > 0 ? `Client · ${n} recent filing${n === 1 ? "" : "s"}` : "Client");
    }

    return idx;
}

function searchPalette(query) {
    if (!paletteIndex) paletteIndex = buildPaletteIndex();
    const q = query.trim().toLowerCase();
    if (!q) {
        // Default suggestions: top movers
        const top = buildMovers("all", state.view.frame).slice(0, 8);
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
        // Whole-token prefix ("h.r. 1" in "h.r. 1 / one big…", not "h.r. 1689")
        else if (lower.startsWith(q) && !/[a-z0-9]/.test(lower.charAt(q.length))) score = 90;
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
    const groups = { signal: { topics: [], entities: [], legislation: [] }, org: [], client: [] };
    for (const r of results) {
        if (r.kind === "signal") groups.signal[r.mode].push(r);
        else if (r.kind === "org") groups.org.push(r);
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

    renderGroup("Organizations", groups.org);
    renderGroup("Topics", groups.signal.topics);
    renderGroup("Agencies", groups.signal.entities);
    renderGroup("Bills", groups.signal.legislation);
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
    const groups = { signal: { topics: [], entities: [], legislation: [] }, org: [], client: [] };
    for (const r of flatList) {
        if (r.kind === "signal") groups.signal[r.mode].push(r);
        else if (r.kind === "org") groups.org.push(r);
        else if (r.kind === "client") groups.client.push(r);
    }
    const ordered = [
        ...groups.org,
        ...groups.signal.topics,
        ...groups.signal.entities,
        ...groups.signal.legislation,
        ...groups.client
    ];
    const choice = ordered[state.palette.focusIdx];
    if (!choice) return;
    closePalette();
    if (choice.kind === "signal") openSignal(choice.mode, choice.name);
    else if (choice.kind === "org") openOrg(choice.orgKey, choice.name);
    else if (choice.kind === "client") openClient(choice.name);
}

/* ─── URL state ─── */

function currentURL() {
    // Query string carries the view (frame/category) so any state of the
    // dashboard is a shareable link; the hash carries the open drawer.
    const params = new URLSearchParams();
    if (state.view.frame !== "quarter") params.set("f", state.view.frame);
    if (state.view.cat !== "all") params.set("cat", state.view.cat);
    if (state.view.normalized) params.set("n", "1");
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
    return `${window.location.pathname}${query}${hash}`;
}

// push = a new history entry (opening a panel step); otherwise the current
// entry is updated in place (view toggles, closing a shared-link panel).
function syncURL(push = false) {
    const entry = {
        ls: 1,
        drawer: state.drawer,
        stack: state.drawerStack,
        depth: state.histDepth || 0
    };
    if (push) history.pushState(entry, "", currentURL());
    else history.replaceState(entry, "", currentURL());
}

function readViewFromURL() {
    const q = new URLSearchParams(window.location.search);
    if (FRAME_KEYS.includes(q.get("f"))) state.view.frame = q.get("f");
    if (q.get("cat") && (q.get("cat") === "recent" || q.get("cat") === "clients" || SIGNAL_MODES.includes(q.get("cat")))) state.view.cat = q.get("cat");
    // Normalization: URL wins, then the sticky per-browser preference.
    if (q.get("n") === "1") state.view.normalized = true;
    else if (q.get("n") === "0") state.view.normalized = false;
    else {
        try { state.view.normalized = localStorage.getItem("lsNormalized") === "1"; } catch (e) {}
    }
}

// The drawer named by the hash, or null for an empty hash.
function drawerFromHash() {
    const hash = window.location.hash.replace(/^#/, "");
    if (!hash) return null;
    let parts;
    try { parts = hash.split("/").map(decodeURIComponent); } catch (e) { return null; }
    const [kind, ...rest] = parts;
    if (SIGNAL_MODES.includes(kind)) return { kind: "signal", mode: kind, name: rest.join("/") };
    if (kind === "org") {
        const key = rest.join("/");
        return { kind: "org", key, name: key };
    }
    if (kind === "client") return { kind: "client", name: rest.join("/") };
    if (kind === "filing" && Number.isFinite(Number(rest[0]))) return { kind: "filing", id: Number(rest[0]) };
    return null;
}

function readURL() {
    readViewFromURL();
    state.drawer = drawerFromHash();
    state.drawerStack = [];
    state.histDepth = 0;
}

// Back/Forward, and hand-edited hashes (which arrive with no state).
function onPopState(event) {
    const entry = event.state;
    const before = { ...state.view };
    readViewFromURL();
    if (entry && entry.ls) {
        state.drawer = entry.drawer || null;
        state.drawerStack = entry.stack || [];
        state.histDepth = entry.depth || 0;
    } else {
        state.drawer = drawerFromHash();
        state.drawerStack = [];
        state.histDepth = 0;
        syncURL();
    }
    if (state.closingDrawer) {
        state.closingDrawer = false;
        if (state.drawer) {
            // Landed on a shared-link panel at the bottom of the stack.
            state.drawer = null;
            state.drawerStack = [];
            syncURL();
        }
    }
    if (before.frame !== state.view.frame || before.cat !== state.view.cat || before.normalized !== state.view.normalized) {
        syncControlsUI();
        renderHero();
        renderMovers();
    }
    if (state.drawer) {
        renderDrawer();
        showDrawer();
    } else {
        hideDrawer();
    }
}

/* Reflect state.view in the control groups (used after readURL restores
   a shared link's view). */
function syncControlsUI() {
    // Both rows are toggle-button groups, so state is aria-pressed.
    document.querySelectorAll("#frame-seg .seg-btn").forEach(x => {
        const on = x.dataset.frame === state.view.frame;
        x.classList.toggle("active", on);
        x.setAttribute("aria-pressed", on ? "true" : "false");
    });
    document.querySelectorAll("#cat-row .cat-pill").forEach(x => {
        const on = x.dataset.cat === state.view.cat;
        x.classList.toggle("active", on);
        x.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const norm = document.getElementById("norm-toggle");
    if (norm) {
        norm.classList.toggle("active", state.view.normalized);
        norm.setAttribute("aria-pressed", state.view.normalized ? "true" : "false");
    }
}

/* ─── Initialization ─── */

/* The one comparison-frame toggle, built from trends.json's frame labels so
   the buttons read "Q1 2026 vs Q1 2025" / "Q2 2026 so far". Same spot on
   every tab. */
function buildFrameToggle() {
    const seg = document.getElementById("frame-seg");
    if (!seg) return;
    seg.replaceChildren();
    for (const frameKey of FRAME_KEYS) {
        const b = el("button", "seg-btn", frameToggleLabel(frameKey));
        b.type = "button";
        b.dataset.frame = frameKey;
        if (frameKey === state.view.frame) b.classList.add("active");
        const f = frameInfo(frameKey);
        if (f) {
            b.title = frameKey === "quarter"
                ? "Latest complete report quarter vs the same quarter a year earlier"
                : `Filings for the current quarter posted through ${fmtMonthDay(parseDate(f.through))}, vs the same point last year`;
        }
        b.onclick = () => {
            if (state.view.frame === frameKey) return;
            state.view.frame = frameKey;
            syncControlsUI();
            syncURL();
            renderMovers();
            if (state.drawer) renderDrawer();
        };
        seg.appendChild(b);
    }
}

function bindControls() {
    document.getElementById("search-trigger").onclick = openPalette;
    const kbd = document.querySelector(".search-trigger-kbd");
    const mac = /mac|iphone|ipad/i.test(navigator.userAgentData?.platform || navigator.platform || "");
    if (kbd && !mac) kbd.textContent = "Ctrl K";

    document.querySelectorAll("#cat-row .cat-pill").forEach(b => {
        b.onclick = () => {
            state.view.cat = b.dataset.cat;
            syncControlsUI();
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

    window.addEventListener("popstate", onPopState);
}

function scrollFocusedIntoView() {
    const node = document.querySelector(".palette-result.focused");
    if (node) node.scrollIntoView({ block: "nearest" });
}

/* Data files are cached until the data changes: stats.json (1-2 KB) is
   revalidated on every load, and its generated_at stamps the URLs of the
   larger files. Without a stamp, normal HTTP caching applies. */
let dataVersion = "";

async function loadJSON(file, { revalidate = false } = {}) {
    try {
        const v = dataVersion && !revalidate ? `?v=${encodeURIComponent(dataVersion)}` : "";
        const r = await fetch(`${DATA_PATH}/${file}${v}`, revalidate ? { cache: "no-cache" } : {});
        if (!r.ok) return null;
        return await r.json();
    } catch { return null; }
}

// Data dates: top bar (desktop), a short line under the hero (phones), and
// the LDA retrieval date in the footer and About dialog.
function renderDataDates() {
    const stats = state.stats;
    const ts = stats?.generated_at || state.trends?.generated_at;
    const through = parseDate(stats?.date_range?.end);
    const parts = [];
    if (through) parts.push(`data through ${fmtMonthDayYear(through)}`);
    if (ts) parts.push(`refreshed ${fmt.ago(ts)}`);
    const text = parts.join(" · ");
    const meta = document.getElementById("topbar-meta");
    if (meta) {
        meta.textContent = text;
        meta.title = ts ? `Last refresh: ${new Date(ts).toLocaleString()}` : "";
    }
    const heroAsOf = document.getElementById("hero-asof");
    if (heroAsOf) {
        heroAsOf.textContent = text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
        heroAsOf.hidden = !text;
    }
    const retrieved = parseDate(ts);
    if (retrieved) {
        document.querySelectorAll("[data-retrieved]").forEach(n => {
            n.textContent = ` on ${fmtMonthDayYear(retrieved)}`;
        });
    }
}

async function init() {
    const stats = await loadJSON("stats.json", { revalidate: true });
    state.stats = stats || null;
    dataVersion = stats?.generated_at || "";
    renderDataDates();

    const pending = {
        trends: loadJSON("trends.json"),
        recent: loadJSON("recent.json"),
        timeseries: loadJSON("timeseries.json"),
        clients: loadJSON("clients.json")
    };
    // The headline needs only stats + clients; show it before the 4 MB
    // trends file finishes.
    pending.clients.then(clients => {
        if (state.loaded) return;
        state.clients = clients || null;
        renderHero();
    });
    const [trends, recent, timeseries, clients] = await Promise.all([
        pending.trends, pending.recent, pending.timeseries, pending.clients
    ]);

    state.trends = trends || null;
    state.filings = recent?.filings || [];
    state.timeseries = timeseries || null;
    state.clients = clients || null;
    state.loaded = true;
    paletteIndex = null;

    // clients.json may be missing on a live deploy mid-rollout (new app.js,
    // old data). Hide the Organizations pill and skip it everywhere else
    // rather than show a broken/empty view.
    const clientsPill = document.getElementById("cat-pill-clients");
    if (clientsPill) {
        if (orgMoversAvailable("quarter") || orgMoversAvailable("qtd")) {
            clientsPill.style.display = "";
        } else {
            clientsPill.style.display = "none";
            if (state.view.cat === "clients") state.view.cat = "all";
        }
    }

    renderDataDates();

    bindControls();
    readURL();            // restore shared view + drawer state from URL
    // Stamp the page entry for Back/Forward handling, URL unchanged.
    history.replaceState({ ls: 1, drawer: state.drawer, stack: [], depth: 0 }, "", window.location.href);
    buildFrameToggle();   // labels come from trends.json's frames
    buildNormToggle();    // hidden until the export ships CPI deflators
    syncControlsUI();
    renderHero();
    renderMovers();
    updatePageInert();

    if (state.drawer) {
        renderDrawer();
        showDrawer();
    }
}

document.addEventListener("DOMContentLoaded", init);
