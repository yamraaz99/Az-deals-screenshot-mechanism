import os
import json
import logging
import asyncio
import re
from io import BytesIO
from datetime import datetime, date
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from playwright.async_api import async_playwright
from aiohttp import web

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get configuration from environment
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
PORT = int(os.getenv('PORT', 10000))

# Screenshot settings
SCREENSHOT_WIDTH = 1240
SCREENSHOT_HEIGHT = 649
SCREENSHOT_TIMEOUT = 60
SCREENSHOT_MAX_RETRIES = 2

# Global browser instance
browser = None
browser_context = None
playwright_instance = None

# Global application instance
application = None


# ============================================================
# === ACCESS CONTROL SYSTEM ===
# ============================================================

# Store loaded user data
authorized_users = {}
admin_ids = []
contact_username = "admin"

def load_users():
    """Load authorized users from users.json file."""
    global authorized_users, admin_ids, contact_username
    
    # Try multiple paths (works both locally and in Docker)
    possible_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.json'),
        '/app/users.json',
        'users.json'
    ]
    
    for filepath in possible_paths:
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                authorized_users = data.get('authorized_users', {})
                admin_ids = data.get('admin_ids', [])
                contact_username = data.get('contact_username', 'admin')
                logger.info(f"✅ Loaded {len(authorized_users)} authorized users from {filepath}")
                logger.info(f"👑 Admin IDs: {admin_ids}")
                return True
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON in {filepath}: {e}")
            continue
        except Exception as e:
            logger.error(f"❌ Error loading users from {filepath}: {e}")
            continue
    
    logger.error("❌ users.json not found! No users will be authorized.")
    return False

def is_user_authorized(user_id: int) -> dict:
    """
    Check if a user is authorized to use the bot.
    Returns dict with 'authorized' (bool), 'reason' (str), and user 'info' (dict or None).
    """
    user_id_str = str(user_id)
    
    # Check if user exists in authorized list
    if user_id_str not in authorized_users:
        return {
            'authorized': False,
            'reason': 'not_registered',
            'info': None
        }
    
    user_info = authorized_users[user_id_str]
    
    # Check expiry date
    expiry_str = user_info.get('expiry', '2000-01-01')
    try:
        expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
    except ValueError:
        logger.error(f"Invalid expiry date format for user {user_id}: {expiry_str}")
        return {
            'authorized': False,
            'reason': 'invalid_expiry',
            'info': user_info
        }
    
    today = date.today()
    
    if today > expiry_date:
        days_expired = (today - expiry_date).days
        return {
            'authorized': False,
            'reason': 'expired',
            'info': user_info,
            'expiry_date': expiry_str,
            'days_expired': days_expired
        }
    
    # User is authorized
    days_remaining = (expiry_date - today).days
    return {
        'authorized': True,
        'reason': 'active',
        'info': user_info,
        'expiry_date': expiry_str,
        'days_remaining': days_remaining
    }

def is_admin(user_id: int) -> bool:
    """Check if user is an admin."""
    return user_id in admin_ids

def get_denial_message(auth_result: dict) -> str:
    """Generate appropriate denial message based on auth result."""
    reason = auth_result.get('reason', 'unknown')
    
    if reason == 'not_registered':
        return (
            "🔒 *Access Denied*\n\n"
            "You don't have access to this bot.\n\n"
            "This is a premium bot available to paid subscribers only.\n\n"
            f"📩 Contact @{contact_username} to get access.\n\n"
            "💰 *Plans Available:*\n"
            "• Monthly subscription\n"
            "• Lifetime access\n\n"
            "Send your payment and get instant activation!"
        )
    
    elif reason == 'expired':
        expiry = auth_result.get('expiry_date', 'Unknown')
        days = auth_result.get('days_expired', 0)
        username = auth_result.get('info', {}).get('username', 'User')
        return (
            "⏰ *Subscription Expired*\n\n"
            f"Hey @{username}, your subscription expired on *{expiry}* "
            f"({days} day{'s' if days != 1 else ''} ago).\n\n"
            f"📩 Contact @{contact_username} to renew your access.\n\n"
            "Renew now to continue using the bot! 🔄"
        )
    
    elif reason == 'invalid_expiry':
        return (
            "⚠️ *Account Error*\n\n"
            "There's an issue with your account configuration.\n\n"
            f"📩 Please contact @{contact_username} to fix this."
        )
    
    else:
        return (
            "🔒 *Access Denied*\n\n"
            f"📩 Contact @{contact_username} for access."
        )

