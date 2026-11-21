/**
 * Agent Efficiency Measurement & Monitoring Frontend Logic
 */

let currentSymbol = "ALL";
let currentAgent = "all";
let currentWindow = 7;

let effectivenessChartInstance = null;
let parameterDriftChartInstance = null;
let latencyChartInstance = null;

document.addEventListener("DOMContentLoaded", () => {
    loadEfficiencyData();
    // Poll every 60 seconds
    setInterval(loadEfficiencyData, 60000);
});

async function loadEfficiencyData() {
    try {
        const [summaryRes, alertsRes, histRes] = await Promise.all([
            fetch("/api/agent-efficiency/summary").then(r => r.ok ? r.json() : null),
            fetch("/api/agent-efficiency/alerts").then(r => r.ok ? r.json() : null),
            fetch(`/api/agent-efficiency/history?agent=${encodeURIComponent(currentAgent)}&window=${currentWindow}`).then(r => r.ok ? r.json() : null)
        ]);

        if (summaryRes) {
            renderSummary(summaryRes);
            renderAgentGrid(summaryRes.agent_scores || {});
            renderLatencyChart(summaryRes.sla_metrics || {});
        }

        if (alertsRes) {
            renderAlerts(alertsRes.alerts || []);
        }

        if (histRes) {
            renderEffectivenessChart(histRes.series || []);
        }

        // If a specific bot is selected, fetch bot-specific telemetry
        if (currentSymbol !== "ALL") {
            const botRes = await fetch(`/api/agent-efficiency/bot/${encodeURIComponent(currentSymbol)}`).then(r => r.ok ? r.json() : null);
            if (botRes) {
                renderBotParameters(botRes.parameter_states || {});
                renderAuditTable(botRes.recent_actions || []);
            }
        } else {
            // Default: use actions from summary if present or fetch nvda sample
            const botRes = await fetch("/api/agent-efficiency/bot/NVDA").then(r => r.ok ? r.json() : null);
            if (botRes) {
                renderBotParameters(botRes.parameter_states || {});
                renderAuditTable(botRes.recent_actions || []);
            }
        }
    } catch (err) {
        console.error("Error loading agent efficiency data:", err);
    }
}

function renderSummary(summary) {
    const healthBadge = document.getElementById("fleetHealthBadge");
    if (healthBadge) {
        healthBadge.textContent = summary.fleet_health || "UNKNOWN";
        healthBadge.className = `value font-mono ${summary.fleet_health === "HEALTHY" ? "val-positive" : "val-negative"}`;
    }

    const updatedEl = document.getElementById("lastUpdatedVal");
    if (updatedEl && summary.timestamp) {
        updatedEl.textContent = summary.timestamp.substring(11, 19) + " UTC";
    }

    const alpha = summary.aggregate_alpha || {};
    const scoreVal = document.getElementById("alphaScoreVal");
    if (scoreVal) {
        const s = alpha.rolling_effectiveness || 0.0;
        scoreVal.textContent = (s >= 0 ? "+" : "") + s.toFixed(2);
        scoreVal.className = `alpha-stat-value ${s >= 0.2 ? "val-positive" : s <= -0.2 ? "val-negative" : "val-neutral"}`;
    }

    const gbEl = document.getElementById("givebackSavedVal");
    if (gbEl) gbEl.textContent = "$" + (alpha.net_giveback_saved || 0).toLocaleString("en-US", { minimumFractionDigits: 2 });

    const premEl = document.getElementById("premiumHarvestedVal");
    if (premEl) premEl.textContent = "$" + (alpha.premium_harvested || 0).toLocaleString("en-US", { minimumFractionDigits: 2 });

    const meanREl = document.getElementById("meanRVal");
    if (meanREl) meanREl.textContent = (alpha.mean_r_multiple || 0).toFixed(2) + "R";

    const totalActEl = document.getElementById("totalActionsVal");
    if (totalActEl) totalActEl.textContent = alpha.total_actions_evaluated || 0;

    const helpedHurtEl = document.getElementById("helpedHurtVal");
    if (helpedHurtEl) helpedHurtEl.textContent = `${alpha.helped_actions || 0} / ${alpha.hurt_actions || 0}`;
}

