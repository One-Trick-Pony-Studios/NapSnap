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

# --- RADAR CALIBRATION GLOBALS ---
RADAR_MAX_GATE = 4
RADAR_TIMEOUT = 5
MOVING_GATES = [60, 60, 50, 40, 40, 40, 40, 40, 40]
STATIC_GATES = [100, 100, 100, 100, 100, 100, 100, 100, 100]

state_lock = threading.RLock()

def get_system_status():
    """Returns 'OFFLINE' if we haven't heard from the ESP in HEARTBEAT_TIMEOUT, else returns the armed state."""
    with state_lock:
        if (time.time() - LAST_HEARTBEAT_TIME) > HEARTBEAT_TIMEOUT:
            return "OFFLINE"
        return "ARMED" if IS_ARMED else "DISARMED"

# --- TIMEZONE & LOGGING CONFIGURATION ---
IST = pytz.timezone('Asia/Kolkata')

logger = logging.getLogger("BabyMonitor")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler('baby_monitor.log', maxBytes=500000, backupCount=3)

def ist_converter(*args):
    return datetime.now(IST).timetuple()

formatter = logging.Formatter('%(asctime)s - %(message)s')
formatter.converter = ist_converter

handler.setFormatter(formatter)
logger.addHandler(handler)

recent_logs = []

def add_log(message):
    timestamp = datetime.now(IST).strftime('%H:%M:%S')
    log_entry = f"[{timestamp}] {message}"
    
    with state_lock:
        recent_logs.insert(0, log_entry)
        if len(recent_logs) > 20:
            recent_logs.pop()
    
    logger.info(message)

def cast_audio(filename):
    """Launches audio casting in a background thread to prevent blocking the Flask server."""
    threading.Thread(target=_cast_audio_worker, args=(filename,), daemon=True).start()

def _cast_audio_worker(filename):
    """Casts audio using Direct IP (known_hosts) or falls back to mDNS."""
    browser = None
    try:
        if MINI_IP:
            chromecasts, browser = pychromecast.get_listed_chromecasts(friendly_names=[MINI_NAME], known_hosts=[MINI_IP])
        else:
            chromecasts, browser = pychromecast.get_listed_chromecasts(friendly_names=[MINI_NAME])

        if not chromecasts:
            add_log("CAST ERROR: Google Mini not found on the network.")
            return

        cast = chromecasts[0]
        cast.wait()

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
        if browser:
            pychromecast.discovery.stop_discovery(browser)