# Load users on module import
load_users()

# ============================================================
# === END ACCESS CONTROL SYSTEM ===
# ============================================================


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
                logger.info("Playwright started successfully")

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
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--no-first-run',
                    '--no-zygote'
                ]
            )
            logger.info("Browser launched successfully")

            browser_context = await browser.new_context(
                viewport={'width': SCREENSHOT_WIDTH, 'height': SCREENSHOT_HEIGHT + 250},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ignore_https_errors=True
            )
            logger.info("Browser context created successfully")

            test_page = await browser_context.new_page()
            await test_page.goto('about:blank', timeout=5000)
            await test_page.close()
            logger.info("Browser test passed")

            logger.info("✅ Browser initialized successfully!")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize browser (attempt {attempt + 1}/{max_retries}): {e}")

            if browser_context:
                try:
                    await browser_context.close()
                except:
                    pass
                browser_context = None

            if browser:
                try:
                    await browser.close()
                except:
                    pass
                browser = None

            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                logger.error("❌ Failed to initialize browser after all retries")
                return False

    return False


async def close_browser():
    """Close browser on shutdown."""
    global browser, browser_context, playwright_instance

    logger.info("Closing browser...")

    if browser_context:
        try:
            await browser_context.close()
        except Exception as e:
            logger.error(f"Error closing browser context: {e}")

    if browser:
        try:
            await browser.close()
        except Exception as e:
            logger.error(f"Error closing browser: {e}")

    if playwright_instance:
        try:
            await playwright_instance.stop()
        except Exception as e:
            logger.error(f"Error stopping Playwright: {e}")

    logger.info("✅ Browser cleanup completed")


def extract_urls(text):
    """Extract all URLs from text."""
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = re.findall(url_pattern, text)
    return urls


def get_url_type(url):
    """Determine the type of URL for screenshot customization."""
    url_lower = url.lower()

    if any(domain in url_lower for domain in ['fkrt.cc', 'fkrt.to', 'fkrt.site', 'fkrt.co']):
        return 'flipkart'
    elif 'amzn.to' in url_lower or 'amazon.in' in url_lower:
        return 'amazon'
    else:
        return 'default'


