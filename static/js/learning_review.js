/**
 * Learning & Analysis Dashboard JavaScript
 * Handles loading, filtering, and decision-making for bot learning insights
 */

let allFindings = [];

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
}

function formatFindingTitle(title) {
    if (!title) return 'Learning Insight';
    let t = String(title).trim();
    // Check if title ends with truncated "on" or "on <all symbols>"
    if (t.toLowerCase().endsWith(' on') || t.toLowerCase().endsWith(' on <all symbols>') || t.toLowerCase().endsWith(' on all symbols')) {
        t = t.replace(/\s+on(\s*(<all symbols>|all symbols))?$/i, '');
        return `${escapeHtml(t)} on <span class="scope-pill">🌐 All Symbols (Global)</span>`;
    }
    // Replace <all symbols> or All Symbols in title
    if (t.includes('<all symbols>')) {
        return escapeHtml(t).replace(/&lt;all symbols&gt;/gi, '<span class="scope-pill">🌐 All Symbols (Global)</span>');
    }
    return escapeHtml(t);
}

/**
 * Show toast notification
 * @param {string} message - Message to display
 * @param {string} type - Type of toast ('success' or 'error')
 */
function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type} show`;
    setTimeout(() => toast.classList.remove('show'), 3000);
}

/**
 * Load learning findings from API
 */
async function loadFindings() {
    try {
        const response = await fetch('/api/learning-candidates');
        const data = await response.json();
        allFindings = data.findings || [];

        updateStats(allFindings);
        renderFindings(allFindings);
    } catch (error) {
        console.error('Failed to load findings:', error);
        document.getElementById('findings-container').innerHTML = `
            <div class="empty-state">
                <h2>Error loading findings</h2>
                <p>${error.message}</p>
            </div>
        `;
    }
}

/**
 * Update statistics cards
 * @param {Array} findings - Array of finding objects
 */
function updateStats(findings) {
    const pending = findings.filter(f => f.status === 'pending_review').length;
    const high = findings.filter(f => f.priority === 'high').length;

    let potentialSavings = 0;
    findings.forEach(f => {
        if (f.backtest_results && f.backtest_results.pnl_improvement) {
            potentialSavings += f.backtest_results.pnl_improvement;
        }
    });

    document.getElementById('stat-pending').textContent = pending;
    document.getElementById('stat-high').textContent = high;
    document.getElementById('stat-savings').textContent = `$${potentialSavings.toFixed(0)}`;

    // Find most recent finding
    if (findings.length > 0) {
        const latest = findings.reduce((a, b) =>
            new Date(a.created_at) > new Date(b.created_at) ? a : b
        );
        const date = new Date(latest.created_at);
        document.getElementById('stat-last-run').textContent = date.toLocaleDateString();
    } else {
        document.getElementById('stat-last-run').textContent = 'Never';
    }
}

/**
 * Render filtered findings
 * @param {Array} findings - Array of finding objects
 */
function renderFindings(findings) {
    const container = document.getElementById('findings-container');

    // Apply filters
    const priorityFilter = document.getElementById('filter-priority').value;
    const typeFilter = document.getElementById('filter-type').value;

    let filtered = findings.filter(f => f.status === 'pending_review');

    if (priorityFilter !== 'all') {
        filtered = filtered.filter(f => f.priority === priorityFilter);
    }

    if (typeFilter !== 'all') {
        filtered = filtered.filter(f => f.type === typeFilter);
    }

    if (filtered.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <h2>🎉 All clear!</h2>
                <p>No pending learning candidates at the moment.</p>
            </div>
        `;
        return;
    }

    // Sort by priority (high -> medium -> low)
    const priorityOrder = { high: 0, medium: 1, low: 2 };
    filtered.sort((a, b) => priorityOrder[a.priority] - priorityOrder[b.priority]);

    container.innerHTML = filtered.map(finding => `
        <div class="finding-card">
            <div class="finding-header">
                <div class="finding-title">
                    <h2>${formatFindingTitle(finding.title)}</h2>
                    <div class="finding-id">${escapeHtml(finding.finding_id)}</div>
                </div>
                <div class="badges">
                    <span class="badge ${escapeHtml(finding.priority)}">${escapeHtml(finding.priority)}</span>
                    <span class="badge ${escapeHtml(finding.type)}">${escapeHtml(String(finding.type || '').replace(/_/g, ' '))}</span>
                </div>
            </div>

            <div class="finding-description">
                ${escapeHtml(finding.description)}
            </div>

            <div class="evidence-section">
                <h3>📊 Telemetry &amp; Backtest Evidence</h3>
                <div class="evidence-grid">
                    ${renderEvidenceItems(finding.evidence)}
                </div>
            </div>

            ${renderProposedRule(finding.proposed_rule, finding.config_changes, finding)}

            <div class="user-notes">
                <label for="notes-${escapeHtml(finding.finding_id)}">Your Notes / Modifications:</label>
                <textarea
                    id="notes-${escapeHtml(finding.finding_id)}"
                    placeholder="Add context, suggest modifications, or explain your decision..."
                ></textarea>
            </div>

            <div class="actions">
                <button class="btn-action btn-approve" onclick="handleDecision('${escapeHtml(finding.finding_id)}', 'approved')">
                    ✓ Approve & Apply
                </button>
                <button class="btn-action btn-modify" onclick="handleDecision('${escapeHtml(finding.finding_id)}', 'modified')">
                    ✎ Request Modification
                </button>
                <button class="btn-action btn-reject" onclick="handleDecision('${escapeHtml(finding.finding_id)}', 'rejected')">
                    ✗ Reject
                </button>
            </div>
        </div>
    `).join('');
}

