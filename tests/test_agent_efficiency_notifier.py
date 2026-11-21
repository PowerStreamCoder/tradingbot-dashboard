"""
Unit tests for AgentEfficiencyNotifier.
"""

import unittest
from unittest.mock import patch, MagicMock

from services.agent_efficiency_notifier import AgentEfficiencyNotifier


class TestAgentEfficiencyNotifier(unittest.TestCase):
    def setUp(self):
        self.sample_summary = {
            "fleet_health": "HEALTHY",
            "aggregate_alpha": {
                "rolling_effectiveness": 0.45,
                "net_giveback_saved": 420.50,
                "premium_harvested": 850.00,
                "mean_r_multiple": 1.25,
                "total_actions_evaluated": 15,
                "helped_actions": 12,
                "hurt_actions": 3,
            },
            "agent_scores": {
                "AdaptivePnLAgent": {
                    "status": "HEALTHY",
                    "headline_metric_name": "Average Realized R",
                    "headline_metric_value": "1.25R",
                },
                "AdaptiveFeedbackEngine": {
                    "status": "HEALTHY",
                    "headline_metric_name": "Rolling S-Score",
                    "headline_metric_value": "+0.45",
                }
            },
            "sla_metrics": {
                "cascade_phase_adaptive": {
                    "p99_ms": 120.5,
                    "sla_breaches": 0,
                }
            }
        }

    def test_format_daily_digest_text(self):
        notifier = AgentEfficiencyNotifier()
        text = notifier.format_daily_digest_text(self.sample_summary)
        self.assertIn("AUTONOMOUS FLEET EFFICIENCY DAILY DIGEST", text)
        self.assertIn("Fleet Health:       HEALTHY", text)
        self.assertIn("Rolling Score (S):  +0.45", text)
        self.assertIn("$420.50 saved", text)
        self.assertIn("AdaptivePnLAgent", text)

    def test_format_daily_digest_html(self):
        notifier = AgentEfficiencyNotifier()
        html = notifier.format_daily_digest_html(self.sample_summary)
        self.assertIn("Autonomous Fleet Efficiency Digest", html)
        self.assertIn("+0.45", html)
        self.assertIn("$420.50", html)
        self.assertIn("AdaptivePnLAgent", html)

    def test_format_alert_payload(self):
        alert = {
            "severity": "CRITICAL",
            "agent": "AdaptivePnLAgent",
            "message": "Cascade adaptive phase p99 latency exceeds 500ms.",
            "metric": "p99_latency_ms",
            "value": 650.0,
            "timestamp": "2026-11-21T11:00:00Z"
        }
        payload = AgentEfficiencyNotifier.format_alert_payload(alert)
        self.assertIn("🚨 *[AGENT ALERT - CRITICAL]* `AdaptivePnLAgent`", payload["text"])
        self.assertEqual(payload["severity"], "CRITICAL")
        self.assertEqual(payload["metric"], "p99_latency_ms")

    def test_dispatch_webhook_no_url(self):
        notifier = AgentEfficiencyNotifier(webhook_url=None)
        res = notifier.dispatch_webhook({"test": "data"})
        self.assertFalse(res)

    @patch("urllib.request.urlopen")
    def test_dispatch_webhook_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        notifier = AgentEfficiencyNotifier(webhook_url="https://hooks.example.com/alerts")
        res = notifier.dispatch_webhook({"test": "data"})
        self.assertTrue(res)

    @patch("urllib.request.urlopen", side_effect=Exception("network error"))
    def test_dispatch_webhook_failure(self, mock_urlopen):
        notifier = AgentEfficiencyNotifier(webhook_url="https://hooks.example.com/alerts")
        res = notifier.dispatch_webhook({"test": "data"})
        self.assertFalse(res)


if __name__ == "__main__":
    unittest.main()
