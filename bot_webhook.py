import os
import logging
import asyncio
import re
from io import BytesIO
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

# Global browser instance
browser = None
browser_context = None
playwright_instance = None

# Global application instance - INITIALIZE EARLY
application = None

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
                    '--disable-blink-features=AutomationControlled'
                ]
            )
            logger.info("Browser launched successfully")
            
            browser_context = await browser.new_context(
                viewport={'width': SCREENSHOT_WIDTH, 'height': SCREENSHOT_HEIGHT},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
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

async def capture_screenshot(url, timeout=30):
    """Capture screenshot of URL using Playwright."""
    global browser_context
    
    if not browser_context:
        logger.error("Browser context is not initialized")
        return None
    
    page = None
    try:
        page = await browser_context.new_page()
        await page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
        await page.wait_for_timeout(2000)
        
        screenshot_bytes = await page.screenshot(
            full_page=False,
            type='jpeg',
            quality=85
        )
        
        logger.info(f"Screenshot captured for: {url}")
        return screenshot_bytes
        
    except Exception as e:
        logger.error(f"Screenshot error for {url}: {e}")
        return None
    finally:
        if page:
            try:
                await page.close()
            except:
                pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message when /start is issued."""
    logger.info(f"User {update.effective_user.id} started the bot")
    await update.message.reply_text(
        "✅ *Bot is Active!*\n\n"
        "👋 Welcome! Send me any message containing URLs, "
        "and I'll send you screenshots!\n\n"
        "*Example messages:*\n"
        "• Check this out https://example.com\n"
        "• https://github.com/user/repo\n"
        "• Multiple links in one message\n\n"
        "✨ *Features:*\n"
        "• Extract links from any message\n"
        "• Works with forwarded messages\n"
        "• Handles multiple links\n"
        "• High-quality screenshots\n"
        "• Supports ALL websites!\n\n"
        "Just send or forward any message with links! 📸",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message."""
    await update.message.reply_text(
        "🤖 *How to use:*\n\n"
        "1. Send any message containing URLs\n"
        "2. Or forward a message with links to me\n"
        "3. I'll extract the links automatically\n"
        "4. Wait for screenshots (usually 10-20 seconds)\n"
        "5. Receive screenshots with your original message!\n\n"
        "*Supported:*\n"
        "• ANY website URL (http:// or https://)\n"
        "• Shopping sites, social media, news, blogs\n"
        "• Multiple links in one message\n\n"
        "*Commands:*\n"
        "/start - Start the bot\n"
        "/help - Show this message\n"
        "/status - Check if bot is working",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check bot status."""
    global browser, browser_context
    
    status_text = "✅ Bot is active (Webhook Mode)\n\n"
    
    if browser and browser_context:
        status_text += "🌐 Screenshot engine: ✅ Ready\n"
        status_text += "📸 You can send URLs for screenshots!"
    elif browser and not browser_context:
        status_text += "🌐 Screenshot engine: ⚠️ Browser loaded but context failed\n"
        status_text += "Please contact administrator"
    else:
        status_text += "🌐 Screenshot engine: ❌ Not initialized\n"
        status_text += "Please contact administrator"
    
    await update.message.reply_text(status_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages and extract URLs."""
    global browser, browser_context
    
    if not browser or not browser_context:
        logger.error(f"Browser not available for user {update.effective_user.id}")
        await update.message.reply_text(
            "⚠️ Screenshot service is initializing or unavailable.\n\n"
            "Please try again in a few moments."
        )
        return
    
    message_text = update.message.text or update.message.caption or ""
    user_id = update.effective_user.id
    
    logger.info(f"Message from user {user_id}: {message_text[:100]}")
    
    urls = extract_urls(message_text)
    
    if not urls:
        await update.message.reply_text(
            "🔍 No links found in your message!\n\n"
            "Please send a message containing URLs."
        )
        return
    
    logger.info(f"Found {len(urls)} URL(s) from user {user_id}")
    
    confirm_msg = await update.message.reply_text(
        f"✅ Found {len(urls)} link(s)!\n📸 Generating screenshot{'s' if len(urls) > 1 else ''}..."
    )
    
    for idx, url in enumerate(urls, 1):
        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, 
                action=ChatAction.TYPING
            )
            
            if len(urls) > 1:
                await confirm_msg.edit_text(
                    f"📸 Processing link {idx}/{len(urls)}...\n"
                    f"🔗 {url[:50]}{'...' if len(url) > 50 else ''}"
                )
            
            screenshot_bytes = await capture_screenshot(url)
            
            if not screenshot_bytes:
                await update.message.reply_text(
                    f"❌ Failed to capture screenshot for:\n{url}"
                )
                continue
            
            caption = message_text[:1024]
            
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, 
                action=ChatAction.UPLOAD_PHOTO
            )
            
            await update.message.reply_photo(
                photo=BytesIO(screenshot_bytes),
                caption=caption
            )
            
            logger.info(f"Screenshot {idx} sent to user {user_id}")
            
            if idx < len(urls):
                await asyncio.sleep(2)
        
        except Exception as e:
            logger.error(f"Error processing URL {idx} from user {user_id}: {e}")
            await update.message.reply_text(
                f"❌ Error processing link {idx}/{len(urls)}:\n{url}"
            )
    
    try:
        await confirm_msg.delete()
    except:
        pass
    
    await update.message.reply_text(
        f"✅ Completed! Processed {len(urls)} link(s)."
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors caused by updates."""
    logger.error(f"Update {update} caused error {context.error}")

async def health_check(request):
    """Health check endpoint for Render."""
    global browser, browser_context, application
    
    status = {
        "bot": "initialized" if application else "not_initialized",
        "browser": "ready" if (browser and browser_context) else "not_ready"
    }
    
    if application and browser and browser_context:
        return web.Response(text=f"OK - {status}", status=200)
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
        logger.error(f"Webhook error: {e}")
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
    print(f"📋 Bot Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-10:]}")
    print(f"🔌 Port: {PORT}")
    print(f"📐 Screenshot Size: {SCREENSHOT_WIDTH}x{SCREENSHOT_HEIGHT}")
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
    application.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, 
        handle_message
    ))
    application.add_error_handler(error_handler)
    
    # Initialize application
    logger.info("Step 3: Initializing Telegram application...")
    await application.initialize()
    await application.start()
    
    # Set webhook - Render provides RENDER_EXTERNAL_URL automatically
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
        
        print(f"✅ Webhook set: {webhook_full_url}")
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
    
    # Startup and cleanup - FIXED: properly pass coroutines
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
