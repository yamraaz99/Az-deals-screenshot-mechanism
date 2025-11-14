# Use official Playwright image with all dependencies pre-installed
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# Copy requirements first (for better caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY bot_webhook.py .

# Expose port
EXPOSE 10000

# Run the application
CMD ["python", "bot_webhook.py"]