async def capture_screenshot(url, timeout=SCREENSHOT_TIMEOUT, max_retries=SCREENSHOT_MAX_RETRIES):
    """Capture screenshot of URL using Playwright with advanced optimizations."""
    global browser_context

    if not browser_context:
        logger.error("Browser context is not initialized")
        return None

    url_type = get_url_type(url)
    logger.info(f"URL type detected: {url_type} for {url}")

    for attempt in range(max_retries):
        page = None
        try:
            page = await browser_context.new_page()

            await page.set_extra_http_headers({
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
            })

            async def route_handler(route):
                request = route.request
                if request.resource_type in ["font", "media"]:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", route_handler)

            page_loaded = False
            strategies = ['commit', 'domcontentloaded', 'load']

            for strategy in strategies:
                try:
                    logger.info(f"Loading {url} with strategy '{strategy}' (attempt {attempt + 1}/{max_retries})")
                    await page.goto(
                        url,
                        wait_until=strategy,
                        timeout=timeout * 1000
                    )
                    page_loaded = True
                    logger.info(f"✅ Page loaded with strategy: {strategy}")
                    break
                except Exception as e:
                    logger.warning(f"Strategy '{strategy}' failed: {str(e)[:100]}")
                    if strategy == strategies[-1]:
                        raise
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
                except:
                    pass
            elif url_type == 'amazon':
                try:
                    await page.evaluate("window.scrollTo(0, 300)")
                    await page.wait_for_timeout(1500)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(800)
                except:
                    pass

            if url_type == 'flipkart':
                logger.info("📸 Flipkart mode: Cropping top 100px (1240×540)")
                screenshot_bytes = await page.screenshot(
                    full_page=False,
                    type='jpeg',
                    quality=85,
                    animations='disabled',
                    clip={'x': 0, 'y': 100, 'width': SCREENSHOT_WIDTH, 'height': 540}
                )
            elif url_type == 'amazon':
                logger.info("📸 Amazon mode: Removing top 250px header, keeping 1240×649")
                screenshot_bytes = await page.screenshot(
                    full_page=False,
                    type='jpeg',
                    quality=85,
                    animations='disabled',
                    clip={'x': 0, 'y': 250, 'width': SCREENSHOT_WIDTH, 'height': SCREENSHOT_HEIGHT}
                )
            else:
                logger.info("📸 Default mode: Standard 1240×649")
                screenshot_bytes = await page.screenshot(
                    full_page=False,
                    type='jpeg',
                    quality=85,
                    animations='disabled'
                )

            logger.info(f"✅ Screenshot captured for: {url}")
            return screenshot_bytes

        except Exception as e:
            logger.error(f"Screenshot attempt {attempt + 1}/{max_retries} failed for {url}: {e}")

            if attempt < max_retries - 1:
                logger.info(f"Retrying in 2 seconds...")
                await asyncio.sleep(2)
            else:
                logger.error(f"❌ All {max_retries} attempts failed for {url}")
                return None
        finally:
            if page:
                try:
                    await page.close()
                except:
                    pass

    return None


# ============================================================
# === COMMAND HANDLERS (with access control) ===
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message when /start is issued."""
    user_id = update.effective_user.id
    logger.info(f"User {user_id} (@{update.effective_user.username}) started the bot")

    # === ACCESS CHECK ===
    auth = is_user_authorized(user_id)
    if not auth['authorized']:
        # Log the denied attempt with user info for easy adding later
        logger.warning(
            f"🚫 ACCESS DENIED for user_id={user_id}, "
            f"username=@{update.effective_user.username}, "
            f"name={update.effective_user.first_name}, "
            f"reason={auth['reason']}"
        )
        await update.message.reply_text(
            get_denial_message(auth),
            parse_mode='Markdown'
        )
        return

    # Show remaining days for authorized users
    days_info = ""
    if auth.get('days_remaining') is not None:
        days = auth['days_remaining']
        plan = auth.get('info', {}).get('plan', 'unknown')
        if plan == 'lifetime':
            days_info = "♾️ Lifetime Access"
        elif days <= 7:
            days_info = f"⚠️ {days} day{'s' if days != 1 else ''} remaining!"
        else:
            days_info = f"📅 {days} days remaining"

    await update.message.reply_text(
        f"✅ *Bot is Active!*\n"
        f"👤 *Your Status:* {days_info}\n\n"
        "🔗 Welcome! Send me any message containing URLs, "
        "and I'll send you screenshots!\n\n"
        "*Example messages:*\n"
        "• Check this out https://example.com\n"
        "• https://github.com/user/repo\n"
        "• Multiple links in one message\n\n"
        "🛠 *Features:*\n"
        "• Extract links from any message\n"
        "• Works with forwarded messages\n"
        "• Handles multiple links\n"
        "• High-quality screenshots\n"
        "• Supports ALL websites!\n"
        "• Smart cropping for Amazon & Flipkart links\n\n"
        "Just send or forward any message with links! 🚀",
        parse_mode='Markdown'
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message."""
    # === ACCESS CHECK ===
    auth = is_user_authorized(update.effective_user.id)
    if not auth['authorized']:
        await update.message.reply_text(get_denial_message(auth), parse_mode='Markdown')
        return

    await update.message.reply_text(
        "📖 *How to use:*\n\n"
        "1. Send any message containing URLs\n"
        "2. Or forward a message with links to me\n"
        "3. I'll extract the links automatically\n"
        "4. Wait for screenshots (may take 30-60 seconds)\n"
        "5. Receive screenshots with your original message!\n\n"
        "*Supported:*\n"
        "• ANY website URL (http:// or https://)\n"
        "• Shopping sites, social media, news, blogs\n"
        "• Multiple links in one message\n\n"
        "*Smart Cropping:*\n"
        "• 🛒 Flipkart links (fkrt.): 1240×540 (top cropped)\n"
        "• 📦 Amazon links: 1240×649 (header removed)\n"
        "• 🌐 Other sites: 1240×649 (standard)\n\n"
        "*Commands:*\n"
        "/start - Start the bot\n"
        "/help - Show this message\n"
        "/status - Check if bot is working\n"
        "/myaccount - Check your subscription status",
        parse_mode='Markdown'
    )


