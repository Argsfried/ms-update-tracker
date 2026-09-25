import os
import re
import json
import time
import sqlite3
import requests
import smtplib
from datetime import datetime
from bs4 import BeautifulSoup
from crontab import CronTab
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template_string, jsonify, request, session, redirect, url_for
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.getenv("SECRET_KEY", "fallback_secret_key_12345")

# --- SESSION & COOKIE SECURITY ---
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=False  # Set to True if using HTTPS
)

# --- SECURITY HEADERS MIDDLEWARE ---
@app.after_request
def add_security_headers(response):
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';"
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

DB_FILE = "database.db"
CONFIG_FILE = "config.json"
CACHE_EXPIRATION_SECONDS = 600
DEFAULT_SEARCH_QUERY = "Microsoft server operating system version 24H2"

DEFAULT_CONFIG = {
    "recipients": "it@titusgt.com",
    "search_query": DEFAULT_SEARCH_QUERY,
    "notified_ids": []
}

def save_config(cfg):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[CONFIG SAVE ERROR] {e}", flush=True)

def load_config():
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, 'r') as f:
            data = json.load(f)
            if "recipients" not in data:
                data["recipients"] = DEFAULT_CONFIG["recipients"]
            if "search_query" not in data:
                data["search_query"] = DEFAULT_CONFIG["search_query"]
            return data
    except Exception as e:
        print(f"[CONFIG ERROR] {e}", flush=True)
        return DEFAULT_CONFIG.copy()

def get_smtp_credentials():
    return {
        "server": os.getenv("SMTP_HOST", ""),
        "port": int(os.getenv("SMTP_PORT", 587)),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASS", ""),
        "sender": os.getenv("SMTP_SENDER", "")
    }

def parse_catalog_date(date_str):
    if not date_str or date_str == "N/A":
        return datetime.min
    clean_str = str(date_str).strip()
    
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(clean_str, fmt)
        except ValueError:
            pass
            
    try:
        parts = [int(p) for p in clean_str.split('/')]
        if len(parts) == 3:
            month, day, year = parts[0], parts[1], parts[2]
            if year < 100:
                year += 2000
            return datetime(year, month, day)
    except Exception:
        pass
        
    return datetime.min

