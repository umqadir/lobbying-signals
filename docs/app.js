// Lobbying Signals Dashboard

const DATA_PATH = 'data';

// Utility functions
function formatNumber(num) {
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toLocaleString();
}

function formatCurrency(num) {
    if (!num) return '-';
    if (num >= 1000000) return '$' + (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return '$' + (num / 1000).toFixed(0) + 'K';
    return '$' + num.toLocaleString();
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function timeAgo(dateStr) {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    return `${diffDays}d ago`;
}

// Data loading
async function loadJSON(filename) {
    try {
        const response = await fetch(`${DATA_PATH}/${filename}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (err) {
        console.error(`Failed to load ${filename}:`, err);
        return null;
    }
}

// Render functions
function renderAlerts(data) {
    const container = document.getElementById('alerts-container');
    if (!data || !data.alerts || data.alerts.length === 0) {
        container.innerHTML = '<div class="loading">No notable trends detected</div>';
        return;
    }

    container.innerHTML = data.alerts.map(alert => `
        <div class="alert-card ${alert.type}">
            <div class="alert-headline">${escapeHtml(alert.headline)}</div>
            <div class="alert-meta">
                ${alert.current_count} activities
                ${alert.change_pct ? `(${alert.change_pct > 0 ? '+' : ''}${alert.change_pct.toFixed(0)}%)` : ''}
            </div>
            ${alert.top_clients && alert.top_clients.length > 0 ? `
                <div class="alert-clients">
                    Top clients: ${alert.top_clients.slice(0, 3).map(c => escapeHtml(c)).join(', ')}
                </div>
            ` : ''}
        </div>
    `).join('');
}

function renderStats(data) {
    if (!data) return;

    document.getElementById('stat-filings').textContent = formatNumber(data.total_filings);
    document.getElementById('stat-activities').textContent = formatNumber(data.total_activities);
    document.getElementById('stat-extracted').textContent = `${data.extracted_pct}%`;

    if (data.date_range && data.date_range.start) {
        const start = new Date(data.date_range.start).getFullYear();
        const end = new Date(data.date_range.end).getFullYear();
        document.getElementById('stat-range').textContent = start === end ? start : `${start}-${end}`;
    }

    // Update last updated time
    if (data.generated_at) {
        document.getElementById('last-updated').textContent = `Last updated: ${timeAgo(data.generated_at)}`;
    }
}

function renderTrends(data, period = '30d') {
    const container = document.getElementById('trends-container');
    if (!data || !data.topics || !data.topics[period]) {
        container.innerHTML = '<div class="loading">No trend data available</div>';
        return;
    }

    const topics = data.topics[period].slice(0, 20);
    container.innerHTML = topics.map(topic => `
        <div class="trend-item">
            <span class="trend-name">${escapeHtml(topic.name)}</span>
            <span class="trend-count">${topic.count} activities</span>
            <span class="trend-change ${topic.change_pct >= 0 ? 'positive' : 'negative'}">
                ${topic.change_pct >= 0 ? '+' : ''}${topic.change_pct.toFixed(0)}%
            </span>
        </div>
    `).join('');
}

function renderEntities(data) {
    const container = document.getElementById('entities-container');
    if (!data || !data.entities || !data.entities['30d']) {
        container.innerHTML = '<div class="loading">No entity data available</div>';
        return;
    }

    const entities = data.entities['30d'].slice(0, 15);
    container.innerHTML = entities.map(entity => `
        <div class="trend-item">
            <span class="trend-name">${escapeHtml(entity.name)}</span>
            <span class="trend-count">${entity.count} mentions</span>
            <span class="trend-change ${entity.change_pct >= 0 ? 'positive' : 'negative'}">
                ${entity.change_pct >= 0 ? '+' : ''}${entity.change_pct.toFixed(0)}%
            </span>
        </div>
    `).join('');
}

function renderRecent(data) {
    const container = document.getElementById('recent-container');
    if (!data || !data.filings || data.filings.length === 0) {
        container.innerHTML = '<div class="loading">No recent filings</div>';
        return;
    }

    container.innerHTML = data.filings.slice(0, 20).map(filing => `
        <div class="filing-item">
            <div class="filing-header">
                <span class="filing-client">${escapeHtml(filing.client)}</span>
                <span class="filing-income">${formatCurrency(filing.income)}</span>
            </div>
            <div class="filing-meta">
                ${escapeHtml(filing.registrant)} &bull; ${formatDate(filing.date)} &bull; Q${filing.quarter} ${filing.year}
            </div>
            ${filing.topics && filing.topics.length > 0 ? `
                <div class="filing-topics">
                    ${filing.topics.map(t => `<span class="topic-tag">${escapeHtml(t)}</span>`).join('')}
                </div>
            ` : ''}
        </div>
    `).join('');
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Tab switching
function setupTabs(trendsData) {
    const buttons = document.querySelectorAll('.tab-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderTrends(trendsData, btn.dataset.period);
        });
    });
}

// Initialize dashboard
async function init() {
    // Load all data in parallel
    const [alerts, stats, trends, recent] = await Promise.all([
        loadJSON('alerts.json'),
        loadJSON('stats.json'),
        loadJSON('trends.json'),
        loadJSON('recent.json')
    ]);

    // Render each section
    renderAlerts(alerts);
    renderStats(stats);
    renderTrends(trends, '30d');
    renderEntities(trends);
    renderRecent(recent);

    // Setup tab switching
    if (trends) {
        setupTabs(trends);
    }
}

// Start
document.addEventListener('DOMContentLoaded', init);