function renderAlerts(alerts) {
    const container = document.getElementById("alertsContainer");
    if (!container) return;

    if (!alerts || alerts.length === 0) {
        container.style.display = "none";
        container.innerHTML = "";
        return;
    }

    container.style.display = "flex";
    container.innerHTML = alerts.map(a => `
        <div class="alert-badge-item ${a.severity ? a.severity.toLowerCase() : 'warning'}">
            <span>🚨</span>
            <div>
                <strong>[${a.severity}] ${a.agent}:</strong> ${a.message}
                <span style="opacity: 0.7; font-size: 0.75rem; margin-left: 8px;">${(a.timestamp || '').substring(11, 19)}</span>
            </div>
        </div>
    `).join("");
}

function renderAgentGrid(agentScores) {
    const grid = document.getElementById("agentFleetGrid");
    if (!grid) return;

    const agentIcons = {
        "AgentOrchestrator": "🎯",
        "AdaptiveEntryAgent": "🚪",
        "AdaptivePnLAgent": "🛡️",
        "AdaptiveCoveredCallAgent": "📜",
        "AdaptiveFeedbackEngine": "🧠",
        "LearningEngine": "🔬",
        "TradeLogAuditor": "🔍"
    };

    grid.innerHTML = Object.entries(agentScores).map(([agentName, data]) => {
        const icon = agentIcons[agentName] || "🤖";
        const statusClass = (data.status || "healthy").toLowerCase();
        return `
            <div class="agent-mini-card">
                <div>
                    <div class="agent-card-header">
                        <span class="agent-title">${icon} ${agentName}</span>
                        <span class="status-pill ${statusClass}">${data.status}</span>
                    </div>
                    <div class="agent-metric-lbl">${data.headline_metric_name}</div>
                    <div class="agent-metric-val">${data.headline_metric_value}</div>
                </div>
                <div class="agent-desc">${data.summary || ""}</div>
            </div>
        `;
    }).join("");
}

function renderEffectivenessChart(series) {
    const ctx = document.getElementById("effectivenessChart");
    if (!ctx) return;

    const labels = series.map(s => s.date);
    const dataPoints = series.map(s => s.rolling_score);

    if (effectivenessChartInstance) {
        effectivenessChartInstance.destroy();
    }

    effectivenessChartInstance = new Chart(ctx, {
        type: "line",
        data: {
            labels: labels,
            datasets: [
                {
                    label: "Rolling Effectiveness (S)",
                    data: dataPoints,
                    borderColor: "#38bdf8",
                    backgroundColor: "rgba(56, 189, 248, 0.1)",
                    borderWidth: 2,
                    tension: 0.25,
                    fill: true,
                    pointBackgroundColor: dataPoints.map(v => v >= 0.2 ? "#4ade80" : v <= -0.2 ? "#f87171" : "#38bdf8")
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    min: -1.0,
                    max: 1.0,
                    grid: { color: "rgba(255, 255, 255, 0.08)" },
                    ticks: { color: "#94a3b8" }
                },
                x: {
                    grid: { color: "rgba(255, 255, 255, 0.05)" },
                    ticks: { color: "#94a3b8" }
                }
            },
            plugins: {
                legend: { labels: { color: "#cbd5e1" } }
            }
        }
    });
}

function renderBotParameters(parameterStates) {
    const ctx = document.getElementById("parameterDriftChart");
    if (!ctx) return;

    const labels = Object.keys(parameterStates);
    const driftPcts = labels.map(k => parameterStates[k].drift_pct);
    const bgColors = driftPcts.map(d => Math.abs(d) > 25 ? "rgba(239, 68, 68, 0.7)" : "rgba(99, 102, 241, 0.7)");

    if (parameterDriftChartInstance) {
        parameterDriftChartInstance.destroy();
    }

    parameterDriftChartInstance = new Chart(ctx, {
        type: "bar",
        data: {
            labels: labels.map(l => l.replace(/_/g, " ")),
            datasets: [
                {
                    label: "Drift vs Baseline (%)",
                    data: driftPcts,
                    backgroundColor: bgColors,
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    min: -40,
                    max: 40,
                    grid: { color: "rgba(255, 255, 255, 0.08)" },
                    ticks: {
                        color: "#94a3b8",
                        callback: v => v + "%"
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: "#94a3b8", font: { size: 10 } }
                }
            },
            plugins: {
                legend: { labels: { color: "#cbd5e1" } }
            }
        }
    });
}

