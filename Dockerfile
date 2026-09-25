FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Set working directory
WORKDIR /app

# Set timezone environment variables inside container
ENV TZ=Asia/Manila
ENV DEBIAN_FRONTEND=noninteractive

# Install system cron, curl, and tzdata for proper timezone handling
RUN apt-get update && apt-get install -y cron curl tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

# Copy package list and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser binaries
RUN playwright install chromium

# Copy remaining application code
COPY . .

# Create entrypoint script to launch both system cron and Flask server
RUN echo '#!/bin/bash\nservice cron start\npython3 app.py' > /app/entrypoint.sh && \
    chmod +x /app/entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/app/entrypoint.sh"]
