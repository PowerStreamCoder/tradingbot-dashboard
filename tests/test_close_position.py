#!/usr/bin/env python3
"""
Test the close position API endpoint with correct parameters.
"""
import requests
import json

# Test data - matches the expected backend format
test_payload = {
    "bot_id": 1,
    "symbol": "NVDA",
    "bucket_group": "upward",
    "bucket_id": 0,
    "close_covered_calls": True
}

print("Testing /api/close-position endpoint...")
print(f"Payload: {json.dumps(test_payload, indent=2)}")

try:
    response = requests.post(
        'http://localhost:8080/api/close-position',
        json=test_payload,
        timeout=5
    )

    print(f"\nStatus Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")

    if response.status_code == 200:
        print("\n✅ API call successful!")
    else:
        print("\n❌ API call failed!")

except requests.exceptions.ConnectionError:
    print("\n⚠️  Server not running at localhost:8000")
except Exception as e:
    print(f"\n❌ Error: {e}")
