import os
import json
import logging
import asyncio
import re
import io
from datetime import datetime, date
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from playwright.async_api import async_playwright
from aiohttp import web
from PIL import Image

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get configuration from environment (Auto-detects Hugging Face Space Port)
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
PORT = int(os.getenv('PORT', 7860))  # Changed default to 7860 for HF Spaces

# Screenshot settings
SCREENSHOT_WIDTH = 1240
SCREENSHOT_HEIGHT = 649
SCREENSHOT_TIMEOUT = 60
SCREENSHOT_MAX_RETRIES = 2

# Global instances
browser = None
browser_context = None
playwright_instance = None
application = None

# =====================================================
# === ACCESS CONTROL SYSTEM ===
# =====================================================

authorized_users = {}
admin_ids =[]
contact_username = "admin"

def load_users():
    """Load authorized users from users.json file."""
    global authorized_users, admin_ids, contact_username
    
    possible_paths =[
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.json'),
        '/app/users.json',
        'users.json'
    ]
    
    for filepath in possible_paths:
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                authorized_users = data.get('authorized_users', {})
                admin_ids = data.get('admin_ids',[])
                contact_username = data.get('contact_username', 'admin')
                logger.info(f"Loaded {len(authorized_users)} authorized users from {filepath}")
                return True
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.error(f"Error loading users from {filepath}: {e}")
            continue
            
    logger.error("users.json not found! No users will be authorized.")
    return False

def is_user_authorized(user_id: int) -> dict:
    user_id_str = str(user_id)
    if user_id_str not in authorized_users:
        return {'authorized': False, 'reason': 'not_registered', 'info': None}
        
    user_info = authorized_users[user_id_str]
    expiry_str = user_info.get('expiry', '2000-01-01')
    
    try:
        expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
    except ValueError:
        return {'authorized': False, 'reason': 'invalid_expiry', 'info': user_info}
        
    today = date.today()
    if today > expiry_date:
        return {
            'authorized': False, 'reason': 'expired', 'info': user_info,
            'expiry_date': expiry_str, 'days_expired': (today - expiry_date).days
        }
        
    return {
        'authorized': True, 'reason': 'active', 'info': user_info,
        'expiry_date': expiry_str, 'days_remaining': (expiry_date - today).days
    }

def is_admin(user_id: int) -> bool:
    return user_id in admin_ids

def get_denial_message(auth_result: dict) -> str:
    reason = auth_result.get('reason', 'unknown')
    if reason == 'not_registered':
        return f"🚫 *Access Denied*\n\nYou don't have access to this bot.\n\nThis is a premium bot available to paid subscribers only.\n\n📞 Contact @{contact_username} to get access.\n\n💎 *Plans Available:*\n• Monthly subscription\n• Lifetime access\n\nSend your payment and get instant activation!"
    elif reason == 'expired':
        expiry = auth_result.get('expiry_date', 'Unknown')
        days = auth_result.get('days_expired', 0)
        username = auth_result.get('info', {}).get('username', 'User')
        return f"⚠️ *Subscription Expired*\n\nHey @{username}, your subscription expired on *{expiry}* ({days} day{'s' if days != 1 else ''} ago).\n\n📞 Contact @{contact_username} to renew your access.\n\nRenew now to continue using the bot! 🚀"
    elif reason == 'invalid_expiry':
        return f"⚠️ *Account Error*\n\nThere's an issue with your account configuration.\n\n📞 Please contact @{contact_username} to fix this."
    else:
        return f"🚫 *Access Denied*\n\n📞 Contact @{contact_username} for access."

load_users()

# =====================================================
# === BROWSER HELPERS & INIT ===
# =====================================================