# --- HTML DASHBOARD TEMPLATE ---
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>NapSnap | Baby Monitor Hub</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; text-align: center; margin: 0; padding: 20px; background-color: #f4f6f9;}
        .navbar { background: #343a40; padding: 12px 20px; border-radius: 8px; margin-bottom: 25px; display: flex; justify-content: space-between; align-items: center; text-align: left; }
        .navbar .brand { color: #fff; font-size: 20px; font-weight: bold; }
        .nav-links a { color: #e2e8f0; text-decoration: none; font-weight: bold; margin-left: 15px; padding: 6px 12px; border-radius: 4px; transition: background 0.2s; }
        .nav-links a:hover { background: #495057; }
        .nav-links a.active { background: #007bff; color: white; }
        .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }
        .status { font-size: 28px; font-weight: bold; margin-bottom: 20px; padding: 10px; border-radius: 5px; display: inline-block; }
        .status-armed { background-color: #d4edda; color: #155724; border: 2px solid #c3e6cb; }
        .status-disarmed { background-color: #f8d7da; color: #721c24; border: 2px solid #f5c6cb; }
        .status-offline { background-color: #6c757d; color: white; border: 2px solid #5a6268; }
        .btn { padding: 20px 40px; font-size: 24px; margin: 10px; border: none; border-radius: 10px; color: white; text-decoration: none; display: inline-block; cursor: pointer;}
        .btn-arm { background-color: #28a745; }
        .btn-disarm { background-color: #dc3545; }
        .btn:disabled { background-color: #cccccc; color: #666666; cursor: not-allowed; opacity: 0.6; }
        .log-box { width: 90%; max-width: 500px; margin: 30px auto 0 auto; background: #f8fafc; padding: 20px; text-align: left; border-radius: 8px; border: 1px solid #e2e8f0; box-shadow: 0px 0px 10px #eee;}
        .control-box { margin-bottom: 20px; background: #f8fafc; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0; }
    </style>
</head>
<body>
    <div class="container">
        <div class="navbar">
            <span class="brand">⚡ NapSnap Hub</span>
            <div class="nav-links">
                <a href="/" class="active">Main Dashboard</a>
                <a href="/calibration">Radar Tuning</a>
                <a href="/logs">Full Logs</a>
            </div>
        </div>

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
                btnArm.disabled = true;
                btnDisarm.disabled = false;
            } else {
                statusContainer.classList.add("status-disarmed");
                offlineNote.style.display = "none";
                btnArm.disabled = false;
                btnDisarm.disabled = true;
            }

            volDisplay.innerText = data.current_volume;
            if (document.activeElement !== volSlider) {
                volSlider.value = data.current_volume;
            }

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

        setInterval(fetchStatus, 5000);
        fetchStatus();
    </script>
</body>
</html>
"""

# --- HTML RADAR CALIBRATION TEMPLATE ---
CALIBRATION_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>NapSnap | Radar Calibration Hub</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; background: #f4f6f9; margin: 0; padding: 20px; color: #333; }
        .navbar { background: #343a40; padding: 12px 20px; border-radius: 8px; margin-bottom: 25px; display: flex; justify-content: space-between; align-items: center; text-align: left; }
        .navbar .brand { color: #fff; font-size: 20px; font-weight: bold; }
        .nav-links a { color: #e2e8f0; text-decoration: none; font-weight: bold; margin-left: 15px; padding: 6px 12px; border-radius: 4px; transition: background 0.2s; }
        .nav-links a:hover { background: #495057; }
        .nav-links a.active { background: #007bff; color: white; }
        .container { max-width: 950px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }
        h1, h2 { color: #1e293b; margin-top: 0; }
        .offline-banner { background-color: #fee2e2; color: #991b1b; border: 1px solid #f87171; padding: 15px; border-radius: 8px; font-weight: bold; font-size: 16px; margin-bottom: 25px; text-align: center; }
        .grid-row { display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }
        .card { flex: 1; min-width: 250px; background: #f8fafc; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0; }
        
        /* Live Engineering Graph Styles */
        .live-viz { background: #0f172a; color: #f8fafc; padding: 20px; border-radius: 10px; margin-bottom: 25px; box-shadow: inset 0 2px 10px rgba(0,0,0,0.5); }
        .viz-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
        .target-pill { padding: 6px 14px; border-radius: 20px; font-weight: bold; font-size: 13px; background: #334155; color: #94a3b8; }
        .target-active { background: #22c55e; color: #052e16; }
        .gate-viz-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 13px; }
        .gate-label { width: 90px; font-family: monospace; font-weight: bold; color: #cbd5e1; }
        .bar-container { flex: 1; background: #1e293b; height: 18px; border-radius: 4px; overflow: hidden; position: relative; display: flex; }
        .bar-fill-moving { height: 100%; background: linear-gradient(90deg, #16a34a, #22c55e); width: 0%; transition: width 0.2s; }
        .bar-fill-static { height: 100%; background: linear-gradient(90deg, #2563eb, #3b82f6); width: 0%; transition: width 0.2s; }
        .threshold-line { position: absolute; top: 0; bottom: 0; width: 2px; background: #ef4444; z-index: 10; }
        
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }
        th { background: #f1f5f9; color: #475569; }
        .slider-group { display: flex; align-items: center; gap: 10px; }
        input[type="range"] { flex: 1; accent-color: #28a745; cursor: pointer; }
        .static-slider input[type="range"] { accent-color: #007bff; }
        .value-lbl { font-family: monospace; font-weight: bold; width: 30px; display: inline-block; text-align: right; }
        .btn-submit { background: #28a745; color: white; border: none; padding: 14px 24px; font-size: 18px; border-radius: 8px; cursor: pointer; font-weight: bold; width: 100%; margin-top: 20px; transition: background 0.2s; }
        .btn-submit:hover { background: #218838; }
        .btn-submit:disabled { background: #cbd5e1; color: #94a3b8; cursor: not-allowed; }
        .status-msg { margin-top: 15px; padding: 12px; border-radius: 6px; display: none; font-weight: bold; text-align: center; }
        .status-success { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .status-error { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
    </style>
</head>
<body>
    <div class="container">
        <div class="navbar">
            <span class="brand">⚡ NapSnap Hub</span>
            <div class="nav-links">
                <a href="/">← Main Dashboard</a>
                <a href="/calibration" class="active">Radar Tuning</a>
                <a href="/logs">Full Logs</a>
            </div>
        </div>

        <h1>HLK-LD2410 Radar Calibration & Live Telemetry</h1>
        <p style="color:#64748b;">Tune spatial gate sensitivities and monitor real-time radar energy streams directly over LAN.</p>
        
        {% if not is_online %}
        <div class="offline-banner">
            ⚠️ ESP8266 Radar Node is OFFLINE. Connect the sensor node to enable radar tuning and live engineering graphs.
        </div>
        {% endif %}

        <!-- Live Engineering Energy Graphs -->
        <div class="live-viz">
            <div class="viz-header">
                <span style="font-weight:bold; font-size:16px;">📡 Live Spatial Gate Energy Spectrum</span>
                <span id="target-pill" class="target-pill">Target: Idle</span>
            </div>
            {% for i in range(9) %}
            <div class="gate-viz-row">
                <span class="gate-label">Gate {{ i }} ({{ "%.2f"|format(i*0.75+0.75) }}m)</span>
                <!-- Moving Energy Bar -->
                <div class="bar-container" title="Moving Energy vs Threshold">
                    <div id="m-bar-{{ i }}" class="bar-fill-moving"></div>
                    <div id="m-thresh-{{ i }}" class="threshold-line" style="left: {{ moving_gates[i] }}%;"></div>
                </div>
                <!-- Static Energy Bar -->
                <div class="bar-container" title="Static Energy vs Threshold">
                    <div id="s-bar-{{ i }}" class="bar-fill-static"></div>
                    <div id="s-thresh-{{ i }}" class="threshold-line" style="left: {{ static_gates[i] }}%;"></div>
                </div>
            </div>
            {% endfor %}
            <div style="display:flex; justify-content:flex-end; gap:20px; font-size:11px; margin-top:10px; color:#94a3b8;">
                <span>🟩 Moving Target Energy</span>
                <span>🟦 Static Target Energy</span>
                <span>🟥 Red Line = Trigger Threshold</span>
            </div>
        </div>

        <div id="alert-banner" class="status-msg"></div>

        <form id="cal-form" onsubmit="saveCalibration(event)">
            <fieldset {% if not is_online %}disabled{% endif %} style="border:none; padding:0; margin:0;">
            <h2>Global Boundary Parameters</h2>
            <div class="grid-row">
                <div class="card">
                    <label><b>Maximum Range Gate Limit:</b></label><br><br>
                    <select name="max_gate" id="max_gate" style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1;">
                        {% for g in range(9) %}
                        <option value="{{ g }}" {% if g == max_gate %}selected{% endif %}>Gate {{ g }} (Up to {{ "%.2f"|format(g * 0.75 + 0.75) }}m)</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="card">
                    <label><b>Unoccupied Clearance Timeout:</b></label><br><br>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <input type="number" name="timeout" id="timeout" min="1" max="600" value="{{ timeout }}" style="flex:1; padding:7px; border-radius:4px; border:1px solid #cbd5e1;">
                        <span>seconds</span>
                    </div>
                </div>
            </div>

            <h2>Distance Gate Threshold Matrix (Gates 0 - 8)</h2>
            <table>
                <thead>
                    <tr>
                        <th style="width: 22%;">Gate & Physical Range</th>
                        <th style="width: 39%;">Moving Target Sensitivity (0-100)</th>
                        <th style="width: 39%;">Static Target Sensitivity (0-100)</th>
                    </tr>
                </thead>
                <tbody>
                    {% for i in range(9) %}
                    <tr>
                        <td>
                            <strong>Gate {{ i }}</strong><br>
                            <span style="font-size:12px; color:#64748b;">{{ "%.2f"|format(i * 0.75) }}m - {{ "%.2f"|format((i + 1) * 0.75) }}m</span>
                        </td>
                        <td>
                            <div class="slider-group">
                                <input type="range" name="moving_g{{ i }}" id="moving_g{{ i }}" min="0" max="100" value="{{ moving_gates[i] }}" oninput="updateThresh('m', {{ i }}, this.value)">
                                <span id="lbl-moving-{{ i }}" class="value-lbl">{{ moving_gates[i] }}</span>
                            </div>
                        </td>
                        <td class="static-slider">
                            <div class="slider-group">
                                <input type="range" name="static_g{{ i }}" id="static_g{{ i }}" min="0" max="100" value="{{ static_gates[i] }}" oninput="updateThresh('s', {{ i }}, this.value)">
                                <span id="lbl-static-{{ i }}" class="value-lbl">{{ static_gates[i] }}</span>
                            </div>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>

            <button type="submit" id="btn-save" class="btn-submit" {% if not is_online %}disabled{% endif %}>
                {% if is_online %}Burn Parameters to Radar Flash Memory{% else %}Node Offline - Tuning Disabled{% endif %}
            </button>
            </fieldset>
        </form>
    </div>

    <script>
        function updateThresh(type, gate, val) {
            document.getElementById('lbl-' + (type === 'm' ? 'moving-' : 'static-') + gate).innerText = val;
            var line = document.getElementById((type === 'm' ? 'm-thresh-' : 's-thresh-') + gate);
            if (line) line.style.left = val + '%';
        }

        function pollTelemetry() {
            fetch('/api/radar_telemetry')
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (!data.online) return;
                    
                    var pill = document.getElementById('target-pill');
                    if (data.presence || data.out_pin) {
                        pill.className = "target-pill target-active";
                        var mode = data.moving_detected ? "Moving" : (data.static_detected ? "Static" : "Active");
                        var distText = data.distance > 0 ? " (" + (data.distance / 100.0).toFixed(2) + "m)" : "";
                        pill.innerText = "Target: " + mode + distText;
                    } else {
                        pill.className = "target-pill";
                        pill.innerText = "Target: None";
                    }

                    if (data.moving_energy) {
                        for (var i = 0; i <= 8; i++) {
                            var mBar = document.getElementById('m-bar-' + i);
                            if (mBar) mBar.style.width = data.moving_energy[i] + '%';
                        }
                    }
                    if (data.static_energy) {
                        for (var i = 0; i <= 8; i++) {
                            var sBar = document.getElementById('s-bar-' + i);
                            if (sBar) sBar.style.width = data.static_energy[i] + '%';
                        }
                    }
                })
                .catch(function(err) { console.error("Telemetry error:", err); });
        }

        function saveCalibration(e) {
            e.preventDefault();
            var btnSave = document.getElementById('btn-save');
            var alertBanner = document.getElementById('alert-banner');
            
            btnSave.disabled = true;
            btnSave.innerText = "Flashing Parameters to Radar...";
            alertBanner.style.display = "none";

            var form = document.getElementById('cal-form');
            var formData = new URLSearchParams(new FormData(form));

            fetch('/commit_calibration', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                body: formData
            })
            .then(function(res) {
                if (res.ok) {
                    alertBanner.className = "status-msg status-success";
                    alertBanner.innerText = "Success: Radar parameters permanently flashed to non-volatile memory!";
                    alertBanner.style.display = "block";
                } else {
                    alertBanner.className = "status-msg status-error";
                    alertBanner.innerText = "Error: Failed to update radar settings. Check ESP8266 connection.";
                    alertBanner.style.display = "block";
                }
                btnSave.disabled = false;
                btnSave.innerText = "Burn Parameters to Radar Flash Memory";
            })
            .catch(function(err) {
                console.error("Save error:", err);
                alertBanner.className = "status-msg status-error";
                alertBanner.innerText = "Network Error: Unable to reach BeagleBone Hub.";
                alertBanner.style.display = "block";
                btnSave.disabled = false;
                btnSave.innerText = "Burn Parameters to Radar Flash Memory";
            });
        }

        setInterval(pollTelemetry, 500);
        pollTelemetry();
    </script>
</body>
</html>
"""

@app.route('/register', methods=['GET'])
def register_esp():
    global ESP_IP, IS_ARMED, LAST_HEARTBEAT_TIME
    
    incoming_ip = request.args.get('ip')
    incoming_state = request.args.get('state')
    
    if incoming_ip:
        with state_lock:
            LAST_HEARTBEAT_TIME = time.time()
            old_ip = ESP_IP
            old_state = IS_ARMED
            incoming_state_bool = (incoming_state == 'arm') if incoming_state else IS_ARMED
            
            ip_changed = (old_ip != incoming_ip)
            state_changed = (old_state != incoming_state_bool)
            
            ESP_IP = incoming_ip
            IS_ARMED = incoming_state_bool

        if ip_changed or state_changed or not old_ip:
            add_log(f"ESP8266 registered. IP: {incoming_ip}, State synced: {incoming_state.upper() if incoming_state else 'N/A'}")
        else:
            logger.info(f"ESP8266 heartbeat keepalive from IP {incoming_ip}.")
        
        return "IP and State Registered", 200
    return "Missing parameters", 400

# --- API ENDPOINTS ---
@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({
        "status": get_system_status(),
        "is_armed": IS_ARMED,
        "current_volume": int(CURRENT_VOLUME * 100),
        "logs": recent_logs
    })

@app.route('/api/calibration', methods=['GET'])
def api_calibration():
    with state_lock:
        return jsonify({
            "max_gate": RADAR_MAX_GATE,
            "timeout": RADAR_TIMEOUT,
            "moving_gates": MOVING_GATES,
            "static_gates": STATIC_GATES
        })

@app.route('/api/radar_telemetry', methods=['GET'])
def api_radar_telemetry():
    with state_lock:
        esp_ip_copy = ESP_IP
        status = get_system_status()
    if status == "OFFLINE" or not esp_ip_copy:
        return jsonify({"online": False})
    try:
        resp = requests.get(f"http://{esp_ip_copy}/telemetry", timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            data["online"] = True
            return jsonify(data)
    except Exception:
        pass
    return jsonify({"online": False})

# --- WEB UI ROUTES ---
@app.route('/')
def dashboard():
    with state_lock:
        logs_copy = list(recent_logs)
        vol_copy = CURRENT_VOLUME
    return render_template_string(HTML_PAGE, logs=logs_copy, current_volume=vol_copy)

@app.route('/calibration')
def calibration_page():
    with state_lock:
        status = get_system_status()
        is_online = (status != "OFFLINE")
        esp_ip_copy = ESP_IP

    # Query device for actual current hardware config on page load
    if is_online and esp_ip_copy:
        try:
            resp = requests.get(f"http://{esp_ip_copy}/get_config", timeout=2)
            if resp.status_code == 200:
                cfg = resp.json()
                with state_lock:
                    global RADAR_MAX_GATE, RADAR_TIMEOUT, MOVING_GATES, STATIC_GATES
                    RADAR_MAX_GATE = cfg.get("max_gate", RADAR_MAX_GATE)
                    RADAR_TIMEOUT = cfg.get("timeout", RADAR_TIMEOUT)
                    MOVING_GATES = cfg.get("moving_gates", MOVING_GATES)
                    STATIC_GATES = cfg.get("static_gates", STATIC_GATES)
        except Exception as e:
            add_log(f"Calibration Notice: Could not fetch state from ESP node. ({e})")

    with state_lock:
        max_g = RADAR_MAX_GATE
        timeout = RADAR_TIMEOUT
        m_gates = list(MOVING_GATES)
        s_gates = list(STATIC_GATES)

    return render_template_string(
        CALIBRATION_PAGE_HTML,
        is_online=is_online,
        max_gate=max_g,
        timeout=timeout,
        moving_gates=m_gates,
        static_gates=s_gates
    )

@app.route('/commit_calibration', methods=['POST'])
def commit_calibration():
    global RADAR_MAX_GATE, RADAR_TIMEOUT, MOVING_GATES, STATIC_GATES, ESP_IP
    
    try:
        max_g = int(request.form.get('max_gate', 4))
        timeout = int(request.form.get('timeout', 5))
        m_gates = []
        s_gates = []
        for i in range(9):
            m_gates.append(int(request.form.get(f'moving_g{i}', 50)))
            s_gates.append(int(request.form.get(f'static_g{i}', 100)))

        with state_lock:
            RADAR_MAX_GATE = max_g
            RADAR_TIMEOUT = timeout
            MOVING_GATES = m_gates
            STATIC_GATES = s_gates
            esp_ip_copy = ESP_IP

        add_log("Calibration Engine: Saved profile constraints to server cache.")

        if esp_ip_copy:
            m_str = ",".join(map(str, m_gates))
            s_str = ",".join(map(str, s_gates))
            url = f"http://{esp_ip_copy}/config_full?max_g={max_g}&timeout={timeout}&moving={m_str}&static={s_str}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                add_log("Calibration Engine: Parameters flashed to radar memory.")
                cast_audio("ding.mp3")
                return "Calibration flashed successfully", 200
            else:
                add_log(f"Calibration Error: ESP returned status {resp.status_code}")
                return f"ESP Error {resp.status_code}", 500
        else:
            add_log("Calibration Warning: ESP8266 not online yet, saved to cache.")
            return "Saved to server cache (ESP offline)", 200
    except Exception as e:
        add_log(f"Calibration Error: Failed to commit calibration. ({e})")
        return "Calibration failed", 500

@app.route('/logs')
def view_all_logs():
    try:
        with open('baby_monitor.log', 'r') as f:
            all_logs = f.readlines()[::-1]
        
        log_html = """
        <html>
        <body style="font-family: monospace; padding: 20px;">
            <h1>Full System Logs</h1>
            <a href="/">Back to Dashboard</a> | <a href="/calibration">Radar Calibration</a>
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