async def myaccount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user their account/subscription details."""
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

    if plan == 'lifetime':
        status_emoji = "♾️"
        days_text = "Lifetime - Never expires"
    elif days <= 3:
        status_emoji = "🔴"
        days_text = f"{days} day{'s' if days != 1 else ''} remaining - RENEW SOON!"
    elif days <= 7:
        status_emoji = "🟡"
        days_text = f"{days} days remaining"
    else:
        status_emoji = "🟢"
        days_text = f"{days} days remaining"

    await update.message.reply_text(
        f"👤 *My Account*\n\n"
        f"📛 Username: @{username}\n"
        f"🆔 User ID: `{user_id}`\n"
        f"📋 Plan: *{plan.title()}*\n"
        f"📅 Active Since: {added}\n"
        f"📅 Expires: {expiry}\n"
        f"{status_emoji} Status: {days_text}\n\n"
        f"Need to renew? Contact @{contact_username}",
        parse_mode='Markdown'
    )


async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to list all users. Only works for admins."""
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ This command is only available to admins.")
        return

    if not authorized_users:
        await update.message.reply_text("📋 No users in the database.")
        return

    today = date.today()
    msg = "👑 *All Authorized Users:*\n\n"

    for uid, info in authorized_users.items():
        username = info.get('username', 'Unknown')
        plan = info.get('plan', '?')
        expiry_str = info.get('expiry', '?')

        try:
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
            days_left = (expiry_date - today).days
            if days_left < 0:
                status = f"❌ Expired {abs(days_left)}d ago"
            elif plan == 'lifetime':
                status = "♾️ Lifetime"
            elif days_left <= 7:
                status = f"⚠️ {days_left}d left"
            else:
                status = f"✅ {days_left}d left"
        except:
            status = "⚠️ Invalid date"

        msg += f"• `{uid}` @{username} | {plan} | {status}\n"

    msg += f"\n📊 Total: {len(authorized_users)} users"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check bot status."""
    global browser, browser_context

    # === ACCESS CHECK ===
    auth = is_user_authorized(update.effective_user.id)
    if not auth['authorized']:
        await update.message.reply_text(get_denial_message(auth), parse_mode='Markdown')
        return

    status_text = "🤖 Bot is active (Webhook Mode)\n\n"

    if browser and browser_context:
        status_text += "🖥 Screenshot engine: ✅ Ready\n"
        status_text += f"⏱ Timeout: {SCREENSHOT_TIMEOUT}s\n"
        status_text += f"🔄 Max retries: {SCREENSHOT_MAX_RETRIES}\n"
        status_text += f"📐 Screenshot sizes:\n"
        status_text += f"  • Flipkart: 1240×540 (cropped)\n"
        status_text += f"  • Amazon: 1240×649 (header removed)\n"
        status_text += f"  • Default: 1240×649\n"
        status_text += "✅ You can send URLs for screenshots!"
    elif browser and not browser_context:
        status_text += "🖥 Screenshot engine: ⚠️ Browser loaded but context failed\n"
        status_text += "Please contact administrator"
    else:
        status_text += "🖥 Screenshot engine: ❌ Not initialized\n"
        status_text += "Please contact administrator"

    await update.message.reply_text(status_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages and extract URLs."""
    global browser, browser_context

    user_id = update.effective_user.id

    # === ACCESS CHECK (MOST IMPORTANT - this is where links are processed) ===
    auth = is_user_authorized(user_id)
    if not auth['authorized']:
        logger.warning(
            f"🚫 UNAUTHORIZED message from user_id={user_id}, "
            f"username=@{update.effective_user.username}"
        )
        await update.message.reply_text(get_denial_message(auth), parse_mode='Markdown')
        return

    if not browser or not browser_context:
        logger.error(f"Browser not available for user {user_id}")
        await update.message.reply_text(
            "⏳ Screenshot service is initializing or unavailable.\n\n"
            "Please try again in a few moments."
        )
        return

    message_text = update.message.text or update.message.caption or ""

    logger.info(f"Message from authorized user {user_id} (@{update.effective_user.username}): {message_text[:100]}")

    urls = extract_urls(message_text)

    if not urls:
        await update.message.reply_text(
            "🔍 No links found in your message!\n\n"
            "Please send a message containing URLs."
        )
        return

    logger.info(f"Found {len(urls)} URL(s) from user {user_id}")

    confirm_msg = await update.message.reply_text(
        f"🔗 Found {len(urls)} link(s)!\n📸 Generating screenshot{'s' if len(urls) > 1 else ''}...\n"
        f"⏱ This may take 30-60 seconds for heavy websites..."
    )

    successful = 0
    failed = 0

    for idx, url in enumerate(urls, 1):
        try:
            logger.info(f"Processing URL {idx}/{len(urls)}: {url}")

            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id,
                action=ChatAction.TYPING
            )

            if len(urls) > 1:
                try:
                    await confirm_msg.edit_text(
                        f"📸 Processing link {idx}/{len(urls)}...\n"
                        f"🔗 {url[:50]}{'...' if len(url) > 50 else ''}\n\n"
                        f"⏳ Please wait, loading page..."
                    )
                except Exception as edit_error:
                    logger.warning(f"Failed to edit progress message: {edit_error}")

            screenshot_bytes = await capture_screenshot(url)

            if not screenshot_bytes:
                failed += 1
                await update.message.reply_text(
                    f"❌ Failed to capture screenshot for:\n{url}\n\n"
                    f"⚠️ The page might be too slow, blocking bots, or timing out.\n"
                    f"Try shortening the URL or checking if it's accessible."
                )
                logger.warning(f"Screenshot failed for URL {idx}/{len(urls)}")
                continue

            # Caption handling
            if len(urls) == 1:
                caption = message_text[:1024]
            else:
                caption = f"📸 Screenshot {idx}/{len(urls)}\n🔗 {url[:900]}"

            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id,
                action=ChatAction.UPLOAD_PHOTO
            )

            await update.message.reply_photo(
                photo=BytesIO(screenshot_bytes),
                caption=caption
            )

            successful += 1
            logger.info(f"✅ Screenshot {idx}/{len(urls)} sent successfully")

            if idx < len(urls):
                await asyncio.sleep(1)

        except Exception as e:
            failed += 1
            logger.error(f"Error processing URL {idx}/{len(urls)} from user {user_id}: {e}", exc_info=True)
            try:
                await update.message.reply_text(
                    f"❌ Error processing link {idx}/{len(urls)}:\n{url}\n\n"
                    f"Error: {str(e)[:100]}"
                )
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    # Delete progress message
    try:
        await confirm_msg.delete()
    except Exception as delete_error:
        logger.warning(f"Failed to delete progress message: {delete_error}")

    # Send summary
    summary = f"✅ Completed!\n\n"
    summary += f"📊 Results:\n"
    summary += f"  ✅ Successful: {successful}\n"
    if failed > 0:
        summary += f"  ❌ Failed: {failed}\n"

    try:
        await update.message.reply_text(summary)
    except Exception as summary_error:
        logger.error(f"Failed to send summary: {summary_error}")

    logger.info(f"Finished processing {len(urls)} URLs for user {user_id}. Success: {successful}, Failed: {failed}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors caused by updates."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)


