#!/usr/bin/env python3
"""
Test script to check bot-overview API data structure
"""
import requests
import json

# Try production URL
try:
    response = requests.get('https://trading-dashboard-w2n5czslna-uc.a.run.app/api/bot-overview')
    data = response.json()

    print("=== BOT OVERVIEW DATA ===")
    print(json.dumps(data, indent=2))

    # Check bucket structure
    for bot_id, bot_data in data.items():
        print(f"\n=== BOT {bot_id} ===")
        for key, value in bot_data.items():
            if key.startswith('bucket') and isinstance(value, dict):
                print(f"\n{key}:")
                print(f"  entryPrice: {value.get('entryPrice')}")
                print(f"  quantity: {value.get('quantity')}")
                print(f"  currentPrice: {value.get('currentPrice')}")
                print(f"  stopLossPrice: {value.get('stopLossPrice')}")
                print(f"  profitTargetPrice: {value.get('profitTargetPrice')}")
                print(f"  unrealizedPnL: {value.get('unrealizedPnL')}")
                print(f"  optionSold: {value.get('optionSold')}")

except Exception as e:
    print(f"Error: {e}")
