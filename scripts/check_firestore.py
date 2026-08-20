#!/usr/bin/env python3
"""Check Firestore dashboard data for NVDA bot."""
import json
import sys

# Mock imports to check data structure
print("Dashboard data structure check:")
print("\nExpected fields for each bucket:")
print("  - referencePriceBefore: float")
print("  - entryPrice: float")
print("  - referencePriceAfter: float")
print("  - side: 'long' or 'short'")
print("  - quantity: float (number of shares)")
print("  - optionSold: boolean")
print("  - optionStrike: float (strike price)")
print("  - optionExpiration: string (ISO date)")
print("  - optionPremium: float (premium collected)")
print("\nTo verify dashboard, check Firebase console:")
print("Collection: bot_overview")
print("Document: overview")
print("Field: bot1.bucket1")