async def health_check(request):
    """Health check endpoint for Render."""
    global browser, browser_context, application

    status = {
        "bot": "initialized" if application else "not_initialized",
        "browser": "ready" if (browser and browser_context) else "not_ready",
        "authorized_users": len(authorized_users)
    }

    if application and browser and browser_context:
        return web.Response(
            text=f"OK - Bot: Active, Browser: Ready, Users: {len(authorized_users)}",
            status=200
        )
    else:
        return web.Response(text=f"Starting - {status}", status=200)


async def webhook_handler(request):
    """Handle incoming webhook updates."""
    global application

    if not application:
        logger.error("Application not initialized yet")
        return web.Response(text="Service starting, please retry", status=503)

    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return web.Response(status=500)


async def startup(app):
    """Initialize bot and browser on startup."""
    global application

    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    print("=" * 60)
    print("🤖 Telegram Screenshot Bot (Webhook Mode)")
    print("=" * 60)
    print(f"🔑 Bot Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-10:]}")
    print(f"🌐 Port: {PORT}")
    print(f"👥 Authorized Users: {len(authorized_users)}")
    print(f"👑 Admin IDs: {admin_ids}")
    print(f"📐 Screenshot Sizes:")
    print(f"  • Flipkart: 1240×540 (top 100px cropped)")
    print(f"  • Amazon: 1240×649 (top 250px cropped)")
    print(f"  • Default: 1240×649")
    print(f"⏱ Timeout: {SCREENSHOT_TIMEOUT}s")
    print(f"🔄 Max Retries: {SCREENSHOT_MAX_RETRIES}")
    print("=" * 60)

    # Initialize browser FIRST
    logger.info("Step 1: Initializing browser...")
    browser_ready = await init_browser()

    if not browser_ready:
        logger.error("Browser initialization failed, but continuing...")

    # Create application
    logger.info("Step 2: Creating Telegram application...")
    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("myaccount", myaccount))
    application.add_handler(CommandHandler("users", admin_users))
    application.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
        handle_message
    ))
    application.add_error_handler(error_handler)

    # Initialize application
    logger.info("Step 3: Initializing Telegram application...")
    await application.initialize()
    await application.start()

    # Set webhook
    webhook_url = os.getenv('RENDER_EXTERNAL_URL')
    if webhook_url:
        webhook_path = f"/webhook/{BOT_TOKEN}"
        webhook_full_url = f"{webhook_url}{webhook_path}"

        logger.info(f"Step 4: Setting webhook to {webhook_full_url}")
        await application.bot.set_webhook(
            url=webhook_full_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )

        print(f"🔗 Webhook set: {webhook_full_url}")
        logger.info(f"Webhook configured at {webhook_full_url}")
    else:
        logger.warning("RENDER_EXTERNAL_URL not set, webhook may not work correctly")

    print("✅ Bot initialized successfully!")
    print("=" * 60)


async def shutdown(app):
    """Cleanup on shutdown."""
    global application

    logger.info("Shutting down...")

    await close_browser()

    if application:
        await application.stop()
        await application.shutdown()

    logger.info("Shutdown complete")


def create_app():
    """Create and configure the web application."""
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set!")

    app = web.Application()

    # Health check endpoint
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)

    # Webhook endpoint
    webhook_path = f"/webhook/{BOT_TOKEN}"
    app.router.add_post(webhook_path, webhook_handler)

    # Startup and cleanup
    app.on_startup.append(startup)
    app.on_cleanup.append(shutdown)

    return app


if __name__ == '__main__':
    try:
        app = create_app()
        print(f"Starting web server on 0.0.0.0:{PORT}")
        web.run_app(app, host='0.0.0.0', port=PORT)
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        raise
