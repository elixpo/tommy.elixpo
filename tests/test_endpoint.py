#!/usr/bin/env python3

import requests
import json

url = "http://localhost:8000/v1/chat/completions"

payload = {
    "messages": [
        {
            "role": "user",
            "content": "what are the latest issues that we have for pollinations on github?"
        }
    ],
    "user_name": "http_test_user",
    "is_admin": False
}

print("Sending request to:", url)

print("Payload:", json.dumps(payload, indent=2))
print("\n" + "="*50 + "\n")


try:
    response = requests.post(url, json=payload, timeout=300)
    response.raise_for_status()
    
    result = response.json()
    print("Response:")
    print(json.dumps(result, indent=2))
    
except requests.exceptions.ConnectionError:
    print("Error: Could not connect to server. Make sure http_bot.py is running on localhost:8000")
except requests.exceptions.Timeout:
    print("Error: Request timed out")
except Exception as e:
    print(f"Error: {e}")

