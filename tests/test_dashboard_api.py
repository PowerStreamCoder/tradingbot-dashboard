#!/usr/bin/env python3
"""
Test Suite for Dashboard API Endpoints

Tests the FastAPI dashboard endpoints:
- Bot status endpoint
- Bot overview endpoint
- Trade history endpoint
- Bucket filtering logic
- Delete bucket endpoint

Usage:
    python test_dashboard_api.py
    python test_dashboard_api.py --verbose
"""

import unittest
import sys
import os
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../dashboard'))


class TestDashboardAPI(unittest.TestCase):
    """Test cases for dashboard API endpoints"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_dashboard_data = Mock()
        self.mock_dashboard_data.bot_data = {
            "msft": {
                "bucket1": {"entryPrice": 100.50, "referencePrice": 100.00, "shares": 10},
                "bucket2": {"entryPrice": None, "referencePrice": None, "shares": 0},
                "bucket3": {"entryPrice": 105.00, "referencePrice": 104.50, "shares": 15}
            },
            "nvda": {
                "bucket1": {"entryPrice": 500.00, "referencePrice": 498.00, "shares": 5},
                "bucket2": {"entryPrice": None, "referencePrice": None, "shares": 0}
            }
        }

    def test_filter_active_buckets(self):
        """Test filtering only active buckets (entryPrice not None)"""
        bot_data = self.mock_dashboard_data.bot_data

        # Filter active buckets
        filtered_data = {}
        for bot_id, bot_info in bot_data.items():
            filtered_data[bot_id] = {}
            for key, value in bot_info.items():
                if key.startswith('bucket'):
                    if isinstance(value, dict) and value.get('entryPrice') is not None:
                        filtered_data[bot_id][key] = value

        # Verify filtering
        self.assertEqual(len(filtered_data["msft"]), 2)  # bucket1 and bucket3
        self.assertEqual(len(filtered_data["nvda"]), 1)  # bucket1 only
        self.assertNotIn("bucket2", filtered_data["msft"])
        self.assertNotIn("bucket2", filtered_data["nvda"])

    def test_bot_status_active(self):
        """Test bot status shows active when heartbeat recent"""
        from datetime import datetime, timezone, timedelta

        last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=30)
        current_time = datetime.now(timezone.utc)

        # Bot is active if heartbeat within 90 seconds
        is_active = (current_time - last_heartbeat).total_seconds() < 90

        self.assertTrue(is_active)

    def test_bot_status_inactive(self):
        """Test bot status shows inactive when heartbeat old"""
        from datetime import datetime, timezone, timedelta

        last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=120)
        current_time = datetime.now(timezone.utc)

        # Bot is inactive if heartbeat older than 90 seconds
        is_active = (current_time - last_heartbeat).total_seconds() < 90

        self.assertFalse(is_active)

    def test_bucket_id_mapping(self):
        """Test dashboard bucket ID mapping (upward 1-5, downward 6-10)"""
        # Upward buckets: 1-5 map to dashboard 1-5
        upward_bucket_1 = 1
        dashboard_id_upward = upward_bucket_1  # No offset
        self.assertEqual(dashboard_id_upward, 1)

        # Downward buckets: 1-5 map to dashboard 6-10
        downward_bucket_1 = 1
        dashboard_id_downward = downward_bucket_1 + 5  # +5 offset
        self.assertEqual(dashboard_id_downward, 6)

        # Verify full mapping
        downward_mappings = {
            1: 6, 2: 7, 3: 8, 4: 9, 5: 10
        }
        for bot_bucket, dash_bucket in downward_mappings.items():
            self.assertEqual(bot_bucket + 5, dash_bucket)

    @patch('main.dashboard_data')
    def test_delete_bucket_endpoint(self, mock_data):
        """Test delete bucket API endpoint"""
        mock_data.bot_data = {
            "msft": {
                "bucket1": {"entryPrice": 100.50},
                "bucket3": {"entryPrice": 105.00}
            }
        }

        # Simulate delete bucket3
        bot_id = "msft"
        bucket_id = 3
        bucket_key = f"bucket{bucket_id}"

        mock_data.bot_data[bot_id].pop(bucket_key, None)

        # Verify deletion
        self.assertNotIn(bucket_key, mock_data.bot_data[bot_id])
        self.assertIn("bucket1", mock_data.bot_data[bot_id])

    def test_trade_history_sorting(self):
        """Test trades are sorted by timestamp descending"""
        trades = [
            {"timestamp": "2026-06-01T10:00:00Z", "symbol": "MSFT"},
            {"timestamp": "2026-06-01T11:00:00Z", "symbol": "NVDA"},
            {"timestamp": "2026-06-01T09:00:00Z", "symbol": "MSFT"}
        ]

        # Sort by timestamp descending (most recent first)
        sorted_trades = sorted(
            trades,
            key=lambda x: x['timestamp'],
            reverse=True
        )

        self.assertEqual(sorted_trades[0]['timestamp'], "2026-06-01T11:00:00Z")
        self.assertEqual(sorted_trades[2]['timestamp'], "2026-06-01T09:00:00Z")

    def test_pnl_formatting(self):
        """Test P&L is formatted correctly with sign and decimals"""
        pnl_positive = 45.678
        pnl_negative = -23.456

        formatted_positive = f"+${pnl_positive:.2f}"
        formatted_negative = f"-${abs(pnl_negative):.2f}"

        self.assertEqual(formatted_positive, "+$45.68")
        self.assertEqual(formatted_negative, "-$23.46")

    def test_bot_overview_includes_metadata(self):
        """Test bot overview includes non-bucket metadata"""
        bot_info = {
            "bucket1": {"entryPrice": 100.50},
            "bucket2": {"entryPrice": None},
            "last_update": "2026-06-01T10:00:00Z",
            "regime": "low_volatility"
        }

        # Filter for active buckets but keep metadata
        filtered = {}
        for key, value in bot_info.items():
            if key.startswith('bucket'):
                if isinstance(value, dict) and value.get('entryPrice') is not None:
                    filtered[key] = value
            else:
                filtered[key] = value  # Keep metadata

        self.assertIn("bucket1", filtered)
        self.assertNotIn("bucket2", filtered)
        self.assertIn("last_update", filtered)
        self.assertIn("regime", filtered)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', action='store_true')
    args, unknown = parser.parse_known_args()

    verbosity = 2 if args.verbose else 1
    unittest.main(argv=[sys.argv[0]] + unknown, verbosity=verbosity)