const BRANCH_LABELS = {
    'overnight_gap': 'Overnight Gap Protection',
    'emergency': 'Emergency Circuit Breaker',
    'stop_loss': 'Hard Stop Loss',
    'atr_stop': 'Dynamic ATR Stop',
    'trailing_stop': 'Profit Trailing Stop',
    'regime_flip': 'Macro Regime Shift',
    'sma_slope': 'SMA Slope Reversal',
    'profit_target': 'Take Profit Target',
    'max_hold': 'Max Holding Duration',
    'hold': 'Position Holding State'
};

/**
 * Render structured proposed rule section
 */
function renderProposedRule(rule, configChanges, finding) {
    rule = rule || {};
    configChanges = configChanges || {};
    const configKeys = Object.keys(configChanges);

    // 1. Suggestion or primary recommendation banner
    let suggestionHtml = '';
    const suggestionText = rule.suggestion || rule.action || (finding && finding.recommendations && finding.recommendations[0]) || '';
    if (suggestionText) {
        suggestionHtml = `
            <div class="rule-suggestion-banner">
                <span class="suggestion-icon">💡</span>
                <div class="suggestion-body">
                    <strong>Recommended Strategy Action:</strong>
                    <div class="suggestion-text">${escapeHtml(suggestionText)}</div>
                </div>
            </div>
        `;
    }

    // 2. Structured Metadata Grid
    const cards = [];

    // Target Branch if present
    if (rule.branch) {
        const branchName = BRANCH_LABELS[rule.branch] || rule.branch.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
        cards.push(`
            <div class="rule-meta-card">
                <span class="meta-label">Target Exit Branch</span>
                <span class="meta-val"><strong>${escapeHtml(branchName)}</strong> (<code>${escapeHtml(rule.branch)}</code>)</span>
            </div>
        `);
    }

    // Kind / Reachability Issue if present
    if (rule.kind) {
        const isDead = rule.kind === 'dead';
        const kindLabel = isDead ? '🟣 Dead (Evaluated but Unfired)' : '🟡 Shadowed (Never Reached by Cascade)';
        cards.push(`
            <div class="rule-meta-card">
                <span class="meta-label">Diagnostic Status</span>
                <span class="meta-val"><span class="chip ${isDead ? 'chip-dead' : 'chip-shadowed'}">${kindLabel}</span></span>
            </div>
        `);
    }

    // Type if valid (not undefined or empty)
    if (rule.type && rule.type !== 'undefined') {
        cards.push(`
            <div class="rule-meta-card">
                <span class="meta-label">Rule Type</span>
                <span class="meta-val"><code>${escapeHtml(String(rule.type).replace(/_/g, ' '))}</code></span>
            </div>
        `);
    }

    // Action if valid (not undefined, empty, or duplicate of suggestion)
    if (rule.action && rule.action !== 'undefined' && rule.action !== suggestionText) {
        cards.push(`
            <div class="rule-meta-card">
                <span class="meta-label">Proposed Action</span>
                <span class="meta-val"><code>${escapeHtml(String(rule.action).replace(/_/g, ' '))}</code></span>
            </div>
        `);
    }

    // Config Field / Parameter
    const targetField = rule.owner_field || rule.param || (configKeys.length ? configKeys[0] : null);
    if (targetField) {
        cards.push(`
            <div class="rule-meta-card">
                <span class="meta-label">Configuration Field</span>
                <span class="meta-val"><code>${escapeHtml(targetField)}</code></span>
            </div>
        `);
    }

    // Proposed Value
    const propVal = rule.proposed_value !== undefined ? rule.proposed_value : (targetField ? configChanges[targetField] : undefined);
    if (propVal !== undefined && propVal !== null && propVal !== '') {
        cards.push(`
            <div class="rule-meta-card">
                <span class="meta-label">Proposed Value</span>
                <span class="meta-val val-proposed">${escapeHtml(String(propVal))}</span>
            </div>
        `);
    } else if (rule.kind === 'shadowed' || !propVal) {
        cards.push(`
            <div class="rule-meta-card">
                <span class="meta-label">Implementation</span>
                <span class="meta-val val-structural">Structural / Cascade Reordering</span>
            </div>
        `);
    }

    // Parameter Diff list if configChanges has items
    let configChangesHtml = '';
    if (configKeys.length) {
        const changeRows = configKeys.map(k => `
            <div class="config-diff-item">
                <code>${escapeHtml(k)}</code> ➔ <strong class="new-val">${escapeHtml(String(configChanges[k]))}</strong>
            </div>
        `).join('');
        configChangesHtml = `
            <div class="config-changes-preview">
                <span class="diff-title">Configuration Parameter Overlays to Apply:</span>
                ${changeRows}
            </div>
        `;
    }

    return `
        <div class="proposed-rule">
            <h4>📝 Proposed Rule &amp; Action Plan</h4>
            ${suggestionHtml}
            <div class="rule-meta-grid">
                ${cards.join('')}
            </div>
            ${configChangesHtml}
            <details class="rule-raw-details">
                <summary>View Technical Rule Payload (JSON)</summary>
                <pre>${escapeHtml(JSON.stringify(rule, null, 2))}</pre>
            </details>
        </div>
    `;
}

