"""
Agent Efficiency Monitoring Notifier & Digest Dispatcher

Formats and dispatches:
- Daily Post-Market Session Digest (17:00 ET)
- Real-time SLA latency breach warnings
- Proactive degradation alerts (S < -0.20, circuit breaker rollbacks)
"""

import json
import logging
import os
import urllib.request
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_WEBHOOK_ENV_VAR = "AGENT_EFFICIENCY_WEBHOOK_URL"


class AgentEfficiencyNotifier:
    """
    Handles formatting and webhook/email dispatch for agent efficiency digests
    and real-time alerts.
    """

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv(DEFAULT_WEBHOOK_ENV_VAR)

    @staticmethod
    def format_daily_digest_text(summary: Dict[str, Any]) -> str:
        """Formats plain-text daily post-market efficiency digest."""
        alpha = summary.get("aggregate_alpha", {})
        scores = summary.get("agent_scores", {})
        sla = summary.get("sla_metrics", {})
        adaptive_sla = sla.get("cascade_phase_adaptive", {})

        s_score = alpha.get("rolling_effectiveness", 0.0)
        giveback = alpha.get("net_giveback_saved", 0.0)
        premium = alpha.get("premium_harvested", 0.0)
        mean_r = alpha.get("mean_r_multiple", 0.0)

        lines = [
            "==================================================",
            "🤖 AUTONOMOUS FLEET EFFICIENCY DAILY DIGEST",
            "==================================================",
            f"Fleet Health:       {summary.get('fleet_health', 'UNKNOWN')}",
            f"Rolling Score (S):  {s_score:+0.2f}",
            f"Mean Realized R:    {mean_r:+0.2f}R",
            f"Giveback Shield:    ${giveback:,.2f} saved",
            f"Premium Harvested:  ${premium:,.2f}",
            f"Actions Evaluated:  {alpha.get('total_actions_evaluated', 0)}",
            f"Helped / Hurt:      {alpha.get('helped_actions', 0)} / {alpha.get('hurt_actions', 0)}",
            "--------------------------------------------------",
            "AGENT SCORECARDS:",
        ]

        for agent, score_data in sorted(scores.items()):
            metric_name = score_data.get("headline_metric_name", "Metric")
            metric_val = score_data.get("headline_metric_value", "N/A")
            status = score_data.get("status", "UNKNOWN")
            lines.append(f"  • {agent:<24} [{status:<8}] {metric_name}: {metric_val}")

        lines.extend([
            "--------------------------------------------------",
            "RUNTIME & SLA:",
            f"  • Cascade Adaptive Phase p99: {adaptive_sla.get('p99_ms', 0):.1f}ms (SLA: <= 500ms)",
            f"  • SLA Breaches:               {adaptive_sla.get('sla_breaches', 0)}",
            "==================================================",
        ])

        return "\n".join(lines)

    @staticmethod
    def format_daily_digest_html(summary: Dict[str, Any]) -> str:
        """Formats HTML daily post-market efficiency digest."""
        alpha = summary.get("aggregate_alpha", {})
        scores = summary.get("agent_scores", {})
        sla = summary.get("sla_metrics", {})
        adaptive_sla = sla.get("cascade_phase_adaptive", {})

        s_score = alpha.get("rolling_effectiveness", 0.0)
        s_color = "#4ade80" if s_score >= 0.2 else "#f87171" if s_score <= -0.2 else "#38bdf8"

        agent_rows = ""
        for agent, score_data in sorted(scores.items()):
            status = score_data.get("status", "HEALTHY")
            st_color = "#4ade80" if status == "HEALTHY" else "#f87171"
            metric_name = score_data.get("headline_metric_name", "")
            metric_val = score_data.get("headline_metric_value", "")
            agent_rows += f"""
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #334155;"><strong>{agent}</strong></td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #334155; color: {st_color};">{status}</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #334155;">{metric_name}: <strong>{metric_val}</strong></td>
            </tr>
            """

        return f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 24px; border-radius: 8px; max-width: 600px;">
            <h2 style="margin-top: 0; color: #38bdf8;">🤖 Autonomous Fleet Efficiency Digest</h2>
            <div style="background-color: #1e293b; padding: 16px; border-radius: 6px; margin-bottom: 16px;">
                <p style="margin: 4px 0;"><strong>Fleet Health:</strong> <span style="color: {'#4ade80' if summary.get('fleet_health') == 'HEALTHY' else '#f87171'};">{summary.get('fleet_health', 'UNKNOWN')}</span></p>
                <p style="margin: 4px 0;"><strong>Rolling S-Score:</strong> <span style="color: {s_color}; font-weight: bold;">{s_score:+0.2f}</span></p>
                <p style="margin: 4px 0;"><strong>Giveback Saved:</strong> ${alpha.get('net_giveback_saved', 0.0):,.2f}</p>
                <p style="margin: 4px 0;"><strong>Premium Harvested:</strong> ${alpha.get('premium_harvested', 0.0):,.2f}</p>
                <p style="margin: 4px 0;"><strong>Cascade Adaptive p99:</strong> {adaptive_sla.get('p99_ms', 0):.1f}ms</p>
            </div>
            <h3 style="color: #cbd5e1; margin-bottom: 8px;">Agent Scorecards</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 0.9em; background-color: #1e293b; border-radius: 6px;">
                <thead>
                    <tr style="background-color: #334155; text-align: left;">
                        <th style="padding: 8px 12px;">Agent</th>
                        <th style="padding: 8px 12px;">Status</th>
                        <th style="padding: 8px 12px;">Key Metric</th>
                    </tr>
                </thead>
                <tbody>
                    {agent_rows}
                </tbody>
            </table>
        </div>
        """

    @staticmethod
    def format_alert_payload(alert: Dict[str, Any]) -> Dict[str, Any]:
        """Formats a single alert for webhook posting (Slack/Telegram compatible)."""
        severity = alert.get("severity", "WARNING")
        agent = alert.get("agent", "Unknown")
        message = alert.get("message", "")
        metric = alert.get("metric", "")
        val = alert.get("value", "")

        return {
            "text": f"🚨 *[AGENT ALERT - {severity}]* `{agent}`\n{message}\n*Metric:* `{metric} = {val}`",
            "severity": severity,
            "agent": agent,
            "metric": metric,
            "value": val,
            "timestamp": alert.get("timestamp"),
        }

    def dispatch_webhook(self, payload: Dict[str, Any], webhook_url: Optional[str] = None) -> bool:
        """Sends JSON payload to the configured webhook URL."""
        target_url = webhook_url or self.webhook_url
        if not target_url or not target_url.startswith(("http://", "https://")):
            logger.info("No valid http/https webhook URL configured; skipping dispatch.")
            return False

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                target_url,
                data=data,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:  # nosec B310
                return resp.status in (200, 201, 202, 204)
        except Exception as e:
            logger.error(f"Failed to dispatch agent efficiency webhook to {target_url}: {e}")
            return False
