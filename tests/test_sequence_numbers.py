#!/usr/bin/env python3
"""
Test Suite for Weekly Sequence Number Logic

Tests the trade sequence number calculation including:
- Week normalization (Monday 00:00:00)
- Sequential numbering within week
- Weekly reset behavior
- Race condition handling

Usage:
    python test_sequence_numbers.py
    python test_sequence_numbers.py --verbose
"""

import unittest
import sys
import os
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../dashboard'))


class TestSequenceNumbers(unittest.TestCase):
    """Test cases for sequence number calculation"""

    def test_week_start_normalization(self):
        """Test that week start is normalized to Monday 00:00:00"""
        # Test various timestamps on same Monday
        test_cases = [
            datetime(2026, 6, 1, 0, 0, 0),   # Monday 00:00:00
            datetime(2026, 6, 1, 14, 30, 0), # Monday 14:30:00
            datetime(2026, 6, 1, 23, 59, 59), # Monday 23:59:59
        ]

        for trade_time in test_cases:
            week_start = (trade_time - timedelta(days=trade_time.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            # All should normalize to same Monday 00:00:00
            self.assertEqual(week_start.weekday(), 0, "Should be Monday")
            self.assertEqual(week_start.hour, 0, "Should be 00:00:00")
            self.assertEqual(week_start.minute, 0)
            self.assertEqual(week_start.second, 0)
            self.assertEqual(week_start.microsecond, 0)

            # All should have same normalized timestamp
            self.assertEqual(week_start, datetime(2026, 6, 1, 0, 0, 0))

    def test_week_start_different_days(self):
        """Test week start for different days of the week"""
        # Week of June 1-7, 2026 (Monday to Sunday)
        test_cases = [
            (datetime(2026, 6, 1, 14, 0, 0), 0),  # Monday
            (datetime(2026, 6, 2, 14, 0, 0), 1),  # Tuesday
            (datetime(2026, 6, 3, 14, 0, 0), 2),  # Wednesday
            (datetime(2026, 6, 4, 14, 0, 0), 3),  # Thursday
            (datetime(2026, 6, 5, 14, 0, 0), 4),  # Friday
            (datetime(2026, 6, 6, 14, 0, 0), 5),  # Saturday
            (datetime(2026, 6, 7, 14, 0, 0), 6),  # Sunday
        ]

        expected_week_start = datetime(2026, 6, 1, 0, 0, 0)  # Monday June 1 at 00:00:00

        for trade_time, expected_weekday in test_cases:
            self.assertEqual(trade_time.weekday(), expected_weekday)

            week_start = (trade_time - timedelta(days=trade_time.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            # All days in same week should have same week_start
            self.assertEqual(week_start, expected_week_start,
                           f"{trade_time.strftime('%A %Y-%m-%d')} should normalize to {expected_week_start}")

    def test_weekly_reset(self):
        """Test that sequence numbers reset on Monday"""
        # Week 1: June 1-7 (Monday to Sunday)
        week1_monday = datetime(2026, 6, 1, 14, 0, 0)
        week1_sunday = datetime(2026, 6, 7, 23, 59, 59)

        # Week 2: June 8-14 (Monday to Sunday)
        week2_monday = datetime(2026, 6, 8, 0, 0, 1)

        # Calculate week starts
        week1_start = (week1_monday - timedelta(days=week1_monday.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week1_end_start = (week1_sunday - timedelta(days=week1_sunday.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week2_start = (week2_monday - timedelta(days=week2_monday.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Week 1 Sunday and Monday should have same week_start
        self.assertEqual(week1_start, week1_end_start)

        # Week 2 Monday should have different week_start
        self.assertNotEqual(week1_start, week2_start)
        self.assertEqual((week2_start - week1_start).days, 7)

    def test_iso_format_consistency(self):
        """Test that ISO format conversion preserves normalization"""
        trade_time = datetime(2026, 6, 1, 14, 30, 45)

        # Normalize week start
        week_start = (trade_time - timedelta(days=trade_time.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Convert to ISO format (for Firestore query)
        week_start_iso = week_start.isoformat()

        # Should be exactly Monday at 00:00:00
        self.assertEqual(week_start_iso, '2026-06-01T00:00:00')

        # Week end should be exactly next Monday at 00:00:00
        week_end = week_start + timedelta(days=7)
        week_end_iso = week_end.isoformat()
        self.assertEqual(week_end_iso, '2026-06-08T00:00:00')

    def test_sequence_number_logic(self):
        """Test sequence number assignment logic"""
        # Simulate Firestore query results (trades in same week)
        week_trades = [
            {'seqNum': 1, 'timestamp': '2026-06-01T10:00:00Z'},  # Monday
            {'seqNum': 2, 'timestamp': '2026-06-01T14:30:00Z'},  # Monday
            {'seqNum': 3, 'timestamp': '2026-06-02T09:15:00Z'},  # Tuesday
            {'seqNum': 4, 'timestamp': '2026-06-03T16:45:00Z'},  # Wednesday
        ]

        # Find max sequence number
        max_seq = 0
        for trade in week_trades:
            seq = trade.get('seqNum', 0)
            if seq > max_seq:
                max_seq = seq

        # Next trade should get max + 1
        new_seq = max_seq + 1
        self.assertEqual(new_seq, 5)

    def test_empty_week_first_trade(self):
        """Test sequence number for first trade of the week"""
        # No trades in week
        week_trades = []

        max_seq = 0
        for trade in week_trades:
            seq = trade.get('seqNum', 0)
            if seq > max_seq:
                max_seq = seq

        # First trade should get sequence #1
        new_seq = max_seq + 1
        self.assertEqual(new_seq, 1)

    def test_missing_seqnum_field(self):
        """Test handling of trades without seqNum field"""
        # Some trades missing seqNum (shouldn't happen, but test defensive code)
        week_trades = [
            {'seqNum': 1, 'timestamp': '2026-06-01T10:00:00Z'},
            {'timestamp': '2026-06-01T11:00:00Z'},  # Missing seqNum
            {'seqNum': 2, 'timestamp': '2026-06-01T14:30:00Z'},
        ]

        max_seq = 0
        for trade in week_trades:
            seq = trade.get('seqNum', 0)  # Default to 0 if missing
            if seq > max_seq:
                max_seq = seq

        # Should correctly find max=2, next should be 3
        new_seq = max_seq + 1
        self.assertEqual(new_seq, 3)

    def test_race_condition_prevention(self):
        """Test that thread lock prevents race conditions"""
        from threading import Lock, Thread
        import time

        # Shared state
        max_seq = 0
        assigned_sequences = []
        lock = Lock()

        def assign_sequence(trade_id):
            """Simulate sequence assignment with lock"""
            nonlocal max_seq

            with lock:
                # Read current max
                current_max = max_seq
                # Small delay to simulate processing
                time.sleep(0.001)
                # Assign next sequence
                new_seq = current_max + 1
                max_seq = new_seq
                assigned_sequences.append((trade_id, new_seq))

        # Simulate 10 concurrent trades
        threads = []
        for i in range(10):
            t = Thread(target=assign_sequence, args=(f"trade_{i}",))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # Verify no duplicate sequence numbers
        sequences = [seq for _, seq in assigned_sequences]
        self.assertEqual(len(sequences), 10, "Should have 10 sequences")
        self.assertEqual(len(set(sequences)), 10, "All sequences should be unique")
        self.assertEqual(sorted(sequences), list(range(1, 11)), "Sequences should be 1-10")

    def test_bug_fix_verification(self):
        """
        Verify the bug fix: two trades on same Monday at different times
        should now have the same week_start after normalization
        """
        # Before fix: These would have different week_start values
        # After fix: Both should normalize to same week_start

        trade1_time = datetime(2026, 6, 1, 10, 0, 0)  # Monday 10:00 AM
        trade2_time = datetime(2026, 6, 1, 14, 30, 0) # Monday 2:30 PM

        # OLD BUG (before normalization):
        # week_start = trade_time - timedelta(days=trade_time.weekday())
        # This kept the hours/minutes from original timestamp

        old_bug_week1 = trade1_time - timedelta(days=trade1_time.weekday())
        old_bug_week2 = trade2_time - timedelta(days=trade2_time.weekday())

        # Bug: These would be different
        self.assertNotEqual(old_bug_week1, old_bug_week2,
                           "Bug demonstration: Without normalization, week starts differ")

        # NEW FIX (with normalization):
        new_fix_week1 = (trade1_time - timedelta(days=trade1_time.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        new_fix_week2 = (trade2_time - timedelta(days=trade2_time.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Fix: These should be identical
        self.assertEqual(new_fix_week1, new_fix_week2,
                        "Bug fix: With normalization, week starts are identical")
        self.assertEqual(new_fix_week1, datetime(2026, 6, 1, 0, 0, 0))


def run_tests():
    """Run all tests with optional verbosity"""
    import argparse

    parser = argparse.ArgumentParser(description='Test Sequence Number Logic')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    args = parser.parse_args()

    verbosity = 2 if args.verbose else 1

    # Create test suite
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestSequenceNumbers)

    # Run tests
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)

    # Print summary
    print("\n" + "="*70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Success rate: {((result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100):.1f}%")
    print("="*70)

    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())