@app.before_request
def require_login():
    open_routes = ['login', 'cron_check_updates', 'static']
    if request.endpoint not in open_routes and not session.get('logged_in'):
        if request.path.startswith('/api/') or request.path.startswith('/details/'):
            return jsonify({"error": "Unauthorized access. Please login."}), 401
        return redirect(url_for('login'))

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS updates (
            update_id TEXT PRIMARY KEY,
            search_query TEXT,
            title TEXT,
            product TEXT,
            classification TEXT,
            last_updated TEXT,
            size TEXT,
            file_name TEXT,
            link TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("PRAGMA table_info(updates)")
    columns = [column[1] for column in cursor.fetchall()]
    if "search_query" not in columns:
        cursor.execute("ALTER TABLE updates ADD COLUMN search_query TEXT")
    conn.commit()
    conn.close()

init_db()

def db_save_updates(updates_list, query_filter):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    for item in updates_list:
        uid = item.get('update_id') or item['title']
        cursor.execute("""
            INSERT OR REPLACE INTO updates (update_id, search_query, title, product, classification, last_updated, size, file_name, link)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (uid, query_filter, item['title'], item['product'], item['classification'], item['last_updated'], item['size'], item['file_name'], item['link']))
    conn.commit()
    conn.close()

def db_get_updates(query_filter):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT update_id, title, product, classification, last_updated, size, file_name, link FROM updates WHERE search_query = ? ORDER BY fetched_at DESC", (query_filter,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# --- ENHANCED SAFE OVERVIEW DETAILS FETCH ---
def fetch_update_overview_details(update_id):
    default_details = {
        "Description": "N/A",
        "More Information": "N/A",
        "Restart behavior": "N/A"
    }
    if not update_id or update_id in ("N/A", "None", ""):
        return default_details

    url = f"https://www.catalog.update.microsoft.com/ScopedViewInline.aspx?updateid={update_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
    
    # Retry loop to handle transient Microsoft network delays or throttling
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # Check multiple potential DOM IDs for description
                desc_elem = (
                    soup.find(id="ScopedViewHandler_desc") or 
                    soup.find(id="ScopedViewHandler_details") or 
                    soup.find(class_="scopedViewText")
                )
                if desc_elem and desc_elem.text.strip():
                    default_details["Description"] = desc_elem.text.strip()
                
                # Extract More Information Link
                more_info_div = soup.find(id="moreInfoDiv") or soup.find(id="ScopedViewHandler_moreInfo")
                if more_info_div:
                    link_elem = more_info_div.find('a')
                    if link_elem and link_elem.get('href'):
                        default_details["More Information"] = link_elem.get('href', '').strip()

                # Extract Restart / Reboot Behavior
                restart_elem = soup.find(id="ScopedViewHandler_rebootBehavior") or soup.find(id="ScopedViewHandler_restart")
                if restart_elem and restart_elem.text.strip():
                    default_details["Restart behavior"] = restart_elem.text.strip()
                
                # Exit early if description was successfully fetched
                if default_details["Description"] != "N/A":
                    break
        except Exception as e:
            print(f"[DETAILS FETCH ATTEMPT {attempt+1} WARNING] {update_id}: {e}", flush=True)
            time.sleep(1)

    return default_details

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Login - Microsoft Server Updates</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #eef2f5; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .login-card { background: white; padding: 35px 30px; border-radius: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); width: 340px; }
        h2 { margin-top: 0; color: #0078d4; text-align: center; margin-bottom: 25px; }
        .form-group { margin-bottom: 18px; }
        label { display: block; font-size: 0.85em; font-weight: bold; color: #555; margin-bottom: 5px; }
        input[type="text"], input[type="password"] { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 5px; box-sizing: border-box; font-size: 0.95em; }
        button { width: 100%; padding: 10px; background: #0078d4; color: white; border: none; border-radius: 5px; font-weight: bold; cursor: pointer; font-size: 1em; }
        button:hover { background: #005a9e; }
        .error-msg { background: #fde8e8; color: #e81123; padding: 10px; border-radius: 5px; font-size: 0.85em; margin-bottom: 15px; border: 1px solid #f8b4b4; text-align: center; }
    </style>
</head>
<body>
    <div class="login-card">
        <h2>Admin Login</h2>
        {% if error %}
            <div class="error-msg">{{ error }}</div>
        {% endif %}
        <form method="POST" action="/login">
            <div class="form-group">
                <label>Username:</label>
                <input type="text" name="username" required>
            </div>
            <div class="form-group">
                <label>Password:</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>
"""

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Microsoft Server Updates</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 30px; background-color: #f4f4f9; }
        h1 { color: #333; margin: 0; }
        .top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 15px; }
        .filter-card {
            background: white; padding: 15px 20px; border-radius: 6px; 
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); display: flex; align-items: center; gap: 15px; flex-wrap: wrap;
        }
        .filter-group { display: flex; flex-direction: column; gap: 5px; }
        .filter-group label { font-size: 0.85em; font-weight: bold; color: #555; }
        .filter-group input, .filter-group select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.9em; }
        
        .btn { padding: 8px 14px; background: #0078d4; color: white; border: none; border-radius: 4px; text-decoration: none; font-weight: bold; cursor: pointer; }
        .btn:hover { background: #005a9e; }
        .btn:disabled { background: #cccccc; cursor: not-allowed; }
        .btn-secondary { background: #666; }
        .btn-secondary:hover { background: #444; }
        .btn-purple { background: #5c2d91; }
        .btn-purple:hover { background: #421f69; }
        .btn-purple:disabled { background: #a38fbe; cursor: not-allowed; }
        .btn-success { background: #107c41; }
        .btn-success:hover { background: #0b582e; }
        .btn-danger { background: #d9534f; padding: 4px 8px; font-size: 0.85em; }

        table { width: 100%; border-collapse: collapse; margin-top: 10px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        th, td { padding: 10px 12px; border: 1px solid #ddd; text-align: left; }
        th { background-color: #0078d4; color: white; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        a.dl-link, a.title-link { color: #0078d4; text-decoration: none; font-weight: bold; cursor: pointer; }
        a.dl-link:hover, a.title-link:hover { text-decoration: underline; }
        code { background: #eee; padding: 2px 5px; border-radius: 3px; font-family: monospace; font-size: 0.9em; }

        .modal-overlay {
            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.5); z-index: 1000; justify-content: center; align-items: center;
        }
        .modal-content {
            background: white; padding: 25px; border-radius: 8px; width: 850px; max-width: 95%; max-height: 85vh;
            overflow-y: auto; box-shadow: 0 4px 12px rgba(0,0,0,0.3); position: relative;
        }
        .close-btn { position: absolute; top: 10px; right: 15px; font-size: 24px; cursor: pointer; font-weight: bold; color: #666; }
        .close-btn:hover { color: #000; }

        .detail-row { margin-bottom: 12px; border-bottom: 1px solid #eee; padding-bottom: 8px; }
        .detail-label { font-weight: bold; color: #0078d4; display: block; margin-bottom: 3px; }
        .detail-val { color: #333; line-height: 1.4; white-space: pre-wrap; }

        .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }
        .form-full { grid-column: span 2; }

        .cron-table-container {
            max-height: 250px;
            overflow-y: auto;
            border: 1px solid #ccc;
            margin-top: 10px;
        }
        .cron-table-container table { margin-top: 0; box-shadow: none; }
        .cron-table-container th { position: sticky; top: 0; z-index: 1; }

        #toastContainer {
            position: fixed; top: 20px; right: 20px; z-index: 2000; display: flex; flex-direction: column; gap: 10px;
        }
        .toast {
            min-width: 260px; padding: 14px 18px; border-radius: 6px; color: white; font-weight: 600;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15); display: flex; align-items: center; justify-content: space-between;
            animation: fadeIn 0.3s ease-in-out; font-size: 0.9em;
        }
        .toast-info { background: #0078d4; }
        .toast-success { background: #107c41; }
        .toast-error { background: #d9534f; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>

    <div id="toastContainer"></div>

    <div class="top-bar">
        <h1>Microsoft Server Updates</h1>
        <div style="display: flex; gap: 10px; align-items: center;">
            <button type="button" id="btnOpenSettings" class="btn btn-success">⚙️ Synology Mail & VM Cron Setup</button>
            <a href="/logout" class="btn btn-secondary" style="font-size: 0.85em;">Sign Out</a>
        </div>
    </div>

    <form method="GET" action="/" class="filter-card">
        <div class="filter-group" style="flex-grow: 1;">
            <label for="query">Catalog Search Query Filter:</label>
            <input type="text" id="query" name="query" value="{{ search_query }}" style="width: 100%;">
        </div>
        <div class="filter-group">
            <label for="start_date">Start Date:</label>
            <input type="date" id="start_date" name="start_date" value="{{ start_date }}">
        </div>
        <div class="filter-group">
            <label for="end_date">End Date:</label>
            <input type="date" id="end_date" name="end_date" value="{{ end_date }}">
        </div>
        <div style="margin-top: auto; display: flex; gap: 5px;">
            <button type="submit" class="btn">Apply Filter</button>
            <a href="/force-refresh?query={{ search_query | urlencode }}&start_date={{ start_date }}&end_date={{ end_date }}" class="btn btn-secondary">Force Refresh</a>
        </div>
    </form>

    <table>
        <tr>
            <th>Title</th>
            <th>Products</th>
            <th>Classification</th>
            <th>Last Updated</th>
            <th>Size</th>
            <th>File Name</th>
            <th>Download Link</th>
        </tr>
        {% for item in updates %}
        <tr>
            <td>
                {% if item.update_id %}
                <a class="title-link" data-uid="{{ item.update_id }}" data-title="{{ item.title | escape }}" data-classification="{{ item.classification | escape }}">{{ item.title }}</a>
                {% else %}
                {{ item.title }}
                {% endif %}
            </td>
            <td>{{ item.product }}</td>
            <td>{{ item.classification }}</td>
            <td>{{ item.last_updated }}</td>
            <td>{{ item.size }}</td>
            <td><code>{{ item.file_name }}</code></td>
            <td>
                {% if item.link != '#' %}
                <a href="{{ item.link }}" class="dl-link" target="_blank">Download Link</a>
                {% else %}
                N/A
                {% endif %}
            </td>
        </tr>
        {% else %}
        <tr>
            <td colspan="7">No updates found for the selected query and date range.</td>
        </tr>
        {% endfor %}
    </table>

    <div id="detailsModal" class="modal-overlay">
        <div class="modal-content">
            <span class="close-btn" id="closeDetailsBtn">&times;</span>
            <h2 id="modalTitle" style="color:#0078d4; margin-top:0;">Update Details</h2>
            <div id="detailsBody"><i>Loading overview...</i></div>
        </div>
    </div>

    <!-- Settings Modal -->
    <div id="settingsModal" class="modal-overlay">
        <div class="modal-content">
            <span class="close-btn" id="closeSettingsBtn">&times;</span>
            <h2 style="color:#0078d4; margin-top:0;">Synology Mail & VM Cron Setup</h2>
            <form id="settingsForm">
                <h3 style="margin-bottom:5px; border-bottom:1px solid #ccc; padding-bottom:5px;">1. SMTP Email Recipients & Default Filter</h3>
                <p style="font-size:0.85em; color:#666; margin:3px 0;">Host, Port, User & Sender credentials are securely loaded from <code>.env</code> file.</p>
                <div class="form-grid">
                    <div class="filter-group form-full">
                        <label>Recipient Email(s) (Comma-separated for multiple):</label>
                        <input type="text" id="smtp_recipient" required placeholder="admin@domain.com, it@domain.com">
                    </div>
                    <div class="filter-group form-full">
                        <label>Default Global Catalog Search Query:</label>
                        <input type="text" id="default_search_query" required placeholder="Microsoft server operating system version 24H2">
                    </div>
                </div>

                <h3 style="margin-top:20px; margin-bottom:5px; border-bottom:1px solid #ccc; padding-bottom:5px;">2. Add New Crontab Entry</h3>
                <div class="form-grid">
                    <div class="filter-group">
                        <label>Cron Expression Schedule:</label>
                        <input type="text" id="cron_schedule_input" placeholder="0 8 * * *">
                    </div>
                    <div class="filter-group">
                        <label>Email Mode Choice:</label>
                        <select id="email_mode">
                            <option value="each_new">All New Unnotified Updates</option>
                            <option value="last_3">Last 3 Latest Items</option>
                            <option value="this_month">This Current Month</option>
                            <option value="specific_month">Specific Month</option>
                        </select>
                    </div>
                    <div class="filter-group form-full">
                        <label>Catalog Filter Query for this Cronjob:</label>
                        <input type="text" id="cron_query_input" placeholder="Use Default Query or enter custom search text">
                    </div>
                    <div class="filter-group form-full" id="specific_month_group" style="display:none;">
                        <label>Target Month (M/YYYY or MM/YYYY):</label>
                        <input type="text" id="target_month" placeholder="e.g. 9/2026 or 09/2026">
                    </div>
                    <div class="filter-group form-full">
                        <button type="button" class="btn" id="btnAddCron">Add Cron Expression</button>
                    </div>
                </div>

                <h3 style="margin-top:20px; margin-bottom:5px; border-bottom:1px solid #ccc; padding-bottom:5px;">3. Linux Crontab Entries</h3>
                
                <div class="cron-table-container">
                    <table>
                        <thead>
                            <tr>
                                <th style="width:40px; text-align:center;">Select</th>
                                <th>Schedule</th>
                                <th>Query Filter</th>
                                <th>Email Mode</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody id="cronListTable">
                            <tr><td colspan="5">Loading crontab...</td></tr>
                        </tbody>
                    </table>
                </div>

                <div style="margin-top: 20px; display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap;">
                    <button type="button" class="btn btn-secondary" id="btnTestEmail">Send Test Email</button>
                    <button type="button" id="btnRunCronNow" class="btn btn-purple" disabled>Run Selected Cron Now</button>
                    <button type="submit" class="btn">Save Configuration</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Secure external application JavaScript -->
    <script src="/static/app.js"></script>
</body>
</html>
"""

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form.get('username')
        passwd = request.form.get('password')
        
        env_user = os.getenv("ADMIN_USER", "admin")
        env_pass = os.getenv("ADMIN_PASS", "admin")

        if user == env_user and passwd == env_pass:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template_string(LOGIN_TEMPLATE, error="Invalid Username or Password.")

    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    cfg = load_config()
    search_query = request.args.get('query', cfg.get('search_query', DEFAULT_SEARCH_QUERY))
    start_date, end_date = parse_date_params()
    updates = fetch_microsoft_catalog(search_query, start_date, end_date, force_refresh=False)
    return render_template_string(HTML_TEMPLATE, updates=updates, search_query=search_query, start_date=start_date.strftime("%Y-%m-%d"), end_date=end_date.strftime("%Y-%m-%d"))

@app.route('/force-refresh')
def force_refresh():
    cfg = load_config()
    search_query = request.args.get('query', cfg.get('search_query', DEFAULT_SEARCH_QUERY))
    start_date, end_date = parse_date_params()
    updates = fetch_microsoft_catalog(search_query, start_date, end_date, force_refresh=True)
    return render_template_string(HTML_TEMPLATE, updates=updates, search_query=search_query, start_date=start_date.strftime("%Y-%m-%d"), end_date=end_date.strftime("%Y-%m-%d"))

@app.route('/api/crons', methods=['GET'])
def get_crons():
    cfg = load_config()
    default_q = cfg.get('search_query', DEFAULT_SEARCH_QUERY)
    mode_labels = {
        "each_new": "All New Unnotified Updates",
        "last_3": "Last 3 Latest Items",
        "this_month": "This Current Month",
        "specific_month": "Specific Month"
    }
    try:
        cron = CronTab(user=True)
        jobs = []
        for idx, job in enumerate(cron):
            cmd = job.command
            mode = "each_new"
            month = ""
            query = default_q

            mode_match = re.search(r'mode=([a-z0-9_]+)', cmd)
            if mode_match:
                mode = mode_match.group(1)

            month_match = re.search(r'month=([0-9/]+)', cmd)
            if month_match:
                month = month_match.group(1)

            query_match = re.search(r'query=([^&">]+)', cmd)
            if query_match:
                query = requests.utils.unquote(query_match.group(1))

            schedule_part = " ".join(str(job).split()[:5])

            jobs.append({
                "id": idx,
                "schedule": schedule_part,
                "mode": mode,
                "mode_display": mode_labels.get(mode, mode),
                "month": month if mode == "specific_month" else "",
                "query": query
            })
        return jsonify(jobs)
    except Exception as e:
        return jsonify([])

@app.route('/api/crons', methods=['POST'])
def add_cron():
    try:
        cfg = load_config()
        data = request.json or {}
        raw_input = data.get('schedule', '0 8 * * *').strip()
        mode = data.get('mode', 'each_new')
        month = data.get('month', '')
        query = data.get('query', cfg.get('search_query', DEFAULT_SEARCH_QUERY)).strip()

        parts = raw_input.split()
        schedule = " ".join(parts[:5]) if len(parts) >= 5 else raw_input

        encoded_query = requests.utils.quote(query)

        cron = CronTab(user=True)
        cmd = f"/usr/bin/curl -s \"http://127.0.0.1:5000/cron/check-updates?mode={mode}&month={month}&query={encoded_query}\" > /dev/null 2>&1"
        job = cron.new(command=cmd, comment="ms_update_checker")
        job.setall(schedule)
        cron.write()
        
        return jsonify({"status": "success", "line": str(job)})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/crons/<int:job_id>', methods=['DELETE'])
def delete_cron(job_id):
    try:
        cron = CronTab(user=True)
        jobs = list(cron)
        if 0 <= job_id < len(jobs):
            cron.remove(jobs[job_id])
            cron.write()
            return jsonify({"status": "success"})
        return jsonify({"error": "Job not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    cfg = load_config()
    if request.method == 'GET':
        return jsonify(cfg)
    
    data = request.json or {}
    cfg['recipients'] = data.get('recipients', cfg['recipients'])
    cfg['search_query'] = data.get('search_query', cfg['search_query'])
    save_config(cfg)

    return jsonify({"status": "success", "message": "Settings updated successfully!"})

@app.route('/api/test-email', methods=['POST'])
def test_email():
    success, msg = send_smtp_email(
        "Test Notification - Microsoft Server Update Checker",
        "<h3>SMTP Connection Successful!</h3><p>Your Flask application can send emails to all configured recipients.</p>"
    )
    return jsonify({"status": "success" if success else "error", "message": msg})

@app.route('/cron/check-updates')
def cron_check_updates():
    cfg = load_config()
    mode = request.args.get('mode', 'each_new')
    target_month = request.args.get('month', '').strip()
    query_filter = request.args.get('query', cfg.get('search_query', DEFAULT_SEARCH_QUERY))

    now = datetime.now()
    start_date = datetime(2020, 1, 1)
    end_date = datetime(2030, 12, 31, 23, 59, 59)

    latest_updates = fetch_microsoft_catalog(query_filter, start_date, end_date, force_refresh=True)
    latest_updates.sort(key=lambda x: parse_catalog_date(x['last_updated']), reverse=True)

    notified_ids = set(cfg.get("notified_ids", []))
    selected_items = []
    subject = ""

    if mode == "each_new":
        unnotified_items = []
        for item in latest_updates:
            uid = item.get('update_id') or item['title']
            if uid not in notified_ids:
                notified_ids.add(uid)
                unnotified_items.append(item)
        
        if unnotified_items:
            cfg['notified_ids'] = list(notified_ids)
            save_config(cfg)
            selected_items = unnotified_items
            subject = f"Microsoft Updates Alert: {len(selected_items)} New Update(s) Available for '{query_filter}'"

    elif mode == "last_3":
        selected_items = latest_updates[:3]
        subject = f"Microsoft Updates Report: 3 Latest Updates for '{query_filter}'"

    elif mode == "this_month":
        for item in latest_updates:
            item_dt = parse_catalog_date(item['last_updated'])
            if item_dt != datetime.min and item_dt.month == now.month and item_dt.year == now.year:
                selected_items.append(item)
        subject = f"Microsoft Updates Report: {now.strftime('%B %Y')} Updates for '{query_filter}'"

    elif mode == "specific_month":
        target_m, target_y = None, None
        if "/" in target_month:
            parts = target_month.split("/")
            try:
                target_m = int(parts[0])
                target_y = int(parts[1])
            except ValueError:
                pass

        for item in latest_updates:
            item_dt = parse_catalog_date(item['last_updated'])
            if target_m and target_y:
                if item_dt.month == target_m and item_dt.year == target_y:
                    selected_items.append(item)
            elif target_month in item['last_updated']:
                selected_items.append(item)
        subject = f"Microsoft Updates Report: {target_month} Updates for '{query_filter}'"

    selected_items.sort(key=lambda x: parse_catalog_date(x['last_updated']), reverse=True)

    if selected_items:
        if mode in ("last_3", "each_new"):
            for item in selected_items:
                uid = item.get('update_id')
                try:
                    item['details'] = fetch_update_overview_details(uid)
                except Exception:
                    item['details'] = {"Description": "N/A", "More Information": "N/A", "Restart behavior": "N/A"}

        email_body = build_email_html(selected_items, query_filter, detailed=(mode in ("last_3", "each_new")))
        send_smtp_email(subject, email_body)
        return jsonify({"status": "success", "found_new": len(selected_items)})

    return jsonify({"status": "success", "found_new": 0})

@app.route('/details/<update_id>')
def get_details(update_id):
    classification = request.args.get('classification', 'N/A')
    details = fetch_update_overview_details(update_id)
    details["Classification"] = classification
    return jsonify(details)

def build_email_html(items, query_filter, detailed=True):
    if detailed:
        cards_html = ""
        for idx, item in enumerate(items):
            details = item.get('details', {})
            description = details.get('Description', 'N/A')
            more_info = details.get('More Information', 'N/A')
            more_info_link = f'<a href="{more_info}" target="_blank" style="color:#0078d4;">{more_info}</a>' if more_info != 'N/A' else 'N/A'
            restart = details.get('Restart behavior', 'N/A')
            dl_link = f'<a href="{item["link"]}" target="_blank" style="color:#ffffff; background-color:#107c41; padding:6px 12px; text-decoration:none; border-radius:4px; font-weight:bold; display:inline-block;">Download Package</a>' if item["link"] != "#" else "N/A"

            cards_html += f"""
            <div style="background:#ffffff; border:1px solid #e0e0e0; border-radius:6px; padding:18px; margin-bottom:20px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                <h3 style="margin-top:0; color:#0078d4; font-size:1.1em; border-bottom:1px solid #eee; padding-bottom:8px;">{item['title']}</h3>
                
                <table style="width:100%; border-collapse:collapse; font-size:0.9em; margin-bottom:12px;">
                    <tr>
                        <td style="padding:4px 0; color:#555; font-weight:bold; width:130px;">Classification:</td>
                        <td style="padding:4px 0; color:#333;">{item['classification']}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0; color:#555; font-weight:bold;">Products:</td>
                        <td style="padding:4px 0; color:#333;">{item['product']}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0; color:#555; font-weight:bold;">Last Updated:</td>
                        <td style="padding:4px 0; color:#333;">{item['last_updated']}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0; color:#555; font-weight:bold;">Package Size:</td>
                        <td style="padding:4px 0; color:#333;">{item['size']}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0; color:#555; font-weight:bold;">File Name:</td>
                        <td style="padding:4px 0;"><code style="background:#eee; padding:2px 5px; border-radius:3px; font-family:monospace;">{item['file_name']}</code></td>
                    </tr>
                </table>

                <div style="background:#f8f9fa; border-left:4px solid #0078d4; padding:12px; margin-bottom:12px; font-size:0.88em;">
                    <p style="margin:0 0 6px 0; color:#333;"><strong>Description:</strong> {description}</p>
                    <p style="margin:0 0 6px 0; color:#333;"><strong>More Information:</strong> {more_info_link}</p>
                    <p style="margin:0; color:#333;"><strong>Restart behavior:</strong> {restart}</p>
                </div>

                <div>{dl_link}</div>
            </div>
            """
        content_block = cards_html
    else:
        rows_html = ""
        for item in items:
            dl_link = f'<a href="{item["link"]}" target="_blank" style="color:#0078d4; font-weight:bold;">Download</a>' if item["link"] != "#" else "N/A"
            rows_html += f"""
            <tr>
                <td style="padding:8px 10px; border:1px solid #ddd; font-weight:bold; color:#0078d4;">{item['title']}</td>
                <td style="padding:8px 10px; border:1px solid #ddd;">{item['product']}</td>
                <td style="padding:8px 10px; border:1px solid #ddd;">{item['classification']}</td>
                <td style="padding:8px 10px; border:1px solid #ddd;">{item['last_updated']}</td>
                <td style="padding:8px 10px; border:1px solid #ddd;">{item['size']}</td>
                <td style="padding:8px 10px; border:1px solid #ddd;"><code style="background:#eee; padding:2px 4px; border-radius:3px;">{item['file_name']}</code></td>
                <td style="padding:8px 10px; border:1px solid #ddd;">{dl_link}</td>
            </tr>
            """
        content_block = f"""
        <table style="width:100%; border-collapse:collapse; background:#ffffff; font-size:0.88em;">
            <thead>
                <tr style="background-color:#0078d4; color:#ffffff;">
                    <th style="padding:10px; border:1px solid #ddd; text-align:left;">Title</th>
                    <th style="padding:10px; border:1px solid #ddd; text-align:left;">Products</th>
                    <th style="padding:10px; border:1px solid #ddd; text-align:left;">Classification</th>
                    <th style="padding:10px; border:1px solid #ddd; text-align:left;">Last Updated</th>
                    <th style="padding:10px; border:1px solid #ddd; text-align:left;">Size</th>
                    <th style="padding:10px; border:1px solid #ddd; text-align:left;">File Name</th>
                    <th style="padding:10px; border:1px solid #ddd; text-align:left;">Download Link</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; background-color: #f4f4f9; padding: 20px; margin: 0;">
        <div style="max-width:900px; margin:0 auto;">
            <h1 style="color: #333; margin-bottom:4px;">Microsoft Server Updates</h1>
            <p style="color: #666; font-size: 0.95em; margin-top:0; margin-bottom:20px;">Catalog Query Filter: <strong>{query_filter}</strong></p>
            {content_block}
        </div>
    </body>
    </html>
    """

def send_smtp_email(subject, html_content):
    smtp_info = get_smtp_credentials()
    cfg = load_config()

    if not smtp_info["server"] or not cfg["recipients"]:
        print("[SMTP ABORT] Missing server or recipient configuration.", flush=True)
        return False, "SMTP configuration incomplete. Check .env and recipient list."

    recipient_list = [r.strip() for r in cfg["recipients"].split(",") if r.strip()]

    msg = MIMEMultipart()
    msg['From'] = smtp_info["sender"]
    msg['To'] = ", ".join(recipient_list)
    msg['Subject'] = subject
    msg.attach(MIMEText(html_content, 'html'))

    try:
        with smtplib.SMTP(smtp_info["server"], smtp_info["port"], timeout=15) as server:
            if smtp_info["port"] == 587:
                server.starttls()
            if smtp_info["user"] and smtp_info["password"]:
                server.login(smtp_info["user"], smtp_info["password"])
            server.send_message(msg)
        return True, "Email sent successfully!"
    except Exception as e:
        print(f"[SMTP ERROR] {e}", flush=True)
        return False, str(e)

def fetch_microsoft_catalog(query_filter, start_date_obj, end_date_obj, force_refresh=False):
    results_map = {}

    if not force_refresh:
        cached_items = db_get_updates(query_filter)
        if cached_items:
            filtered_cached = []
            for item in cached_items:
                item_date = parse_catalog_date(item['last_updated'])
                if start_date_obj <= item_date <= end_date_obj:
                    filtered_cached.append(item)
            filtered_cached.sort(key=lambda x: parse_catalog_date(x['last_updated']), reverse=True)
            return filtered_cached

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36")
        page = context.new_page()

        encoded_query = requests.utils.quote(query_filter)
        search_url = f"https://www.catalog.update.microsoft.com/Search.aspx?q={encoded_query}&scol=DateComputed&sdir=desc"

        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#ctl00_catalogBody_updateMatches", timeout=20000)

            rows = page.locator("#ctl00_catalogBody_updateMatches tr").all()

            for i in range(1, len(rows)):
                cols = rows[i].locator("td").all()
                if len(cols) < 7:
                    continue

                last_updated_str = cols[4].inner_text().strip()

                title = cols[1].inner_text().strip()
                product = cols[2].inner_text().strip()
                classification = cols[3].inner_text().strip()
                size = cols[6].inner_text().strip() if len(cols) > 6 else "N/A"

                file_name = "N/A"
                download_link = "#"
                update_id = None

                btn = cols[7].locator("input")
                if btn.count() > 0:
                    btn_id = btn.get_attribute("id") or ""
                    btn_onclick = btn.get_attribute("onclick") or ""
                    
                    # Extract GUID from ID or onclick handler
                    update_id_match = re.search(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', btn_id + " " + btn_onclick, re.I)
                    if update_id_match:
                        update_id = update_id_match.group(0)

                # Fallback GUID search inside row HTML if button attribute check missed it
                if not update_id:
                    row_html = rows[i].inner_html()
                    guid_match = re.search(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', row_html, re.I)
                    if guid_match:
                        update_id = guid_match.group(0)

                # Fetch download URL dialog if update_id was successfully discovered
                if update_id:
                    try:
                        dialog_url = f"https://www.catalog.update.microsoft.com/DownloadDialog.aspx?updateIDs=[{{\"size\":0,\"languages\":\"\",\"uidInfo\":\"{update_id}\",\"updateID\":\"{update_id}\"}}]"
                        dlg_res = context.request.get(dialog_url)
                        dlg_text = dlg_res.text()
                        
                        urls = re.findall(r'http[s]?://download\.windowsupdate\.com[^\'"\s>]+', dlg_text)
                        if not urls:
                            urls = re.findall(r'downloadInformation\[\d+\]\.files\[\d+\]\.url\s*=\s*[\'"](http[s]?://[^\'"]+)[\'"]', dlg_text)
                        
                        if urls:
                            download_link = urls[0]
                            file_name = download_link.split('/')[-1]
                    except Exception as err:
                        print(f"[DOWNLOAD URL FETCH WARNING] {update_id}: {err}", flush=True)

                key = update_id if update_id else title
                results_map[key] = {
                    "update_id": update_id,
                    "title": title,
                    "product": product,
                    "classification": classification,
                    "last_updated": last_updated_str,
                    "size": size,
                    "file_name": file_name,
                    "link": download_link
                }

            items_list = list(results_map.values())
            db_save_updates(items_list, query_filter)

            filtered_scraped = []
            for item in items_list:
                item_date = parse_catalog_date(item['last_updated'])
                if start_date_obj <= item_date <= end_date_obj:
                    filtered_scraped.append(item)
            
            filtered_scraped.sort(key=lambda x: parse_catalog_date(x['last_updated']), reverse=True)
            return filtered_scraped

        except Exception as e:
            print(f"[PLAYWRIGHT ERROR] {e}", flush=True)

        browser.close()

    return []

def parse_date_params():
    now = datetime.now()
    default_start = datetime(now.year, now.month, 1)
    default_end = datetime(now.year, now.month, now.day, 23, 59, 59)

    start_param = request.args.get('start_date')
    end_param = request.args.get('end_date')

    try:
        start_date = datetime.strptime(start_param, "%Y-%m-%d") if start_param else default_start
    except ValueError:
        start_date = default_start

    try:
        end_date = datetime.strptime(end_param, "%Y-%m-%d") if end_param else default_end
        end_date = end_date.replace(hour=23, minute=59, second=59)
    except ValueError:
        end_date = default_end

    return start_date, end_date

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)