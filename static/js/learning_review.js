/**
 * Learning & Analysis Dashboard JavaScript
 * Handles loading, filtering, and decision-making for bot learning insights
 */

let allFindings = [];

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
                    <h2>${finding.title}</h2>
                    <div class="finding-id">${finding.finding_id}</div>
                </div>
                <div class="badges">
                    <span class="badge ${finding.priority}">${finding.priority}</span>
                    <span class="badge ${finding.type}">${finding.type.replace('_', ' ')}</span>
                </div>
            </div>

            <div class="finding-description">
                ${finding.description}
            </div>

            <div class="evidence-section">
                <h3>📊 Evidence</h3>
                <div class="evidence-grid">
                    ${renderEvidenceItems(finding.evidence)}
                </div>
            </div>

            <div class="proposed-rule">
                <h4>📝 Proposed Rule</h4>
                <strong>Type:</strong> ${finding.proposed_rule.type}<br>
                <strong>Action:</strong> ${finding.proposed_rule.action}
                <pre>${JSON.stringify(finding.proposed_rule, null, 2)}</pre>
            </div>

            <div class="user-notes">
                <label for="notes-${finding.finding_id}">Your Notes / Modifications:</label>
                <textarea
                    id="notes-${finding.finding_id}"
                    placeholder="Add context, suggest modifications, or explain your decision..."
                ></textarea>
            </div>

            <div class="actions">
                <button class="btn-action btn-approve" onclick="handleDecision('${finding.finding_id}', 'approved')">
                    ✓ Approve & Apply
                </button>
                <button class="btn-action btn-modify" onclick="handleDecision('${finding.finding_id}', 'modified')">
                    ✎ Request Modification
                </button>
                <button class="btn-action btn-reject" onclick="handleDecision('${finding.finding_id}', 'rejected')">
                    ✗ Reject
                </button>
            </div>
        </div>
    `).join('');
}

/**
 * Render evidence items for a finding
 * @param {Object} evidence - Evidence data object
 * @returns {string} HTML string of evidence items
 */
function renderEvidenceItems(evidence) {
    const items = [];

    if (evidence.sample_size) {
        items.push(`
            <div class="evidence-item">
                <div class="label">Sample Size</div>
                <div class="value">${evidence.sample_size}</div>
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
        items.push(`
            <div class="evidence-item">
                <div class="label">Avg P&L</div>
                <div class="value">${formatPrice(evidence.avg_pnl)}</div>
            </div>
        `);
    }

    if (evidence.total_pnl !== undefined) {
        items.push(`
            <div class="evidence-item">
                <div class="label">Total P&L</div>
                <div class="value">${formatPrice(evidence.total_pnl)}</div>
            </div>
        `);
    }

    return items.join('');
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
