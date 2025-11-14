FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

# Install requests for webhook clearing
RUN pip install --no-cache-dir requests

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all bot files
COPY bot.py .
COPY bot_webhook.py .
COPY check_playwright.py .
COPY clear_webhook.py .

# Verify Playwright installation
RUN python check_playwright.py

# Default command (can be overridden in render.yaml)
CMD ["sh", "-c", "python clear_webhook.py && python bot.py"]
