import os
import logging
import asyncio
import re
from io import BytesIO
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from playwright.async_api import async_playwright

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get bot token from environment
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Screenshot settings
SCREENSHOT_WIDTH = 1240
SCREENSHOT_HEIGHT = 649

# Global browser instance
browser = None
browser_context = None

async def init_browser():
    """Initialize browser on startup."""
    global browser, browser_context
    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )
        browser_context = await browser.new_context(
            viewport={'width': SCREENSHOT_WIDTH, 'height': SCREENSHOT_HEIGHT},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        logger.info("Browser initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize browser: {e}")
        raise

async def close_browser():
    """Close browser on shutdown."""
    global browser, browser_context
    if browser_context:
        await browser_context.close()
    if browser:
        await browser.close()
    logger.info("Browser closed")

def extract_urls(text):
    """Extract all URLs from text."""
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = re.findall(url_pattern, text)
    return urls

async def capture_screenshot(url, timeout=30):
    """Capture screenshot of URL using Playwright."""
    global browser_context
    page = None
    try:
        page = await browser_context.new_page()
        await page.goto(url, wait_until='networkidle', timeout=timeout * 1000)
        
        # Take screenshot
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
            await page.close()

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
    status_text = "✅ Bot is active and working!\n"
    if browser:
        status_text += "🌐 Screenshot engine: Ready"
    else:
        status_text += "⚠️ Screenshot engine: Initializing..."
    await update.message.reply_text(status_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages and extract URLs."""
    message_text = update.message.text or update.message.caption or ""
    user_id = update.effective_user.id
    
    logger.info(f"Message from user {user_id}: {message_text[:100]}")
    
    # Extract URLs
    urls = extract_urls(message_text)
    
    if not urls:
        logger.info(f"No URLs found in message from user {user_id}")
        await update.message.reply_text(
            "🔍 No links found in your message!\n\n"
            "Please send a message containing URLs.\n\n"
            "Example: Check this out https://example.com"
        )
        return
    
    logger.info(f"Found {len(urls)} URL(s) from user {user_id}")
    
    # Send confirmation
    if len(urls) == 1:
        confirm_msg = await update.message.reply_text(
            f"✅ Found 1 link!\n📸 Generating screenshot..."
        )
    else:
        confirm_msg = await update.message.reply_text(
            f"✅ Found {len(urls)} links!\n📸 Generating screenshots..."
        )
    
    # Process each URL
    for idx, url in enumerate(urls, 1):
        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, 
                action=ChatAction.TYPING
            )
            
            # Update progress
            if len(urls) > 1:
                await confirm_msg.edit_text(
                    f"📸 Processing link {idx}/{len(urls)}...\n"
                    f"🔗 {url[:50]}{'...' if len(url) > 50 else ''}"
                )
            
            # Capture screenshot
            screenshot_bytes = await capture_screenshot(url)
            
            if not screenshot_bytes:
                await update.message.reply_text(
                    f"❌ Failed to capture screenshot for:\n{url}"
                )
                continue
            
            # Prepare caption
            caption = message_text[:1024]
            
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, 
                action=ChatAction.UPLOAD_PHOTO
            )
            
            # Send screenshot
            await update.message.reply_photo(
                photo=BytesIO(screenshot_bytes),
                caption=caption
            )
            
            logger.info(f"Screenshot {idx} sent to user {user_id}")
            
            # Delay between screenshots
            if idx < len(urls):
                await asyncio.sleep(2)
        
        except Exception as e:
            logger.error(f"Error processing URL {idx} from user {user_id}: {e}")
            await update.message.reply_text(
                f"❌ Error processing link {idx}/{len(urls)}:\n{url}"
            )
    
    # Delete progress message
    try:
        await confirm_msg.delete()
    except:
        pass
    
    # Send completion message
    await update.message.reply_text(
        f"✅ Completed! Processed {len(urls)} link(s)."
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors caused by updates."""
    logger.error(f"Update {update} caused error {context.error}")

async def post_init(application):
    """Initialize browser after bot starts."""
    await init_browser()

async def post_shutdown(application):
    """Close browser on shutdown."""
    await close_browser()

def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        print("❌ ERROR: TELEGRAM_BOT_TOKEN not set!")
        return
    
    print("🤖 Starting Telegram Screenshot Bot...")
    
    # Create application
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
    
    # Set up initialization and cleanup
    application.post_init = post_init
    application.post_shutdown = post_shutdown
    
    print("✅ Bot started successfully!")
    print("💬 Send /start to your bot to test it")
    print("🔄 Polling for messages...\n")
    logger.info("Bot started and polling...")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
