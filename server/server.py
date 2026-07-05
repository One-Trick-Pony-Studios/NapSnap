from flask import Flask, send_from_directory, render_template_string, redirect, request, jsonify
import pychromecast
import requests
import logging
from logging.handlers import RotatingFileHandler
import pytz
from datetime import datetime
import time
import threading

app = Flask(__name__)

# --- CONFIGURATION ---
SERVER_IP = "192.168.0.203" # Replace with your BeagleBone IP
MINI_IP = "192.168.0.137"
MINI_NAME = "Family Room speaker" 
ESP_IP = None                  # Dynamically assigned by ESP8266
IS_ARMED = False               # Global state tracker
CURRENT_VOLUME = 0.5
LAST_HEARTBEAT_TIME = 0
HEARTBEAT_TIMEOUT = 660  # Seconds before we consider the ESP offline

state_lock = threading.RLock()

def get_system_status():
    """Returns 'OFFLINE' if we haven't heard from the ESP in HEARTBEAT_TIMEOUT, else returns the armed state."""
    with state_lock:
        if (time.time() - LAST_HEARTBEAT_TIME) > HEARTBEAT_TIMEOUT:
            return "OFFLINE"
        return "ARMED" if IS_ARMED else "DISARMED"

# --- CONFIGURATION ---
IST = pytz.timezone('Asia/Kolkata')

# Configure logging
logger = logging.getLogger("BabyMonitor")
logger.setLevel(logging.INFO)
# Rotate logs: Max 500KB per file, keep 3 backup files
handler = RotatingFileHandler('baby_monitor.log', maxBytes=500000, backupCount=3)

# Force logging to use IST
def ist_converter(*args):
    return datetime.now(IST).timetuple()

formatter = logging.Formatter('%(asctime)s - %(message)s')
formatter.converter = ist_converter # Apply the IST converter

handler.setFormatter(formatter)
logger.addHandler(handler)

# Memory store for the last 20 logs to display on the dashboard
recent_logs = []

def add_log(message):
    timestamp = datetime.now(IST).strftime('%H:%M:%S')
    log_entry = f"[{timestamp}] {message}"
    
    with state_lock:
        # Add to memory for dashboard (Top 20)
        recent_logs.insert(0, log_entry)
        if len(recent_logs) > 20:
            recent_logs.pop()
    
    # Write to rotated file
    logger.info(message)

def cast_audio(filename):
    """Launches audio casting in a background thread to prevent blocking the Flask server."""
    threading.Thread(target=_cast_audio_worker, args=(filename,), daemon=True).start()

def _cast_audio_worker(filename):
    """Casts audio using Direct IP (known_hosts) or falls back to mDNS."""
    browser = None
    try:
        # By passing known_hosts, PyChromecast bypasses the mDNS broadcast and directly polls the IP
        if MINI_IP:
            chromecasts, browser = pychromecast.get_listed_chromecasts(friendly_names=[MINI_NAME], known_hosts=[MINI_IP])
        else:
            chromecasts, browser = pychromecast.get_listed_chromecasts(friendly_names=[MINI_NAME])

        if not chromecasts:
            add_log("CAST ERROR: Google Mini not found on the network.")
            return

        cast = chromecasts[0]
        cast.wait()

        # Retrieve dynamic volume under lock
        with state_lock:
            vol = CURRENT_VOLUME

        cast.set_volume(vol)

        mc = cast.media_controller
        mp3_url = f"http://{SERVER_IP}:5000/static/{filename}"

        mc.play_media(mp3_url, 'audio/mp3')
        mc.block_until_active()

    except Exception as e:
        add_log(f"CAST ERROR: Failed to connect to speaker. ({e})")

    finally:
        # The finally block guarantees the browser is stopped, even if the connection crashes halfway through
        if browser:
            pychromecast.discovery.stop_discovery(browser)

