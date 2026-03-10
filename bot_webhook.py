import os
import json
import logging
import asyncio
import re
from io import BytesIO
from datetime import datetime, date
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from playwright.async_api import async_playwright
from aiohttp import web

# ==========================================
# Configure logging
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# Get configuration from environment
# ==========================================
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
PORT = int(os.getenv('PORT', 10000))

# Screenshot settings
SCREENSHOT_WIDTH = 1240
SCREENSHOT_HEIGHT = 649
SCREENSHOT_TIMEOUT = 45
SCREENSHOT_MAX_RETRIES = 2

# AMAZON AOD CONFIGURATION
AOD_VIEWPORT_W = 1920
AOD_VIEWPORT_H = 1080
AOD_CROP_W = 576
AOD_CROP_H = 239
AOD_DEVICE_SCALE = 2

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
AMAZON_SHORT_DOMAINS = frozenset({"amzn.in", "amzn.to", "a.co", "amzn.eu", "amzn.asia"})
AOD_SELECTORS =[
    "#aod-pinned-offer",
    "#aod-offer-list",
    "#all-offers-display",
    "#aod-container",
    "#aod-price-0",
]
AOD_DISMISS_SELECTORS = [
    "#sp-cc-accept",
    'input[data-action-type="DISMISS"]',
    "#attach-close_sideSheet-link",
    ".a-modal-close a",
]

# Global instances
browser = None
browser_context = None
playwright_instance = None
application = None

# ==========================================
# ACCESS CONTROL SYSTEM
# ==========================================
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
        days_expired = (today - expiry_date).days
        return {'authorized': False, 'reason': 'expired', 'info': user_info, 'expiry_date': expiry_str, 'days_expired': days_expired}
    
    days_remaining = (expiry_date - today).days
    return {'authorized': True, 'reason': 'active', 'info': user_info, 'expiry_date': expiry_str, 'days_remaining': days_remaining}

def is_admin(user_id: int) -> bool:
    return user_id in admin_ids

def get_denial_message(auth_result: dict) -> str:
    reason = auth_result.get('reason', 'unknown')
    if reason == 'not_registered':
        return (
            "⚠️ *Access Denied*\n\n"
            "You don't have access to this bot.\n\n"
            "This is a premium bot available to paid subscribers only.\n\n"
            f"💬 Contact @{contact_username} to get access.\n\n"
            "💎 *Plans Available:*\n"
            "• Monthly subscription\n• Lifetime access\n\n"
            "Send your payment and get instant activation!"
        )
    elif reason == 'expired':
        return (
            "⏳ *Subscription Expired*\n\n"
            f"Hey, your subscription expired {auth_result.get('days_expired', 0)} days ago.\n\n"
            f"💬 Contact @{contact_username} to renew your access."
        )
    return f"⚠️ *Access Denied*\n\n💬 Contact @{contact_username} for access."

load_users()

# ==========================================
# AMAZON AOD URL UTILITIES
# ==========================================
def _is_amazon_short_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host in AMAZON_SHORT_DOMAINS

def _build_aod_url(url: str) -> str:
    p = urlparse(url)
    host = p.netloc.lower()
    if "amazon.in" in host and not host.startswith("www."):
        p = p._replace(netloc="www.amazon.in")
    qs = parse_qs(p.query, keep_blank_values=True)
    qs["aod"] = ["1"]
    return urlunparse(p._replace(query=urlencode(qs, doseq=True)))

# ==========================================
# AMAZON AOD ASYNC HELPERS
# ==========================================
async def _dismiss_amazon_popups(page) -> None:
    for sel in AOD_DISMISS_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click(timeout=1000)
        except Exception:
            pass

async def _wait_for_aod_panel(page) -> bool:
    combined_sel = ", ".join(AOD_SELECTORS)
    try:
        await page.wait_for_selector(combined_sel, state="visible", timeout=12000)
        return True
    except Exception:
        return False

