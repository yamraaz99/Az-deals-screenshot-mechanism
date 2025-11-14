FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code and test script
COPY bot.py .
COPY test_browser.py .

# Test browser installation (optional, comment out if causing issues)
RUN python test_browser.py || echo "Browser test failed but continuing..."

# Run the bot
CMD ["python", "-u", "bot.py"]