/**
 * Render evidence items for a finding
 * @param {Object} evidence - Evidence data object
 * @returns {string} HTML string of evidence items
 */
function renderEvidenceItems(evidence) {
    if (!evidence || typeof evidence !== 'object') {
        return '<div class="evidence-empty">No telemetry evidence attached.</div>';
    }
    const items = [];

    // Session-based telemetry evidence
    if (evidence.session_count !== undefined) {
        items.push(`
            <div class="evidence-item">
                <div class="label">Sessions Analyzed</div>
                <div class="value">${evidence.session_count}</div>
            </div>
        `);
    }

    if (evidence.shadowed_sessions !== undefined) {
        const pct = evidence.session_count ? ((evidence.shadowed_sessions / evidence.session_count) * 100).toFixed(0) : '0';
        items.push(`
            <div class="evidence-item item-alert">
                <div class="label">Shadowed Sessions</div>
                <div class="value">${evidence.shadowed_sessions} <span class="sub-val">(${pct}%)</span></div>
            </div>
        `);
    }

    if (evidence.dead_sessions !== undefined) {
        const pct = evidence.session_count ? ((evidence.dead_sessions / evidence.session_count) * 100).toFixed(0) : '0';
        items.push(`
            <div class="evidence-item ${evidence.dead_sessions > 0 ? 'item-alert' : ''}">
                <div class="label">Dead / Unfired Sessions</div>
                <div class="value">${evidence.dead_sessions} <span class="sub-val">(${pct}%)</span></div>
            </div>
        `);
    }

    if (evidence.fired_sessions !== undefined) {
        items.push(`
            <div class="evidence-item ${evidence.fired_sessions > 0 ? 'item-success' : ''}">
                <div class="label">Fired Sessions</div>
                <div class="value">${evidence.fired_sessions}</div>
            </div>
        `);
    }

    if (evidence.total_evaluated !== undefined) {
        items.push(`
            <div class="evidence-item">
                <div class="label">Total Cascade Checks</div>
                <div class="value">${evidence.total_evaluated}</div>
            </div>
        `);
    }

    if (evidence.max_proximity !== undefined) {
        const proxPct = (Number(evidence.max_proximity) * 100).toFixed(0);
        items.push(`
            <div class="evidence-item">
                <div class="label">Peak Proximity Saturation</div>
                <div class="value">${Number(evidence.max_proximity).toFixed(2)} <span class="sub-val">(${proxPct}%)</span></div>
            </div>
        `);
    }

    // Trade-based evidence
    if (evidence.sample_size) {
        items.push(`
            <div class="evidence-item">
                <div class="label">Sample Size</div>
                <div class="value">${evidence.sample_size} trades</div>
            </div>
        `);
    }

    if (evidence.win_rate !== undefined) {
        items.push(`
            <div class="evidence-item">
                <div class="label">Win Rate</div>
                <div class="value">${(evidence.win_rate * 100).toFixed(1)}%</div>
            </div>
        `);
    }

    if (evidence.avg_pnl !== undefined) {
        const pnlFormatted = typeof formatPrice === 'function' ? formatPrice(evidence.avg_pnl) : `$${evidence.avg_pnl}`;
        items.push(`
            <div class="evidence-item">
                <div class="label">Avg P&L</div>
                <div class="value">${pnlFormatted}</div>
            </div>
        `);
    }

    if (evidence.total_pnl !== undefined) {
        const pnlFormatted = typeof formatPrice === 'function' ? formatPrice(evidence.total_pnl) : `$${evidence.total_pnl}`;
        items.push(`
            <div class="evidence-item">
                <div class="label">Total P&L</div>
                <div class="value">${pnlFormatted}</div>
            </div>
        `);
    }

    if (evidence.lookback_days) {
        items.push(`
            <div class="evidence-item">
                <div class="label">Lookback Period</div>
                <div class="value">${evidence.lookback_days} Days</div>
            </div>
        `);
    }

    if (evidence.source) {
        items.push(`
            <div class="evidence-item">
                <div class="label">Telemetry Source</div>
                <div class="value" style="font-size: 1.05em;">${escapeHtml(String(evidence.source).replace(/_/g, ' '))}</div>
            </div>
        `);
    }

    return items.join('') || '<div class="evidence-empty">No additional metrics available.</div>';
}

/**
 * Handle user decision on a finding
 * @param {string} findingId - ID of the finding
 * @param {string} decision - Decision made ('approved', 'modified', or 'rejected')
 */
async function handleDecision(findingId, decision) {
    const notes = document.getElementById(`notes-${findingId}`).value;

    if (decision === 'approved' && !confirm('Are you sure you want to approve this change? It will be applied to the trading strategy.')) {
        return;
    }

    try {
        const response = await fetch('/api/learning-candidates/decision', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                finding_id: findingId,
                decision: decision,
                user_notes: notes
            })
        });

        if (response.ok) {
            showToast(`Finding ${decision} successfully!`, 'success');
            loadFindings();  // Reload to remove processed finding
        } else {
            showToast('Failed to save decision. Please try again.', 'error');
        }
    } catch (error) {
        console.error('Error saving decision:', error);
        showToast('Network error. Please check your connection.', 'error');
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Filter change handlers
    document.getElementById('filter-priority').addEventListener('change', () => {
        renderFindings(allFindings);
    });

    document.getElementById('filter-type').addEventListener('change', () => {
        renderFindings(allFindings);
    });

    // Load findings
    loadFindings();
});