# ==========================================
# AMAZON AOD CAPTURE (NATIVE PLAYWRIGHT CLIP)
# ==========================================
async def capture_amazon_aod_screenshot(url: str, timeout: int = SCREENSHOT_TIMEOUT) -> bytes | None:
    global browser
    if not browser: return None
        
    aod_context = None
    page = None
    
    try:
        aod_context = await browser.new_context(
            viewport={"width": AOD_VIEWPORT_W, "height": AOD_VIEWPORT_H},
            device_scale_factor=AOD_DEVICE_SCALE,
            user_agent=DESKTOP_UA,
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        
        # Abort heavy rendering requests to save Render RAM
        async def route_handler(route):
            if route.request.resource_type in["font", "media", "websocket"]:
                await route.abort()
            else:
                await route.continue_()
                
        await aod_context.route("**/*", route_handler)
        page = await aod_context.new_page()
        
        working_url = url
        if _is_amazon_short_url(url):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                working_url = page.url
            except Exception: pass
                
        aod_url = _build_aod_url(working_url)
        logger.info(f"Amazon AOD: Navigating to {aod_url}")
        
        # Removed 'networkidle', relying on domcontentloaded + manual timeout
        await page.goto(aod_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            
        aod_found = await _wait_for_aod_panel(page)
        if not aod_found:
            logger.warning("Amazon AOD: Panel not found — aborting")
            return None
            
        # Give JS time to populate prices
        await page.wait_for_timeout(3500)
        await _dismiss_amazon_popups(page)
        
        # NATIVE CROP: Replaces all the PIL logic! X coordinates = 1920 - 576 = 1344
        clip_rect = {
            "x": AOD_VIEWPORT_W - AOD_CROP_W, 
            "y": 0,
            "width": AOD_CROP_W,
            "height": AOD_CROP_H
        }
        
        raw_img = await page.screenshot(type="jpeg", quality=90, clip=clip_rect)
        logger.info(f"✅ Amazon AOD natively captured!")
        return raw_img
        
    except Exception as e:
        logger.error(f"Amazon AOD Capture failed: {e}")
        return None
    finally:
        if page:
            try: await page.close()
            except: pass
        if aod_context:
            try: await aod_context.close()
            except: pass

# ==========================================
# BROWSER INIT / CLOSE
# ==========================================
async def init_browser():
    global browser, browser_context, playwright_instance
    try:
        if not playwright_instance:
            playwright_instance = await async_playwright().start()

        browser = await playwright_instance.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-extensions',
                '--disable-blink-features=AutomationControlled',
                '--no-zygote',
                '--single-process' # Massively saves RAM
            ]
        )

        browser_context = await browser.new_context(
            viewport={'width': SCREENSHOT_WIDTH, 'height': SCREENSHOT_HEIGHT},
            user_agent=DESKTOP_UA
        )

        logger.info("✅ Browser initialized successfully!")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize browser: {e}")
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
    if not text: return[]
    return re.findall(r'https?://[^\s<>"{}|\^`\[\]]+', text)

def get_url_type(url):
    url_lower = url.lower()
    if any(d in url_lower for d in['fkrt.cc', 'fkrt.to', 'fkrt.site', 'fkrt.co', 'flipkart']): return 'flipkart'
    if any(d in url_lower for d in['amazon', 'amzn.to', 'a.co', 'amzn.eu', 'amzn.in']): return 'amazon'
    return 'default'

