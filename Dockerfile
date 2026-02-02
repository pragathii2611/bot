# Use official Python image
FROM python:3.9-slim

# 1. Install basic system tools
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# 2. Install Google Chrome (Direct Download Method)
# This avoids the "apt-key" error by downloading the .deb file directly
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb

# 3. Set working directory
WORKDIR /app

# 4. Copy project files
COPY . .

# 5. Install Python libraries
RUN pip install --no-cache-dir -r requirements.txt

# 6. Run the app
CMD ["python", "app.py"]