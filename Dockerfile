FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    SMAILPRO_HEADLESS=true \
    PORT=10000

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ensure playwright chromium browser is present
RUN playwright install chromium

# Copy application code
COPY . .

# Expose Render health check port
EXPOSE 10000

# Run bot
CMD ["python", "bot.py"]