# --- HTML DASHBOARD TEMPLATE ---
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Baby Monitor Hub</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial; text-align: center; margin-top: 50px; background-color: #f4f4f4;}
        .status { font-size: 28px; font-weight: bold; margin-bottom: 20px; padding: 10px; border-radius: 5px; display: inline-block; }
        .status-armed { background-color: #d4edda; color: #155724; border: 2px solid #c3e6cb; }
        .status-disarmed { background-color: #f8d7da; color: #721c24; border: 2px solid #f5c6cb; }
        .status-offline { background-color: #6c757d; color: white; border: 2px solid #5a6268; }
        .btn { padding: 20px 40px; font-size: 24px; margin: 10px; border: none; border-radius: 10px; color: white; text-decoration: none; display: inline-block; cursor: pointer;}
        .btn-arm { background-color: #28a745; }
        .btn-disarm { background-color: #dc3545; }
        .btn:disabled { background-color: #cccccc; color: #666666; cursor: not-allowed; opacity: 0.6; }
        .log-box { width: 80%; max-width: 500px; margin: 30px auto; background: white; padding: 20px; text-align: left; border-radius: 8px; box-shadow: 0px 0px 10px #ccc;}
        .control-box { margin-bottom: 20px; }
    </style>
</head>
<body>
    <h1>Baby Monitor Control</h1>

    <div class="control-box">
        <h3>Alert Volume: <span id="vol-display">{{ (current_volume * 100)|int }}</span>%</h3>
        <input type="range" id="vol-slider" min="0" max="100" value="{{ (current_volume * 100)|int }}" style="width: 80%;" onchange="updateVolume(this.value)">
    </div>
    
    <div id="status-container" class="status">
        CURRENT STATUS: LOADING...
    </div>
    
    <br>
    <div id="offline-note" style="color: #6c757d; font-style: italic; display: none;">
        Control unavailable while system is OFFLINE.
    </div>
    
    <div id="control-buttons">
        <button id="btn-arm" onclick="sendCommand('arm')" class="btn btn-arm">ARM SYSTEM</button>
        <button id="btn-disarm" onclick="sendCommand('disarm')" class="btn btn-disarm">DISARM SYSTEM</button>
    </div>
    
    <div class="log-box">
        <h3>Recent System Logs:</h3>
        <a href="/logs">View Full Archived Logs</a>
        <ul id="log-list">
            {% for log in logs %}
                <li>{{ log }}</li>
            {% endfor %}
        </ul>
    </div>

    <script>
        function updateUI(data) {
            var statusContainer = document.getElementById('status-container');
            var offlineNote = document.getElementById('offline-note');
            var btnArm = document.getElementById('btn-arm');
            var btnDisarm = document.getElementById('btn-disarm');
            var volDisplay = document.getElementById('vol-display');
            var volSlider = document.getElementById('vol-slider');
            var logList = document.getElementById('log-list');

            // Update status class and text
            statusContainer.innerText = "CURRENT STATUS: " + data.status;
            statusContainer.className = "status";
            if (data.status === "OFFLINE") {
                statusContainer.classList.add("status-offline");
                offlineNote.style.display = "block";
                btnArm.disabled = true;
                btnDisarm.disabled = true;
            } else if (data.status === "ARMED") {
                statusContainer.classList.add("status-armed");
                offlineNote.style.display = "none";
                btnArm.disabled = true;  // Gray out ARM
                btnDisarm.disabled = false;
            } else {
                statusContainer.classList.add("status-disarmed");
                offlineNote.style.display = "none";
                btnArm.disabled = false;
                btnDisarm.disabled = true; // Gray out DISARM
            }

            // Update volume display and slider if not dragging
            volDisplay.innerText = data.current_volume;
            if (document.activeElement !== volSlider) {
                volSlider.value = data.current_volume;
            }

            // Update logs using ES5 loop
            var logHTML = "";
            if (data.logs) {
                for (var i = 0; i < data.logs.length; i++) {
                    logHTML += "<li>" + data.logs[i] + "</li>";
                }
            }
            logList.innerHTML = logHTML;
        }

        function fetchStatus() {
            fetch('/api/status')
                .then(function(res) { return res.json(); })
                .then(function(data) { updateUI(data); })
                .catch(function(err) { console.error("Error fetching status:", err); });
        }

        function sendCommand(action) {
            var btnArm = document.getElementById('btn-arm');
            var btnDisarm = document.getElementById('btn-disarm');
            // Disable immediately to prevent double submission
            btnArm.disabled = true;
            btnDisarm.disabled = true;

            fetch('/command/' + action)
                .then(function(res) {
                    fetchStatus();
                })
                .catch(function(err) {
                    console.error("Error sending command:", err);
                    fetchStatus();
                });
        }

        function updateVolume(val) {
            var formData = new URLSearchParams();
            formData.append('volume', val);

            fetch('/set_volume', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                body: formData
            })
            .then(function(res) { fetchStatus(); })
            .catch(function(err) { console.error("Error setting volume:", err); });
        }

        // Poll every 5 seconds for updates
        setInterval(fetchStatus, 5000);
        // Run initial update
        fetchStatus();
    </script>
</body>
</html>
"""

@app.route('/register', methods=['GET'])
def register_esp():
    global ESP_IP, IS_ARMED, LAST_HEARTBEAT_TIME
    
    incoming_ip = request.args.get('ip')
    incoming_state = request.args.get('state') # Get the state from ESP
    
    if incoming_ip:
        with state_lock:
            # Update the heartbeat whenever the ESP talks to us
            LAST_HEARTBEAT_TIME = time.time()
            
            old_ip = ESP_IP
            old_state = IS_ARMED
            incoming_state_bool = (incoming_state == 'arm') if incoming_state else IS_ARMED
            
            ip_changed = (old_ip != incoming_ip)
            state_changed = (old_state != incoming_state_bool)
            
            ESP_IP = incoming_ip
            IS_ARMED = incoming_state_bool

        if ip_changed or state_changed or not old_ip:
            # Sync or registration event: log to dashboard and rotate log
            add_log(f"ESP8266 registered. IP: {incoming_ip}, State synced: {incoming_state.upper() if incoming_state else 'N/A'}")
        else:
            # Regular keepalive: write only to rotated log file (not dashboard) to minimize noise
            logger.info(f"ESP8266 heartbeat keepalive from IP {incoming_ip}.")
        
        return "IP and State Registered", 200
    return "Missing parameters", 400

# --- API ENDPOINT FOR AJAX STATUS ---
@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({
        "status": get_system_status(),
        "is_armed": IS_ARMED,
        "current_volume": int(CURRENT_VOLUME * 100),
        "logs": recent_logs
    })

# --- WEB UI ROUTE ---
@app.route('/')
def dashboard():
    with state_lock:
        logs_copy = list(recent_logs)
        vol_copy = CURRENT_VOLUME
    return render_template_string(HTML_PAGE, logs=logs_copy, current_volume=vol_copy)

@app.route('/logs')
def view_all_logs():
    try:
        with open('baby_monitor.log', 'r') as f:
            all_logs = f.readlines()[::-1]
        
        log_html = """
        <html>
        <body style="font-family: monospace; padding: 20px;">
            <h1>Full System Logs</h1>
            <a href="/">Back to Dashboard</a>
            <hr>
            <pre>""" + "".join(all_logs) + """</pre>
        </body>
        </html>
        """
        return log_html
    except FileNotFoundError:
        return "No logs found yet."

@app.route('/set_volume', methods=['POST'])
def set_volume():
    global CURRENT_VOLUME
    new_vol = int(request.form.get('volume')) / 100.0
    with state_lock:
        CURRENT_VOLUME = new_vol
    add_log(f"Volume updated to {int(new_vol * 100)}%")
    return "Volume updated", 200

@app.route('/command/<action>')
def send_command(action):
    global ESP_IP, IS_ARMED
    
    with state_lock:
        esp_ip_copy = ESP_IP
    
    if not esp_ip_copy:
        add_log("ERROR: Cannot send command. ESP8266 has not registered its IP yet.")
        return "No IP registered", 400
        
    try:
        response = requests.get(f"http://{esp_ip_copy}/{action}", timeout=3)
        
        if response.status_code == 200:
            with state_lock:
                LAST_HEARTBEAT_TIME = time.time()
                if action == 'arm':
                    IS_ARMED = True
                elif action == 'disarm':
                    IS_ARMED = False
            
            if action == 'arm':
                add_log("System successfully ARMED via Web UI.")
                cast_audio("ding.mp3") 
            elif action == 'disarm':
                add_log("System successfully DISARMED via Web UI.")
                cast_audio("dong.mp3") 
            return "Command processed", 200
        else:
            add_log(f"ERROR: ESP8266 returned status {response.status_code}.")
            return f"ESP error: {response.status_code}", 500
            
    except Exception as e:
        add_log(f"CRITICAL ERROR: Could not reach ESP8266 at {esp_ip_copy}.")
        return "ESP unreachable", 500

# --- RADAR TRIGGER ROUTE ---
@app.route('/trigger', methods=['GET'])
def trigger_alert():
    global ESP_IP, LAST_HEARTBEAT_TIME, IS_ARMED
    
    incoming_ip = request.remote_addr
    
    with state_lock:
        # Update heartbeat time to resolve reboot offline state bug
        LAST_HEARTBEAT_TIME = time.time()
        
        # ESP sent a wakeup alert, so it is armed
        IS_ARMED = True
        
        ip_changed = (ESP_IP != incoming_ip)
        ESP_IP = incoming_ip
        
    if ip_changed:
        add_log(f"Auto-healed connection: ESP8266 found at {incoming_ip}")

    add_log("WAKEUP DETECTED: Movement triggered the radar!")
    cast_audio("chime.mp3") 
    return "Alert logged and casted", 200

# --- STATIC FILE SERVER ---
@app.route('/static/<filename>')
def serve_audio(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    add_log("BeagleBone Hub Booted. Waiting for ESP8266...")
    app.run(host='0.0.0.0', port=5000)