function renderLatencyChart(slaMetrics) {
    const ctx = document.getElementById("latencyChart");
    if (!ctx) return;

    const phases = ["Adaptive PnL Phase", "Marks & Distances", "Order Dispatch"];
    const p50s = [
        (slaMetrics.cascade_phase_adaptive || {}).p50_ms || 0,
        (slaMetrics.cascade_phase_marks || {}).p50_ms || 0,
        (slaMetrics.cascade_phase_orders || {}).p50_ms || 0,
    ];
    const p95s = [
        (slaMetrics.cascade_phase_adaptive || {}).p95_ms || 0,
        (slaMetrics.cascade_phase_marks || {}).p95_ms || 0,
        (slaMetrics.cascade_phase_orders || {}).p95_ms || 0,
    ];
    const p99s = [
        (slaMetrics.cascade_phase_adaptive || {}).p99_ms || 0,
        (slaMetrics.cascade_phase_marks || {}).p99_ms || 0,
        (slaMetrics.cascade_phase_orders || {}).p99_ms || 0,
    ];

    if (latencyChartInstance) {
        latencyChartInstance.destroy();
    }

    latencyChartInstance = new Chart(ctx, {
        type: "bar",
        data: {
            labels: phases,
            datasets: [
                { label: "p50 (ms)", data: p50s, backgroundColor: "rgba(56, 189, 248, 0.6)" },
                { label: "p95 (ms)", data: p95s, backgroundColor: "rgba(234, 179, 8, 0.6)" },
                { label: "p99 (ms)", data: p99s, backgroundColor: "rgba(239, 68, 68, 0.6)" }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: "rgba(255, 255, 255, 0.08)" },
                    ticks: { color: "#94a3b8", callback: v => v + " ms" }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: "#cbd5e1" }
                }
            },
            plugins: {
                legend: { labels: { color: "#cbd5e1" } }
            }
        }
    });
}

function renderAuditTable(actions) {
    const tbody = document.getElementById("auditTableBody");
    if (!tbody) return;

    if (!actions || actions.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: #64748b; padding: 24px;">No recent actions recorded.</td></tr>`;
        return;
    }

    tbody.innerHTML = actions.slice(0, 50).map(a => {
        const outcome = a.outcome || {};
        const pnl = a.final_pnl !== undefined ? a.final_pnl : outcome.final_pnl;
        const pnlStr = pnl !== undefined ? (pnl >= 0 ? `+$${pnl.toFixed(2)}` : `-$${Math.abs(pnl).toFixed(2)}`) : "--";
        const pnlClass = pnl !== undefined ? (pnl > 0 ? "val-positive" : pnl < 0 ? "val-negative" : "") : "";
        const rVal = a.realized_r !== undefined ? a.realized_r : outcome.realized_r;
        const rStr = rVal !== undefined && rVal !== null ? `${Number(rVal).toFixed(2)}R` : "--";
        const isResolved = a.resolved || outcome.resolved;

        return `
            <tr>
                <td style="font-family:'JetBrains Mono',monospace;">${(a.created_at || '').substring(0, 19).replace('T', ' ')}</td>
                <td><strong>${a.symbol || '--'}</strong></td>
                <td><code>${a.action_type || '--'}</code></td>
                <td>${a.threshold_used !== undefined ? a.threshold_used : '--'}</td>
                <td class="${pnlClass}">${pnlStr}</td>
                <td>${rStr}</td>
                <td>
                    <span class="status-pill ${isResolved ? 'healthy' : 'paused'}">
                        ${isResolved ? 'RESOLVED' : 'IN-FLIGHT'}
                    </span>
                </td>
            </tr>
        `;
    }).join("");
}

function onSymbolFilterChange() {
    const select = document.getElementById("symbolFilter");
    if (select) {
        currentSymbol = select.value;
        loadEfficiencyData();
    }
}

function onAgentFilterChange() {
    const select = document.getElementById("agentFilter");
    if (select) {
        currentAgent = select.value;
        loadEfficiencyData();
    }
}

function onWindowFilterChange() {
    const select = document.getElementById("windowFilter");
    if (select) {
        currentWindow = parseInt(select.value, 10);
        loadEfficiencyData();
    }
}
