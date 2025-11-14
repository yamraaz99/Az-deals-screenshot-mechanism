# Use official Playwright Python image with pre-installed dependencies
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=10000
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Copy requirements first (for better caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (this was missing!)
RUN python -m playwright install chromium && \
    python -m playwright install-deps chromium

# Verify installation
RUN python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); print('Chromium path:', p.chromium.executable_path); p.stop()"

# Copy application code
COPY bot_webhook.py .

# Expose port
EXPOSE 10000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Run the application
CMD ["python", "-u", "bot_webhook.py"]