async def init_browser():
    """Initialize browser on startup."""
    global browser, browser_context, playwright_instance
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Initializing browser (attempt {attempt + 1}/{max_retries})...")
            if not playwright_instance:
                playwright_instance = await async_playwright().start()
                
            browser = await playwright_instance.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-software-rasterizer',
                    '--disable-extensions',
                    '--disable-blink-features=AutomationControlled',
                    '--force-color-profile=srgb',  # Better colors
                    '--disable-lcd-text'          # Better text AA for crops
                ]
            )
            
            # device_scale_factor=2 boosts ALL screenshots (Flipkart & Amazon Fallback) to high quality!
            browser_context = await browser.new_context(
                viewport={'width': SCREENSHOT_WIDTH, 'height': SCREENSHOT_HEIGHT + 250},
                device_scale_factor=2, 
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
                ignore_https_errors=True
            )
            
            # Quick Test
            test_page = await browser_context.new_page()
            await test_page.goto('about:blank', timeout=5000)
            await test_page.close()
            
            logger.info("✅ Browser initialized successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            if browser_context:
                try: await browser_context.close()
                except: pass
                browser_context = None
            if browser:
                try: await browser.close()
                except: pass
                browser = None
                
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                return False
    return False

async def close_browser():
    global browser, browser_context, playwright_instance
    if browser_context:
        try: await browser_context.close()
        except: pass
    if browser:
        try: await browser.close()
        except: pass
    if playwright_instance:
        try: await playwright_instance.stop()
        except: pass

def extract_urls(text):
    url_pattern = r'https?://[^\s<>"{}|\^`[]]+'
    return re.findall(url_pattern, text)

def get_url_type(url):
    url_lower = url.lower()
    if any(domain in url_lower for domain in['fkrt.cc', 'fkrt.to', 'fkrt.site', 'fkrt.co']):
        return 'flipkart'
    elif 'amzn.to' in url_lower or 'amazon.in' in url_lower:
        return 'amazon'
    return 'default'

# =====================================================
# === SCREENSHOT ENGINE (AOD + FALLBACK) ===
# =====================================================

async def capture_screenshot(url, timeout=SCREENSHOT_TIMEOUT, max_retries=SCREENSHOT_MAX_RETRIES):
    global browser_context
    if not browser_context:
        return None
        
    url_type = get_url_type(url)
    logger.info(f"URL type detected: {url_type} for {url}")
    
    for attempt in range(max_retries):
        
        # ----------------------------------------------------
        # 🚀 PRIMARY LOGIC: AMAZON AOD (HIGH QUALITY CROP)
        # ----------------------------------------------------
        if url_type == 'amazon':
            logger.info("Attempting primary AOD screenshot logic for Amazon...")
            aod_page = None
            try:
                aod_page = await browser_context.new_page()
                await aod_page.set_viewport_size({"width": 1920, "height": 1080})
                
                # 1. Resolve redirect if short domain
                resolved_url = url
                if any(sd in url for sd in["amzn.in", "amzn.to", "a.co", "amzn.eu", "amzn.asia"]):
                    await aod_page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    resolved_url = aod_page.url
                    
                # 2. Inject aod=1
                p = urlparse(resolved_url)
                qs = parse_qs(p.query, keep_blank_values=True)
                if "aod" not in qs or qs["aod"] != ["1"]:
                    qs["aod"] = ["1"]
                    new_query = urlencode(qs, doseq=True)
                    netloc = p.netloc.lower()
                    if "amazon.in" in netloc and not netloc.startswith("www."):
                        netloc = "www.amazon.in"
                    aod_url = urlunparse(p._replace(query=new_query, netloc=netloc))
                else:
                    aod_url = resolved_url
                    
                # 3. Goto AOD URL
                await aod_page.goto(aod_url, wait_until="domcontentloaded", timeout=15000)
                
                # 4. Dismiss Popups
                DISMISS_SELECTORS = ["#sp-cc-accept", 'input[data-action-type="DISMISS"]', "#attach-close_sideSheet-link", ".a-modal-close a"]
                for sel in DISMISS_SELECTORS:
                    try:
                        el = await aod_page.query_selector(sel)
                        if el and await el.is_visible():
                            await el.click(timeout=1500)
                    except: pass
                        
                # 5. Wait for AOD panel & scroll into view
                AOD_SELECTORS =["#aod-pinned-offer", "#aod-offer-list", "#all-offers-display", "#aod-container", "#aod-price-0"]
                aod_found = False
                for sel in AOD_SELECTORS:
                    try:
                        await aod_page.wait_for_selector(sel, state="visible", timeout=12000)
                        aod_found = True
                        break
                    except: pass
                        
                if not aod_found:
                    raise Exception("AOD panel selectors not found on page.")
                    
                for sel in AOD_SELECTORS:
                    try:
                        el = await aod_page.query_selector(sel)
                        if el: await el.scroll_into_view_if_needed(timeout=2000)
                    except: pass
                
                # 6. Capture full raw PNG for PIL cropping (Maximum compression & quality)
                raw_png = await aod_page.screenshot(type="png", full_page=False)
                
                # PIL Cropping Engine
                img = Image.open(io.BytesIO(raw_png))
                s = img.width / 1920.0  # Dynamic scale calculation (Should be 2.0 based on context)
                
                CROP_W = 576
                CROP_H = 239
                left = max(img.width - CROP_W * s, 0)
                upper = 0
                right = img.width
                lower = min(CROP_H * s, img.height)
                
                cropped = img.crop((left, upper, right, lower))
                cropped = cropped.resize((CROP_W, int(CROP_H)), Image.LANCZOS)
                
                # Canvas pad if smaller
                if cropped.size != (CROP_W, CROP_H):
                    canvas = Image.new("RGB", (CROP_W, CROP_H), (255, 255, 255))
                    canvas.paste(cropped, (CROP_W - cropped.width, 0))
                    cropped = canvas
                    
                out_bytes = io.BytesIO()
                cropped.save(out_bytes, format="PNG", optimize=True)
                logger.info("✅ Successfully captured primary AOD screenshot.")
                
                await aod_page.close()
                return out_bytes.getvalue()
                
            except Exception as e:
                logger.warning(f"⚠️ Primary AOD logic failed: {str(e)}. Falling back to default Amazon mode.")
                if aod_page:
                    try: await aod_page.close()
                    except: pass

        # ----------------------------------------------------
        # ⚠️ FALLBACK / REGULAR LOGIC
        # ----------------------------------------------------
        page = None
        try:
            page = await browser_context.new_page()
            await page.set_extra_http_headers({
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
            })
            
            async def route_handler(route):
                if route.request.resource_type in ["font", "media"]:
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", route_handler)
            
            page_loaded = False
            strategies = ['commit', 'domcontentloaded', 'load']
            for strategy in strategies:
                try:
                    await page.goto(url, wait_until=strategy, timeout=timeout * 1000)
                    page_loaded = True
                    break
                except Exception as e:
                    if strategy == strategies[-1]: raise
                    continue
                    
            if not page_loaded:
                raise Exception("All loading strategies failed")
                
            await page.wait_for_timeout(2000)
            await page.unroute("**/*")
            await page.wait_for_timeout(1500)
            
            if url_type == 'default':
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                    await page.wait_for_timeout(1000)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(500)
                except: pass
            elif url_type == 'amazon':
                try:
                    await page.evaluate("window.scrollTo(0, 300)")
                    await page.wait_for_timeout(1500)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(800)
                except: pass
                
            # Perform fallback crops
            if url_type == 'flipkart':
                screenshot_bytes = await page.screenshot(
                    full_page=False, type='jpeg', quality=85, animations='disabled',
                    clip={'x': 0, 'y': 100, 'width': SCREENSHOT_WIDTH, 'height': 540}
                )
            elif url_type == 'amazon':
                screenshot_bytes = await page.screenshot(
                    full_page=False, type='jpeg', quality=85, animations='disabled',
                    clip={'x': 0, 'y': 250, 'width': SCREENSHOT_WIDTH, 'height': SCREENSHOT_HEIGHT}
                )
            else:
                screenshot_bytes = await page.screenshot(
                    full_page=False, type='jpeg', quality=85, animations='disabled'
                )
                
            logger.info(f"Fallback/Default screenshot captured for: {url}")
            return screenshot_bytes
            
        except Exception as e:
            logger.error(f"Screenshot attempt {attempt + 1}/{max_retries} failed for {url}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
            else:
                return None
        finally:
            if page:
                try: await page.close()
                except: pass

    return None

# =====================================================
# === BOT COMMAND HANDLERS ===
# =====================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    auth = is_user_authorized(user_id)
    if not auth['authorized']:
        await update.message.reply_text(get_denial_message(auth), parse_mode='Markdown')
        return

    days_info = ""
    plan = auth.get('info', {}).get('plan', 'unknown')
    days = auth.get('days_remaining')
    if plan == 'lifetime': days_info = "♾️ Lifetime Access"
    elif days is not None and days <= 7: days_info = f"⚠️ {days} days remaining!"
    else: days_info = f"⏳ {days} days remaining"

    await update.message.reply_text(
        f"✅ *Bot is Active!*\n📊 *Your Status:* {days_info}\n\n"
        "👋 Welcome! Send me any message containing URLs, and I'll send you screenshots!\n\n"
        "📌 *Features:*\n• Extract links from any message\n• Works with forwarded messages\n"
        "• High-quality AOD Amazon screenshots\n• Smart cropping for Flipkart\n\n"
        "Just send or forward any message with links! 🚀",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    auth = is_user_authorized(user_id)
    if not auth['authorized']:
        await update.message.reply_text(get_denial_message(auth), parse_mode='Markdown')
        return

    if not browser or not browser_context:
        await update.message.reply_text("⚠️ Screenshot service is initializing. Please try again in a moment.")
        return

    message_text = update.message.text or update.message.caption or ""
    urls = extract_urls(message_text)
    
    if not urls:
        await update.message.reply_text("❌ No links found in your message!\nPlease send a message containing URLs.")
        return

    confirm_msg = await update.message.reply_text(
        f"🔍 Found {len(urls)} link(s)!\n📸 Generating screenshot{'s' if len(urls) > 1 else ''}...\n"
        f"⏳ This may take 15-30 seconds for heavy websites..."
    )

    successful = 0
    failed = 0

    for idx, url in enumerate(urls, 1):
        try:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            
            if len(urls) > 1:
                try:
                    await confirm_msg.edit_text(f"⏳ Processing link {idx}/{len(urls)}...\n🔗 {url[:50]}{'...' if len(url) > 50 else ''}")
                except: pass

            screenshot_bytes = await capture_screenshot(url)
            
            if not screenshot_bytes:
                failed += 1
                await update.message.reply_text(f"❌ Failed to capture screenshot for:\n{url}\n⚠️ The page might be too slow or blocked.")
                continue

            caption = message_text[:1024] if len(urls) == 1 else f"📸 Screenshot {idx}/{len(urls)}\n🔗 {url[:900]}"
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
            await update.message.reply_photo(photo=io.BytesIO(screenshot_bytes), caption=caption)
            successful += 1
            
            if idx < len(urls): await asyncio.sleep(1)
                
        except Exception as e:
            failed += 1
            logger.error(f"Error processing URL {idx}/{len(urls)}: {e}")
            await update.message.reply_text(f"❌ Error processing link:\n{url}")

    try: await confirm_msg.delete()
    except: pass
    
    summary = f"✅ Completed!\n\n📊 Results:\n✅ Successful: {successful}\n"
    if failed > 0: summary += f"❌ Failed: {failed}\n"
    await update.message.reply_text(summary)

# =====================================================
# === WEB & WEBHOOK HANDLERS ===
# =====================================================

async def health_check(request):
    status = {
        "bot": "initialized" if application else "not_initialized",
        "browser": "ready" if (browser and browser_context) else "not_ready",
        "authorized_users": len(authorized_users)
    }
    if application and browser and browser_context:
        return web.Response(text=f"OK - Bot Active, Browser Ready. Users: {len(authorized_users)}", status=200)
    return web.Response(text=f"Starting - {status}", status=200)

async def webhook_handler(request):
    if not application: return web.Response(text="Service starting, please retry", status=503)
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return web.Response(status=500)

async def startup(app):
    global application
    if not BOT_TOKEN: raise ValueError("TELEGRAM_BOT_TOKEN not set")
    
    logger.info("Step 1: Initializing browser...")
    await init_browser()
    
    logger.info("Step 2: Creating Telegram application...")
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_message))
    
    logger.info("Step 3: Initializing Telegram application...")
    await application.initialize()
    await application.start()
    
    # Auto-detect webhook URL in HF Space
    webhook_url = os.getenv('RENDER_EXTERNAL_URL', '')
    if not webhook_url and os.getenv('SPACE_HOST'):
        webhook_url = f"https://{os.getenv('SPACE_HOST')}"
        
    if webhook_url:
        webhook_full_url = f"{webhook_url}/webhook/{BOT_TOKEN}"
        logger.info(f"Step 4: Setting webhook to {webhook_full_url}")
        await application.bot.set_webhook(url=webhook_full_url, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        print(f"✅ Webhook configured at {webhook_full_url}")
    else:
        logger.warning("No webhook URL set. Ensure RENDER_EXTERNAL_URL is configured.")

async def shutdown(app):
    await close_browser()
    if application:
        await application.stop()
        await application.shutdown()

def create_app():
    if not BOT_TOKEN: raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set!")
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    app.router.add_post(f'/webhook/{BOT_TOKEN}', webhook_handler)
    app.on_startup.append(startup)
    app.on_cleanup.append(shutdown)
    return app

if __name__ == '__main__':
    try:
        app = create_app()
        print(f"🚀 Starting web server on 0.0.0.0:{PORT}")
        web.run_app(app, host='0.0.0.0', port=PORT)
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