# ==========================================
# MAIN SCREENSHOT CAPTURE 
# ==========================================
async def capture_screenshot(url, timeout=SCREENSHOT_TIMEOUT, max_retries=SCREENSHOT_MAX_RETRIES):
    global browser_context
    if not browser_context: return None

    url_type = get_url_type(url)
    
    # 1. Try AOD
    if url_type == 'amazon':
        aod_bytes = await capture_amazon_aod_screenshot(url, timeout)
        if aod_bytes: return aod_bytes
        logger.info("⚠️ AOD failed, falling back to standard Amazon capture")

    # 2. Standard Capture
    for attempt in range(max_retries):
        page = None
        try:
            page = await browser_context.new_page()
            
            async def block_media(route):
                if route.request.resource_type in ["font", "media"]: await route.abort()
                else: await route.continue_()
            await page.route("**/*", block_media)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception: pass
            
            await page.wait_for_timeout(2500)
            
            if url_type == 'amazon':
                await page.evaluate("window.scrollTo(0, 300)")
                await page.wait_for_timeout(1000)
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(500)

            # Native Cropping instead of PIL
            if url_type == 'flipkart':
                screenshot_bytes = await page.screenshot(
                    type='jpeg', quality=85, clip={'x': 0, 'y': 100, 'width': SCREENSHOT_WIDTH, 'height': 540}
                )
            elif url_type == 'amazon':
                screenshot_bytes = await page.screenshot(
                    type='jpeg', quality=85, clip={'x': 0, 'y': 250, 'width': SCREENSHOT_WIDTH, 'height': SCREENSHOT_HEIGHT}
                )
            else:
                screenshot_bytes = await page.screenshot(type='jpeg', quality=85)

            return screenshot_bytes

        except Exception as e:
            logger.error(f"Screenshot attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(2)
        finally:
            if page:
                try: await page.close()
                except: pass

    return None

# ==========================================
# COMMAND HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = is_user_authorized(update.effective_user.id)
    if not auth['authorized']:
        await update.message.reply_text(get_denial_message(auth), parse_mode='Markdown')
        return

    days_info = f"✅ {auth.get('days_remaining', 0)} days remaining" if auth.get('info', {}).get('plan') != 'lifetime' else "💎 Lifetime Access"

    await update.message.reply_text(
        f"✅ *Bot is Active!*\n📊 *Your Status:* {days_info}\n\n"
        "👋 Welcome! Send me any message containing URLs, and I'll send you screenshots!\n\n"
        "💡 *Example messages:*\n• Check this out https://example.com\n• Multiple links in one message\n\n"
        "✨ *Features:*\n• Extract links from any message\n• Works with forwarded messages\n"
        "• Smart AOD panel capture for Amazon deals\n• Smart cropping for Flipkart links\n\n"
        "Just send or forward any message with links! 🚀",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = is_user_authorized(update.effective_user.id)
    if not auth['authorized']:
        await update.message.reply_text(get_denial_message(auth), parse_mode='Markdown')
        return

    await update.message.reply_text(
        "📖 *How to use:*\n1. Send any message containing URLs\n2. Or forward a message with links to me\n"
        "3. I'll extract the links automatically\n4. Wait for screenshots (may take 30-60 seconds)\n\n"
        "🌐 *Supported:*\n• ANY website URL\n• Multiple links in one message\n\n"
        "✂️ *Smart Cropping:*\n• Amazon links: AOD panel (576×239) — fallback 1240×649\n"
        "• Flipkart links: 1240×540 (top cropped)\n• Other sites: 1240×649 (standard)\n\n"
        "🤖 *Commands:*\n/start - Start the bot\n/help - Show this message\n"
        "/status - Check if bot is working\n/myaccount - Check your subscription status",
        parse_mode='Markdown'
    )

async def myaccount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    auth = is_user_authorized(user_id)
    if not auth['authorized']:
        await update.message.reply_text(get_denial_message(auth), parse_mode='Markdown')
        return

    info = auth.get('info', {})
    plan = info.get('plan', 'Unknown')
    expiry = auth.get('expiry_date', 'Unknown')
    added = info.get('added_on', 'Unknown')
    days = auth.get('days_remaining', 0)
    username = info.get('username', 'Unknown')

    await update.message.reply_text(
        f"👤 *My Account*\n\n👤 Username: @{username}\n🔑 User ID: `{user_id}`\n"
        f"📦 Plan: *{plan.title()}*\n📅 Active Since: {added}\n⏳ Expires: {expiry}\n"
        f"✅ Status: {days} days remaining\n\nNeed to renew? Contact @{contact_username}",
        parse_mode='Markdown'
    )

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 This command is only available to admins.")
        return

    msg = "👥 *All Authorized Users:*\n\n"
    for uid, info in authorized_users.items():
        msg += f"• `{uid}` @{info.get('username', 'Unknown')} | {info.get('plan', '?')}\n"
    msg += f"\n📊 Total: {len(authorized_users)} users"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth = is_user_authorized(update.effective_user.id)
    if not auth['authorized']:
        await update.message.reply_text(get_denial_message(auth), parse_mode='Markdown')
        return

    status_text = "✅ Bot is active (Webhook Mode)\n\n"
    if browser and browser_context:
        status_text += "🟢 Screenshot engine: Ready\n"
        status_text += f"📐 Screenshot sizes:\n  • Amazon AOD: {AOD_CROP_W}×{AOD_CROP_H} (primary)\n"
        status_text += "  • Amazon fallback: 1240×649\n  • Flipkart: 1240×540\n  • Default: 1240×649\n"
    else:
        status_text += "🔴 Screenshot engine: Not initialized\n"
    await update.message.reply_text(status_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    auth = is_user_authorized(user_id)
    
    if not auth['authorized']:
        await update.message.reply_text(get_denial_message(auth), parse_mode='Markdown')
        return

    message_text = update.message.text or update.message.caption or ""
    urls = extract_urls(message_text)
    
    if not urls:
        await update.message.reply_text("❌ No links found in your message!")
        return

    confirm_msg = await update.message.reply_text(
        f"🔍 Found {len(urls)} link(s)!\n📸 Generating screenshot{'s' if len(urls) > 1 else ''}...\n"
        f"⏳ This may take 30-60 seconds for heavy websites..."
    )

    successful = 0
    failed = 0

    for idx, url in enumerate(urls, 1):
        try:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            
            if len(urls) > 1:
                try: await confirm_msg.edit_text(f"🔄 Processing link {idx}/{len(urls)}...\n🔗 {url[:50]}...")
                except: pass

            screenshot_bytes = await capture_screenshot(url)

            if not screenshot_bytes:
                failed += 1
                await update.message.reply_text(
                    f"❌ Failed to capture screenshot for:\n{url}\n\n"
                    f"⚠️ The page might be too slow, blocking bots, or timing out."
                )
                continue

            caption = message_text[:1024] if len(urls) == 1 else f"📸 Screenshot {idx}/{len(urls)}\n🔗 {url[:900]}"
            
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
            await update.message.reply_photo(photo=BytesIO(screenshot_bytes), caption=caption)
            
            successful += 1

            if idx < len(urls): await asyncio.sleep(1)

        except Exception as e:
            failed += 1
            logger.error(f"Error processing URL: {e}")
            await update.message.reply_text(f"❌ Error processing link {idx}/{len(urls)}:\n{url}\n\nError: {str(e)[:100]}")

    try: await confirm_msg.delete()
    except: pass

    summary = f"✅ Completed!\n\n📊 Results:\n✅ Successful: {successful}\n"
    if failed > 0: summary += f"❌ Failed: {failed}\n"
    await update.message.reply_text(summary)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

# ==========================================
# WEBHOOK & RUNNER
# ==========================================
async def health_check(request):
    return web.Response(text="OK - Bot Active", status=200)

async def webhook_handler(request):
    global application
    if not application: return web.Response(status=503)
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
        return web.Response(status=200)
    except Exception:
        return web.Response(status=500)

async def startup(app):
    global application
    await init_browser()
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("myaccount", myaccount))
    application.add_handler(CommandHandler("users", admin_users))
    application.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    await application.initialize()
    await application.start()

    webhook_url = os.getenv('RENDER_EXTERNAL_URL')
    if webhook_url:
        full_url = f"{webhook_url}/webhook/{BOT_TOKEN}"
        await application.bot.set_webhook(url=full_url, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        logger.info(f"✅ Webhook configured at {full_url}")

async def shutdown(app):
    global application
    await close_browser()
    if application:
        await application.stop()
        await application.shutdown()

def create_app():
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_post(f"/webhook/{BOT_TOKEN}", webhook_handler)
    app.on_startup.append(startup)
    app.on_cleanup.append(shutdown)
    return app

if __name__ == '__main__':
    app = create_app()
    web.run_app(app, host='0.0.0.0', port=PORT)
