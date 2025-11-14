#!/usr/bin/env bash
# build.sh - Run during deployment

set -e  # Exit on error

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Installing Playwright browsers..."
playwright install chromium

echo "Installing Playwright system dependencies..."
playwright install-deps chromium

echo "Build completed successfully!"
