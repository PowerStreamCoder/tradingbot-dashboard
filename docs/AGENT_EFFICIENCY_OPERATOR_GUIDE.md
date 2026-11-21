# Agent Efficiency Measurement & Regular Monitoring — Operator Guide

**Document:** `AGENT_EFFICIENCY_OPERATOR_GUIDE.md`  
**Target Audience:** Algorithmic Trading Operators, Quant Developers, Site Reliability Engineers  
**Related Specification:** [`AGENT_EFFICIENCY_MONITORING.md`](./AGENT_EFFICIENCY_MONITORING.md)  
**Related Issue:** [#139](https://github.com/PowerStreamCoder/tradingbot-dashboard/issues/139)  

---

## 1. Overview & Operational Purpose

The `TradingBots` platform employs a multi-tiered autonomous agent network consisting of **7 specialized agents**. These agents dynamically adapt parameters in live trading:
- Pre-entry setup screening (`AdaptiveEntryAgent`)
- Regime-intent orchestration (`AgentOrchestrator`)
- In-flight dynamic break-even and profit locks (`AdaptivePnLAgent`)
- Covered call overlays (`AdaptiveCoveredCallAgent`)
- Closed-loop threshold auto-tuning (`AdaptiveFeedbackEngine`)
- Offline proposal learning (`LearningEngine`)
- Execution integrity reconciliation (`TradeLogAuditor`)

The **Agent Efficiency Dashboard** (`/agent-efficiency`) provides operators with a centralized, real-time command center to answer two critical operational questions:
1. **Alpha Efficiency ($\text{Eff}_{\text{financial}}$)**: Are our autonomous agent interventions generating net positive R-multiples and shielding profits, or causing premature stop-outs?
2. **Runtime SLA Efficiency ($\text{Eff}_{\text{runtime}}$)**: Are tick evaluations in the exit cascade meeting execution SLAs ($p99 \le 500\text{ ms}$, max $\le 2{,}000\text{ ms}$)?

---

## 2. Navigating the UI (`/agent-efficiency`)

The dashboard is accessible from the top navigation bar via the **`🤖 Agent Efficiency`** tab or directly at `http://<DASHBOARD_URL>/agent-efficiency`.

### Layout Anatomy

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. Header & Live Telemetry Pills (Fleet Health, Active Agents, UTC)    │
├────────────────────────────────────────────────────────────────────────┤
│ 2. Real-Time Alert Banners (SLA breaches, rollbacks, pinned params)   │
├────────────────────────────────────────────────────────────────────────┤
│ 3. Aggregate Alpha Strip (Rolling S, Giveback Saved, Harvested Yield)  │
├────────────────────────────────────────────────────────────────────────┤
│ 4. 7-Agent Fleet Scorecard Grid (Status pills, Headline metrics)       │
├────────────────────────────────────────────────────────────────────────┤
│ 5. Filter Toolbar: Symbol [All/NVDA/IWM/...] | Agent Focus | Window    │
├────────────────────────────────────────────────────────────────────────┤
│ 6. Dynamic Chart.js Visualizations:                                    │
│    • Rolling Effectiveness Trend (St)                                  │
│    • Parameter Drift vs ±35% Clamps                                    │
│    • Cascade Latency Percentiles (p50/p95/p99 vs 500ms SLA)            │
├────────────────────────────────────────────────────────────────────────┤
│ 7. Action Audit & Resolution Ledger Table                              │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Interpreting Agent Scorecards & KPIs

Each of the 7 autonomous agents displays an operational status pill (`HEALTHY`, `DEGRADED`, `STALLED`, `PAUSED`) and key efficiency indicators:

### 🎯 AgentOrchestrator
* **Headline Metric:** `Intent Match Rate` (%)
* **Meaning:** Proportion of trading sessions where the derived `strategy_intent` aligned correctly with the macro regime (`BULL` $\rightarrow$ `TREND_MOMENTUM_GROWTH`, `NEUTRAL` $\rightarrow$ `NEUTRAL_RANGE_HARVEST`, `BEAR` $\rightarrow$ `VOLATILITY_DEFENSIVE`).
* **Degradation Trigger:** Intent match rate falls below 80%.

### 🚪 AdaptiveEntryAgent
* **Headline Metric:** `Filter Selectivity` (%)
* **Meaning:** Percentage of pre-entry technical signals rejected by dynamic MTF and ATR expansion filters ($\frac{\text{Rejected}}{\text{Total Signals}}$).
* **Admitted Win Rate:** Realized win rate of signals that passed through the admission gate.

### 🛡️ AdaptivePnLAgent
* **Headline Metric:** `Average Realized R` ($\bar{R}$)
* **Meaning:** Realized R-multiple across positions where the PnL agent actively intervened (break-even lock, scale-out).
* **Giveback Shield:** Total dollar amount preserved:
  $$\text{Giveback Saved} = \sum \max(0, \text{Peak Unrealized PnL} - \text{Realized PnL})$$
* **Break-Even Conversion Rate:** Percentage of positions that closed green after a break-even stop was locked.

### 📜 AdaptiveCoveredCallAgent
* **Headline Metric:** `Premium Harvested` (\$)
* **Meaning:** Cumulative net option premium captured in range/neutral regimes.
* **Opportunity Cost Delta:** Premium harvested minus capped equity upside. Positive values denote net accretive overlay yield.

### 🧠 AdaptiveFeedbackEngine
* **Headline Metric:** `Rolling S-Score` ($S_t$)
* **Meaning:** Mathematical effectiveness of automated threshold nudges:
  $$S = \frac{1}{N}\sum_{i=1}^N S_i \in [-1.0, 1.0] \quad (N \in [5, 20])$$
  - $+1.0$: Action resulted in a profitable or positive R outcome.
  - $-1.0$: Action resulted in a net loss.
* **Degradation Trigger:** $S_t < -0.20$ or more than 2 circuit-breaker rollback trips in the evaluation window.

### 🔬 LearningEngine
* **Headline Metric:** `Candidate Adoption Rate` (%)
* **Meaning:** Percentage of offline backtest proposals and parameter candidates accepted and applied to live bot profiles.

### 🔍 TradeLogAuditor
* **Headline Metric:** `14-Check Pass Rate` (%)
* **Meaning:** Reconciliation pass rate across the 14 post-market trade-log audit checks.
* **Degradation Trigger:** Any CRITICAL trade-log anomaly detected.

---

## 4. Visual Charts Guide

### Chart 1: Rolling Effectiveness Trend ($S_t$)
* **Y-Axis:** Effectiveness score from $-1.00$ to $+1.00$.
* **Interpretation Zones:**
  - 🟢 **Accretive Zone ($S_t \ge +0.20$):** Agent interventions are net-positive to strategy alpha.
  - ⚪ **Neutral Zone ($-0.20 < S_t < +0.20$):** Interventions preserve capital without significant drag.
  - 🔴 **Hurting Zone ($S_t \le -0.20$):** Interventions are prematurely stopping out winning trades. Investigate underlying parameters.

### Chart 2: Parameter Drift Trajectory
* **Bars:** Current active threshold drift percentage relative to baseline.
* **Safety Boundaries:** Parameters are clamped within $\pm 35\%$ by `HARD_SAFETY_CLAMPS`.
* **Warning Signs:** Bars turning red indicate drift exceeds $\pm 25\%$; pinned parameters indicate a parameter has hit its maximum or minimum safety boundary.

### Chart 3: Cascade Execution Phase Latencies
* **Grouped Bars:** $p50$, $p95$, and $p99$ execution times in milliseconds for:
  - `Adaptive PnL Phase` (`cascade_phase_adaptive_ns`)
  - `Marks & Distances` (`cascade_phase_marks_ns`)
  - `Order Dispatch` (`cascade_phase_orders_ns`)
* **SLA Threshold:** Red line indicates the **500 ms $p99$ SLA**. Any phase bar exceeding 500 ms triggers an immediate alert.

---

## 5. Automated Monitoring & Alerts Runbook

### Alert 1: Latency SLA Breach (`sla_p99_*` or `sla_max_*`)
* **Severity:** HIGH ($p99 > 500\text{ ms}$) or CRITICAL ($\max > 2{,}000\text{ ms}$).
* **Root Cause:** Event-loop contention, slow market data lock resolution, or heavy Firestore writes in tick callback.
* **Operator Action:**
  1. Inspect `execution_metrics` for the affected symbol.
  2. Verify that **proximity throttling** is active (bypassing expensive calculations when price is far from exit barriers).
  3. Check VM CPU utilization via Google Cloud Console.

### Alert 2: Sustained Negative Alpha (`alpha_neg_*`)
* **Severity:** CRITICAL ($S_t < -0.20$).
* **Root Cause:** In-flight stops or break-even locks are too tight, causing trades to exit before the trend thesis materializes.
* **Operator Action:**
  1. Open the Action Audit table and inspect recent `break_even_lock` outcomes.
  2. Review the parameter drift for `break_even_lock_r_multiple`.
  3. If necessary, execute a parameter reset via the Governance Review tab (`/governance-review`).

### Alert 3: Circuit-Breaker Rollback (`cb_*`)
* **Severity:** CRITICAL.
* **Root Cause:** Profit Factor dropped by $\Delta\text{PF} < -0.15$ following recent threshold nudges.
* **Operator Action:**
  1. Verify that `AdaptiveFeedbackEngine` automatically rolled back parameters to baseline.
  2. Inspect `<symbol>.adaptive.json` to confirm quarantine state.

---

## 6. Daily Post-Market Session Digest (17:00 ET)

At market close (17:00 ET / 23:00 CET), the service compiles a session digest:
* Aggregates daily $S$-scores, R-multiples gained/saved, and premium harvested across all active symbols.
* Dispatches a structured summary to the configured webhook URL (`AGENT_EFFICIENCY_WEBHOOK_URL`) and notification email channel.

---

*Authored for the TradingBots Quantitative Trading Operations Group.*
