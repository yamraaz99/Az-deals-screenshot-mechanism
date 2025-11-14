#!/usr/bin/env python3
"""Test script to verify Playwright browser installation."""

import asyncio
from playwright.async_api import async_playwright

async def test_browser():
    """Test if browser can be launched and take a screenshot."""
    print("🔍 Testing Playwright browser installation...")
    
    try:
        print("📦 Starting Playwright...")
        playwright = await async_playwright().start()
        
        print("🌐 Launching Chromium browser...")
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer',
                '--disable-extensions',
                '--no-first-run',
                '--no-zygote',
                '--single-process'
            ]
        )
        
        print("📄 Creating browser context...")
        context = await browser.new_context(
            viewport={'width': 1240, 'height': 649}
        )
        
        print("🔗 Opening test page...")
        page = await context.new_page()
        await page.goto('https://example.com', timeout=30000)
        
        print("📸 Taking screenshot...")
        screenshot = await page.screenshot(type='jpeg', quality=85)
        
        print(f"✅ Screenshot captured! Size: {len(screenshot)} bytes")
        
        await page.close()
        await context.close()
        await browser.close()
        await playwright.stop()
        
        print("\n✅ ✅ ✅ Browser test PASSED! ✅ ✅ ✅")
        print("Your Playwright installation is working correctly!")
        return True
        
    except Exception as e:
        print(f"\n❌ ❌ ❌ Browser test FAILED! ❌ ❌ ❌")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    result = asyncio.run(test_browser())
    exit(0 if result else 1)
