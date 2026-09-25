# 🛡️ Microsoft Update Tracker (`ms-update-tracker`)

A containerized Flask web application and automated Playwright scraper that monitors the Microsoft Update Catalog, stores patch records locally, and sends scheduled HTML email reports via Linux `cron` tasks.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0+-000000?style=flat&logo=flask&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED?style=flat&logo=docker&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-Automated-2EAD33?style=flat&logo=playwright&logoColor=white)

---

## 🌟 Key Features

* **Automated Catalog Scraping:** Headless Playwright engine extracts update titles, classifications, sizes, package file names, and direct download links.
* **Smart Reporting Modes:**
  * `All New Unnotified Updates`: Tracks sent update GUIDs to eliminate duplicate email alerts.
  * `Last 3 Latest Items`: Generates detailed overview cards with Knowledge Base links and reboot behavior.
  * `This Current Month` & `Specific Month`: Produces clean HTML summary tables for targeted patch auditing.
* **Containerized Deployment:** Bundles Flask, Playwright, SQLite caching, and an internal Linux system `cron` daemon inside a single Docker container.
* **Built-in Timezone Alignment:** Synchronized to local system time (`Asia/Manila` / UTC+8) for accurate scheduled execution.
* **Hardened Web Security:** Features password authentication, externalized JavaScript (`static/app.js`), Content Security Policy (CSP) headers, and `HttpOnly` session cookies.

---

## 📁 Repository File Structure

```text
ms-update-tracker/
├── static/
│   └── app.js          # Client-side UI interaction script
├── .env                # Environment template (SMTP & Auth secrets)
├── .gitignore          # Git exclusion rules
├── app.py              # Core Flask app, scrapers, and cron endpoints
├── config.json         # Persistent user configurations and notified IDs
├── database.db         # Persistent SQLite update cache
├── Dockerfile          # Container build recipe
├── docker-compose.yml  # Service orchestration configuration
└── requirements.txt    # Python package dependencies
`````
---

## 📋 Ubuntu Prerequisites & Installation

Before deploying on a fresh Ubuntu Server (20.04 / 22.04 / 24.04 LTS), update your package repository and install Git, Python 3, and Docker.

**Update System Packages & Install Core Utilities:**
```text
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3 python3-pip curl ca-certificates gnupg
`````

**Install Docker & Docker Compose:**
```text
# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL [https://download.docker.com/linux/ubuntu/gpg](https://download.docker.com/linux/ubuntu/gpg) | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker's official repository to APT sources
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] [https://download.docker.com/linux/ubuntu](https://download.docker.com/linux/ubuntu) \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine and Docker Compose plugin
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable Docker service and assign current user to docker group
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
`````

---

## 🔒 Configuration & Git Exclusion Setup

To prevent committing sensitive environment variables, passwords, or persistent local database files, ensure you create a .gitignore before pushing code to your repository.

**Create a .gitignore file in your repository root:**
```text
# Sensitive Environment Variables
.env

# Local Databases & Dynamic Storage
*.db
database.db
config.json

# Python Caches
__pycache__/
*.pyc

# IDE / OS Files
.DS_Store
.vscode/
.idea/
`````
---

## 🚀 Initial Deployment Guide

**Clone the Repository:**
```text
cd ~
git clone [https://github.com/Argsfried/ms-update-tracker.git](https://github.com/Argsfried/ms-update-tracker.git)
cd ms-update-tracker
`````

**Create your local .env configuration file from the template:**
```text
nano .env
`````
**Fill in the details with your setup:**
```text
# Flask Secret Key
SECRET_KEY=generate_a_random_secret_key_here

# Web Dashboard Login
ADMIN_USER=admin
ADMIN_PASS=YourStrongPasswordHere!

# SMTP Email Configuration
SMTP_HOST=mail.yourdomain.com
SMTP_PORT=587
SMTP_USER=notifications@yourdomain.com
SMTP_PASS=YourSmtpPassword
SMTP_SENDER=notifications@yourdomain.com
`````

**Initialize Local Storage Placeholders:**
```text
touch config.json
touch database.db
`````

**Build and Launch Container Stack:**
```text
docker compose up -d --build

# or to recreate

docker compose up -d --force-recreate --build
`````
