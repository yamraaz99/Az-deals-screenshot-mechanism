#!/usr/bin/env python3
"""Clear Telegram webhook and enable polling mode."""

import os
import requests

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not BOT_TOKEN:
    print("❌ ERROR: TELEGRAM_BOT_TOKEN not set!")
    exit(1)

print("=" * 60)
print("Clearing Telegram Webhook")
print("=" * 60)

# Delete webhook
url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
params = {"drop_pending_updates": True}

try:
    response = requests.post(url, params=params, timeout=10)
    result = response.json()
    
    if result.get("ok"):
        print("✅ Webhook cleared successfully!")
        print(f"📝 Response: {result.get('description', 'No description')}")
    else:
        print(f"❌ Failed to clear webhook: {result}")
        
except Exception as e:
    print(f"❌ Error: {e}")

print("=" * 60)
print("\n💡 Now you can safely use polling mode!")
print("   Run: python bot.py")
print("=" * 60)
