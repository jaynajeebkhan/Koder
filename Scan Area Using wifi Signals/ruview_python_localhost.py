import argparse
import json
import math
import random
import re
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse


APP_STARTED_AT = time.time()
RSSI_HISTORY = []
RSSI_LOCK = threading.Lock()
RSSI_HISTORY_LIMIT = 240
UPDATE_INTERVAL_SECONDS = 1


def room_payload():
    return {
        "width_m": 6.0,
        "height_m": 4.0,
        "sensors": [
            {"id": "router", "x": 0.08, "y": 0.14, "label": "WiFi router"},
            {"id": "laptop", "x": 0.90, "y": 0.82, "label": "This PC"},
        ],
        "zones": [
            {"id": "near-router", "x": 0.04, "y": 0.08, "w": 0.25, "h": 0.28, "label": "router zone"},
            {"id": "center", "x": 0.30, "y": 0.28, "w": 0.42, "h": 0.36, "label": "center activity"},
            {"id": "desk", "x": 0.74, "y": 0.66, "w": 0.20, "h": 0.22, "label": "PC zone"},
        ],
    }


def estimate_router_distance_m(rssi_dbm, band):
    if rssi_dbm is None:
        return None

    # Indoor RSSI distance is a rough estimate. This uses a calibrated learning
    # model, not a physics-grade measurement.
    band_text = (band or "").lower()
    reference_dbm_at_1m = -50 if "5" in band_text or "6" in band_text else -45
    path_loss_exponent = 3.0
    distance = 10 ** ((reference_dbm_at_1m - rssi_dbm) / (10 * path_loss_exponent))
    return round(max(0.3, min(50.0, distance)), 2)


def add_rssi_sample(wifi):
    if not wifi or wifi.get("rssi_dbm") is None:
        return

    sample = {
        "timestamp": time.time(),
        "rssi_dbm": wifi.get("rssi_dbm"),
        "signal_percent": wifi.get("signal_percent"),
    }
    with RSSI_LOCK:
        RSSI_HISTORY.append(sample)
        del RSSI_HISTORY[:-RSSI_HISTORY_LIMIT]


def rssi_analysis():
    with RSSI_LOCK:
        samples = list(RSSI_HISTORY[-60:])

    if not samples:
        return {
            "sample_count": 0,
            "average_rssi_dbm": None,
            "rssi_delta_dbm": None,
            "movement_score": 0,
            "possible_human_count": 0,
            "possible_animal_count": 0,
            "possible_presence": False,
            "detection_label": "waiting for WiFi RSSI",
            "presence_state": "waiting-for-rssi",
            "human_count_supported": False,
            "animal_count_supported": False,
        }

    current = samples[-1]["rssi_dbm"]
    values = [sample["rssi_dbm"] for sample in samples]
    recent_values = values[-10:]
    average = sum(values) / len(values)
    recent_average = sum(recent_values) / len(recent_values)
    variance = sum((value - average) ** 2 for value in values) / len(values)
    recent_variance = sum((value - recent_average) ** 2 for value in recent_values) / len(recent_values)
    delta = current - average
    percent_values = [
        sample["signal_percent"]
        for sample in samples
        if sample.get("signal_percent") is not None
    ]
    percent_swing = 0 if not percent_values else max(percent_values[-10:]) - min(percent_values[-10:])
    movement_score = min(
        100,
        int((abs(delta) * 18) + (variance * 11) + (recent_variance * 18) + (percent_swing * 2)),
    )

    if len(samples) < 8:
        state = "learning-baseline"
    elif movement_score >= 14:
        state = "possible-motion-near-signal-path"
    else:
        state = "stable-no-strong-motion"

    possible_presence = state == "possible-motion-near-signal-path"
    possible_human_count = 1 if possible_presence else 0
    if len(samples) < 8:
        detection_label = "calibrating baseline"
    elif possible_presence:
        detection_label = "possible moving person or object"
    else:
        detection_label = "no RSSI motion detected"

    return {
        "sample_count": len(samples),
        "average_rssi_dbm": round(average, 1),
        "rssi_delta_dbm": round(delta, 1),
        "movement_score": movement_score,
        "possible_human_count": possible_human_count,
        "possible_animal_count": 0,
        "possible_presence": possible_presence,
        "detection_label": detection_label,
        "presence_state": state,
        "human_count_supported": False,
        "animal_count_supported": False,
    }


def clear_rssi_history():
    with RSSI_LOCK:
        RSSI_HISTORY.clear()


def read_windows_wifi():
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    data = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower()] = value.strip()

    signal_text = data.get("signal")
    signal_percent = None
    if signal_text:
        match = re.search(r"(\d+)", signal_text)
        if match:
            signal_percent = max(0, min(100, int(match.group(1))))

    rssi_text = data.get("rssi")
    rssi_dbm = None
    if rssi_text:
        match = re.search(r"-?\d+", rssi_text)
        if match:
            rssi_dbm = int(match.group(0))

    return {
        "ssid": data.get("ssid", "unknown"),
        "bssid": data.get("ap bssid", data.get("bssid", "unknown")),
        "band": data.get("band", "unknown"),
        "radio_type": data.get("radio type", "unknown"),
        "channel": data.get("channel", "unknown"),
        "receive_rate_mbps": data.get("receive rate (mbps)", "unknown"),
        "transmit_rate_mbps": data.get("transmit rate (mbps)", "unknown"),
        "signal_percent": signal_percent,
        "rssi_dbm": rssi_dbm,
    }


def generate_heatmap(width=32, height=18, tracks=None):
    now = time.time() - APP_STARTED_AT
    tracks = tracks or []

    grid = []
    for y in range(height):
        row = []
        ny = y / max(1, height - 1)
        for x in range(width):
            nx = x / max(1, width - 1)
            value = 0.0
            for track in tracks:
                px = track["position"]["x"]
                py = track["position"]["y"]
                strength = 1.0 if track["type"] == "human" else 0.68
                dx = nx - px
                dy = ny - py
                distance = dx * dx + dy * dy
                spread = 0.017 if track["type"] == "human" else 0.012
                value += strength * math.exp(-distance / spread)
            value += 0.10 * math.sin((nx * 9.0) + now)
            value += 0.08 * math.cos((ny * 7.0) - (now * 0.7))
            value += random.uniform(-0.025, 0.025)
            row.append(round(max(0.0, min(1.0, value)), 3))
        grid.append(row)
    return grid


def clamp01(value):
    return max(0.0, min(1.0, value))


def human_skeleton(cx, cy, scale, phase):
    sway = 0.018 * math.sin(phase)
    arm = 0.035 * math.sin(phase * 1.45)
    step = 0.035 * math.sin(phase * 1.8)
    return {
        "head": [clamp01(cx + sway * 0.4), clamp01(cy - 0.24 * scale)],
        "neck": [clamp01(cx + sway * 0.25), clamp01(cy - 0.17 * scale)],
        "chest": [clamp01(cx), clamp01(cy - 0.09 * scale)],
        "pelvis": [clamp01(cx - sway * 0.2), clamp01(cy + 0.04 * scale)],
        "left_elbow": [clamp01(cx - 0.10 * scale), clamp01(cy - 0.06 * scale + arm)],
        "left_hand": [clamp01(cx - 0.18 * scale), clamp01(cy + 0.03 * scale + arm)],
        "right_elbow": [clamp01(cx + 0.10 * scale), clamp01(cy - 0.06 * scale - arm)],
        "right_hand": [clamp01(cx + 0.18 * scale), clamp01(cy + 0.03 * scale - arm)],
        "left_knee": [clamp01(cx - 0.07 * scale), clamp01(cy + 0.15 * scale + step)],
        "left_foot": [clamp01(cx - 0.10 * scale), clamp01(cy + 0.29 * scale + step * 0.3)],
        "right_knee": [clamp01(cx + 0.07 * scale), clamp01(cy + 0.15 * scale - step)],
        "right_foot": [clamp01(cx + 0.10 * scale), clamp01(cy + 0.29 * scale - step * 0.3)],
    }


def animal_body(cx, cy, scale, phase):
    tail = 0.03 * math.sin(phase * 2.4)
    head = 0.025 * math.cos(phase * 1.7)
    return {
        "head": [clamp01(cx + 0.14 * scale + head), clamp01(cy - 0.02 * scale)],
        "body_front": [clamp01(cx + 0.06 * scale), clamp01(cy)],
        "body_back": [clamp01(cx - 0.08 * scale), clamp01(cy + 0.01 * scale)],
        "tail": [clamp01(cx - 0.18 * scale), clamp01(cy - 0.02 * scale + tail)],
        "front_leg": [clamp01(cx + 0.07 * scale), clamp01(cy + 0.12 * scale)],
        "back_leg": [clamp01(cx - 0.08 * scale), clamp01(cy + 0.13 * scale)],
    }


def attach_track_distances(tracks, room):
    router = next(sensor for sensor in room["sensors"] if sensor["id"] == "router")
    laptop = next(sensor for sensor in room["sensors"] if sensor["id"] == "laptop")
    width = room["width_m"]
    height = room["height_m"]

    for track in tracks:
        x = track["position"]["x"]
        y = track["position"]["y"]
        router_distance = math.sqrt(((x - router["x"]) * width) ** 2 + ((y - router["y"]) * height) ** 2)
        laptop_distance = math.sqrt(((x - laptop["x"]) * width) ** 2 + ((y - laptop["y"]) * height) ** 2)
        track["distance_from_router_m"] = round(router_distance, 2)
        track["distance_from_pc_m"] = round(laptop_distance, 2)
    return tracks


def tracking_payload(demo=False, wifi=None):
    room = room_payload()
    analysis = rssi_analysis()
    router_distance = estimate_router_distance_m(
        None if wifi is None else wifi.get("rssi_dbm"),
        None if wifi is None else wifi.get("band"),
    )

    if not demo:
        possible_humans = analysis["possible_human_count"]
        return {
            "timestamp": time.time(),
            "mode": "experimental-rssi-motion",
            "demo_enabled": False,
            "counts": {
                "humans": possible_humans,
                "animals": 0,
                "total": possible_humans,
            },
            "tracks": [],
            "room": room,
            "router_distance_estimate_m": router_distance,
            "rssi_analysis": analysis,
            "update_interval_seconds": UPDATE_INTERVAL_SECONDS,
            "limitations": [
                "RSSI mode can only show possible motion/person presence, not verified identity.",
                "Animal count remains 0 because RSSI cannot classify animal vs human.",
                "Enable demo tracking only to study how CSI-style skeleton UI would look.",
            ],
        }

    t = time.time() - APP_STARTED_AT
    tracks = []

    human_a_x = 0.30 + 0.16 * math.sin(t * 0.20)
    human_a_y = 0.52 + 0.07 * math.cos(t * 0.28)
    tracks.append(
        {
            "id": "human-1",
            "type": "human",
            "label": "Human 1",
            "position": {"x": round(clamp01(human_a_x), 3), "y": round(clamp01(human_a_y), 3)},
            "velocity": {"x": round(0.032 * math.cos(t * 0.20), 3), "y": round(-0.020 * math.sin(t * 0.28), 3)},
            "confidence": round(0.84 + 0.05 * math.sin(t * 0.41), 3),
            "skeleton": human_skeleton(human_a_x, human_a_y, 0.86, t * 1.8),
        }
    )

    human_b_visible = math.sin(t * 0.11) > -0.62
    if human_b_visible:
        human_b_x = 0.67 + 0.10 * math.cos(t * 0.17)
        human_b_y = 0.48 + 0.06 * math.sin(t * 0.23)
        tracks.append(
            {
                "id": "human-2",
                "type": "human",
                "label": "Human 2",
                "position": {"x": round(clamp01(human_b_x), 3), "y": round(clamp01(human_b_y), 3)},
                "velocity": {"x": round(-0.017 * math.sin(t * 0.17), 3), "y": round(0.014 * math.cos(t * 0.23), 3)},
                "confidence": round(0.72 + 0.07 * math.cos(t * 0.31), 3),
                "skeleton": human_skeleton(human_b_x, human_b_y, 0.74, t * 1.55 + 1.8),
            }
        )

    animal_x = 0.52 + 0.26 * math.sin(t * 0.33 + 1.2)
    animal_y = 0.74 + 0.05 * math.cos(t * 0.58)
    tracks.append(
        {
            "id": "animal-1",
            "type": "animal",
            "label": "Animal 1",
            "species_guess": "small pet",
            "position": {"x": round(clamp01(animal_x), 3), "y": round(clamp01(animal_y), 3)},
            "velocity": {"x": round(0.086 * math.cos(t * 0.33 + 1.2), 3), "y": round(-0.029 * math.sin(t * 0.58), 3)},
            "confidence": round(0.64 + 0.08 * math.sin(t * 0.47 + 0.8), 3),
            "body": animal_body(animal_x, animal_y, 0.64, t * 2.1),
        }
    )

    tracks = attach_track_distances(tracks, room)
    humans = [track for track in tracks if track["type"] == "human"]
    animals = [track for track in tracks if track["type"] == "animal"]
    return {
        "timestamp": time.time(),
        "mode": "simulated-csi-style-tracking-from-real-rssi-seed",
        "demo_enabled": True,
        "counts": {
            "humans": len(humans),
            "animals": len(animals),
            "total": len(tracks),
        },
        "tracks": tracks,
        "room": room,
        "router_distance_estimate_m": router_distance,
        "rssi_analysis": analysis,
        "update_interval_seconds": UPDATE_INTERVAL_SECONDS,
        "limitations": [
            "Human and animal tracks are simulated demo tracks.",
            "This Windows Python version reads real RSSI but not CSI.",
            "Real classification needs CSI hardware, calibration, and labeled data.",
        ],
    }


def latest_payload(demo=False):
    wifi = read_windows_wifi()
    add_rssi_sample(wifi)
    now = time.time()
    t = now - APP_STARTED_AT
    real_signal = None if wifi is None else wifi.get("signal_percent")
    base_signal = 72 if real_signal is None else real_signal
    tracking = tracking_payload(demo=demo, wifi=wifi)
    analysis = rssi_analysis()
    router_distance = estimate_router_distance_m(
        None if wifi is None else wifi.get("rssi_dbm"),
        None if wifi is None else wifi.get("band"),
    )

    learning_signal = max(
        0,
        min(
            100,
            base_signal
            + int(8 * math.sin(t * 0.9))
            + int(4 * math.cos(t * 0.37))
            + random.randint(-2, 2),
        ),
    )

    return {
        "timestamp": now,
        "source": (
            "real-windows-wifi-rssi-plus-demo-tracking" if demo and wifi
            else "real-windows-wifi-rssi-only" if wifi
            else "simulation-only"
        ),
        "wifi": wifi,
        "real_signal_percent": real_signal,
        "learning_signal_percent": learning_signal,
        "signal_percent": learning_signal,
        "confidence": round(0.72 + 0.12 * math.sin(t * 0.31), 3),
        "presence": analysis["presence_state"] == "possible-motion-near-signal-path",
        "possible_human_count": tracking["counts"]["humans"],
        "possible_animal_count": tracking["counts"]["animals"],
        "verified_human_count": 0,
        "verified_animal_count": 0,
        "rssi_analysis": analysis,
        "router_distance_estimate_m": router_distance,
        "update_interval_seconds": UPDATE_INTERVAL_SECONDS,
        "heatmap": generate_heatmap(tracks=tracking["tracks"]),
    }


def vitals_payload():
    t = time.time() - APP_STARTED_AT
    return {
        "timestamp": time.time(),
        "mode": "simulated-not-medical",
        "breathing_rate_bpm": round(15.5 + 1.2 * math.sin(t * 0.22), 1),
        "movement_index": round(0.32 + 0.18 * abs(math.sin(t * 0.51)), 3),
        "confidence": round(0.66 + 0.09 * math.cos(t * 0.19), 3),
    }


def pose_payload(demo=False):
    tracking = tracking_payload(demo=demo)
    people = []
    for track in tracking["tracks"]:
        if track["type"] != "human":
            continue
        people.append(
            {
                "id": track["id"],
                "confidence": track["confidence"],
                "joints": track["skeleton"],
            }
        )
    return {
        "timestamp": time.time(),
        "mode": "simulated",
        "people": people,
    }


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RuView Python Localhost</title>
  <style>
    :root {
      --bg: #101316;
      --panel: #171c20;
      --panel-2: #20272d;
      --line: #33414a;
      --text: #edf3f7;
      --muted: #9dafba;
      --green: #45d483;
      --yellow: #ffd45c;
      --red: #ff667a;
      --cyan: #57c7ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: Segoe UI, Arial, sans-serif;
      color: var(--text);
      background: var(--bg);
    }
    main {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 22px 0 28px;
    }
    header {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 18px;
    }
    h1 {
      margin: 0;
      font-size: 28px;
      font-weight: 700;
    }
    .subtitle {
      color: var(--muted);
      margin-top: 6px;
      font-size: 14px;
    }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      white-space: nowrap;
      font-size: 13px;
    }
    .dot {
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: var(--green);
      box-shadow: 0 0 14px var(--green);
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 14px;
    }
    .viewer {
      min-height: 600px;
      border: 1px solid var(--line);
      background: #050708;
      border-radius: 8px;
      overflow: hidden;
      position: relative;
    }
    canvas {
      display: block;
      width: 100%;
      height: 100%;
      min-height: 600px;
    }
    .overlay {
      position: absolute;
      left: 14px;
      right: 14px;
      bottom: 14px;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      pointer-events: none;
    }
    .pill {
      border: 1px solid rgba(255, 255, 255, 0.18);
      background: rgba(16, 19, 22, 0.78);
      border-radius: 8px;
      padding: 9px 10px;
      font-size: 13px;
      backdrop-filter: blur(8px);
    }
    aside {
      display: grid;
      gap: 12px;
      align-content: start;
    }
    .panel {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 14px;
    }
    .panel h2 {
      font-size: 15px;
      margin: 0 0 12px;
    }
    .metric {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 9px 0;
      border-top: 1px solid rgba(255, 255, 255, 0.08);
      font-size: 14px;
    }
    .metric:first-of-type { border-top: 0; padding-top: 0; }
    .label { color: var(--muted); }
    .value { text-align: right; font-variant-numeric: tabular-nums; }
    .bar {
      height: 10px;
      border-radius: 999px;
      background: var(--panel-2);
      overflow: hidden;
      margin-top: 8px;
      border: 1px solid var(--line);
    }
    .fill {
      width: 0%;
      height: 100%;
      background: linear-gradient(90deg, var(--red), var(--yellow), var(--green));
      transition: width 180ms ease;
    }
    code {
      color: #cfeeff;
      word-break: break-word;
    }
    @media (max-width: 860px) {
      header { align-items: flex-start; flex-direction: column; }
      .layout { grid-template-columns: 1fr; }
      .viewer, canvas { min-height: 420px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>RuView Python Localhost</h1>
        <div class="subtitle">Real Windows WiFi RSSI plus simulated learning model at 127.0.0.1:3000</div>
      </div>
      <div class="status"><span class="dot"></span><span id="status">Connecting</span></div>
    </header>

    <section class="layout">
      <div class="viewer">
        <canvas id="canvas"></canvas>
        <div class="overlay">
          <div class="pill" id="frameLabel">Simulated heatmap frame</div>
          <div class="pill" id="sourceLabel">Source</div>
        </div>
      </div>

      <aside>
        <div class="panel">
          <h2>Signal</h2>
          <div class="metric"><span class="label">Real WiFi signal</span><span class="value" id="realSignal">--%</span></div>
          <div class="metric"><span class="label">RSSI</span><span class="value" id="rssi">-- dBm</span></div>
          <div class="metric"><span class="label">Learning signal</span><span class="value" id="signal">--%</span></div>
          <div class="bar"><div class="fill" id="signalFill"></div></div>
          <div class="metric"><span class="label">Simulated presence</span><span class="value" id="presence">--</span></div>
          <div class="metric"><span class="label">Model confidence</span><span class="value" id="confidence">--</span></div>
        </div>

        <div class="panel">
          <h2>WiFi Adapter</h2>
          <div class="metric"><span class="label">SSID</span><span class="value" id="ssid">--</span></div>
          <div class="metric"><span class="label">Band</span><span class="value" id="band">--</span></div>
          <div class="metric"><span class="label">Radio</span><span class="value" id="radio">--</span></div>
          <div class="metric"><span class="label">Channel</span><span class="value" id="channel">--</span></div>
          <div class="metric"><span class="label">Rx / Tx</span><span class="value" id="rates">--</span></div>
        </div>

        <div class="panel">
          <h2>Learning Model</h2>
          <div class="metric"><span class="label">Breathing demo</span><span class="value" id="breathing">-- bpm</span></div>
          <div class="metric"><span class="label">Movement demo</span><span class="value" id="movement">--</span></div>
        </div>

        <div class="panel">
          <h2>Local API</h2>
          <div class="metric"><span class="label">Health</span><span class="value"><code>/health</code></span></div>
          <div class="metric"><span class="label">Signal</span><span class="value"><code>/api/v1/sensing/latest</code></span></div>
          <div class="metric"><span class="label">Pose</span><span class="value"><code>/api/v1/pose/current</code></span></div>
        </div>
      </aside>
    </section>
  </main>

  <script>
    const canvas = document.getElementById("canvas");
    const ctx = canvas.getContext("2d");
    let latest = null;
    let frame = 0;

    function setText(id, text) {
      document.getElementById(id).textContent = text;
    }

    function resizeCanvas() {
      const rect = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.floor(rect.width * ratio);
      canvas.height = Math.floor(rect.height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }

    function colorFor(v) {
      const r = Math.floor(30 + 225 * v);
      const g = Math.floor(80 + 150 * Math.sin(v * Math.PI));
      const b = Math.floor(150 + 70 * (1 - v));
      return `rgb(${r}, ${g}, ${b})`;
    }

    function drawHeatmap(data) {
      const rect = canvas.getBoundingClientRect();
      const w = rect.width;
      const h = rect.height;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#050708";
      ctx.fillRect(0, 0, w, h);

      if (!data || !data.heatmap) {
        ctx.fillStyle = "#edf3f7";
        ctx.font = "16px Segoe UI";
        ctx.fillText("Waiting for local signal frames...", 24, 36);
        return;
      }

      const grid = data.heatmap;
      const rows = grid.length;
      const cols = grid[0].length;
      const cellW = w / cols;
      const cellH = h / rows;

      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          const v = grid[y][x];
          ctx.fillStyle = colorFor(v);
          ctx.globalAlpha = 0.24 + v * 0.72;
          ctx.fillRect(x * cellW, y * cellH, Math.ceil(cellW) + 1, Math.ceil(cellH) + 1);
        }
      }
      ctx.globalAlpha = 1;

      ctx.strokeStyle = "rgba(255,255,255,0.12)";
      ctx.lineWidth = 1;
      for (let x = 0; x <= cols; x += 4) {
        ctx.beginPath();
        ctx.moveTo(x * cellW, 0);
        ctx.lineTo(x * cellW, h);
        ctx.stroke();
      }
      for (let y = 0; y <= rows; y += 3) {
        ctx.beginPath();
        ctx.moveTo(0, y * cellH);
        ctx.lineTo(w, y * cellH);
        ctx.stroke();
      }

      ctx.fillStyle = "rgba(255,255,255,0.86)";
      ctx.font = "13px Segoe UI";
      ctx.fillText("Simulated signal density, not optical video", 16, 24);
    }

    async function refresh() {
      try {
        const [signalRes, vitalsRes] = await Promise.all([
          fetch("/api/v1/sensing/latest"),
          fetch("/api/v1/vital-signs")
        ]);
        latest = await signalRes.json();
        const vitals = await vitalsRes.json();

        setText("status", "Live");
        setText("realSignal", latest.real_signal_percent === null ? "not available" : `${latest.real_signal_percent}%`);
        setText("rssi", latest.wifi && latest.wifi.rssi_dbm !== null ? `${latest.wifi.rssi_dbm} dBm` : "-- dBm");
        setText("signal", `${latest.learning_signal_percent}%`);
        setText("presence", latest.presence ? "Detected" : "Clear");
        setText("confidence", latest.confidence.toFixed(3));
        setText("sourceLabel", latest.source);
        setText("frameLabel", `Frame ${++frame}`);
        document.getElementById("signalFill").style.width = `${latest.signal_percent}%`;

        const wifi = latest.wifi || {};
        setText("ssid", wifi.ssid || "simulation");
        setText("band", wifi.band || "simulation");
        setText("radio", wifi.radio_type || "simulation");
        setText("channel", wifi.channel || "--");
        setText("rates", wifi.receive_rate_mbps && wifi.transmit_rate_mbps ? `${wifi.receive_rate_mbps} / ${wifi.transmit_rate_mbps}` : "--");
        setText("breathing", `${vitals.breathing_rate_bpm} bpm`);
        setText("movement", vitals.movement_index.toFixed(3));
      } catch (error) {
        setText("status", "Disconnected");
      }
    }

    function loop() {
      drawHeatmap(latest);
      requestAnimationFrame(loop);
    }

    window.addEventListener("resize", resizeCanvas);
    resizeCanvas();
    refresh();
    setInterval(refresh, 850);
    loop();
  </script>
</body>
</html>
"""


HTML_V2 = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RuView Sense Lab</title>
  <style>
    :root {
      --bg: #0b0f12;
      --surface: #111820;
      --surface-2: #17212a;
      --surface-3: #202c36;
      --line: #2f414c;
      --text: #eef5f7;
      --muted: #9eb1ba;
      --human: #61d394;
      --animal: #ffbd59;
      --signal: #64b5ff;
      --danger: #ff6376;
      --violet: #c7a1ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background: #0b0f12;
      font-family: Segoe UI, Arial, sans-serif;
    }
    main {
      width: min(1380px, calc(100vw - 28px));
      margin: 0 auto;
      padding: 16px 0 24px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 12px;
    }
    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.1;
      letter-spacing: 0;
    }
    .sub {
      margin-top: 5px;
      color: var(--muted);
      font-size: 13px;
    }
    .top-status {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      min-height: 32px;
      padding: 7px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      color: var(--text);
      font-size: 13px;
      white-space: nowrap;
    }
    .chip input {
      accent-color: var(--human);
      margin: 0;
    }
    .chip button {
      appearance: none;
      border: 0;
      background: transparent;
      color: var(--text);
      font: inherit;
      padding: 0;
      cursor: pointer;
    }
    .dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--human);
      box-shadow: 0 0 14px var(--human);
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 12px;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .stat, .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
    }
    .stat {
      min-height: 92px;
      padding: 13px;
    }
    .stat-label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
    }
    .stat-value {
      margin-top: 5px;
      font-size: 34px;
      line-height: 1;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }
    .stat-small {
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .human { color: var(--human); }
    .animal { color: var(--animal); }
    .signal { color: var(--signal); }
    .violet { color: var(--violet); }
    .stage {
      position: relative;
      height: 690px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #050708;
      overflow: hidden;
    }
    canvas {
      display: block;
      width: 100%;
      height: 100%;
    }
    .legend {
      position: absolute;
      left: 12px;
      right: 12px;
      bottom: 12px;
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 10px;
      pointer-events: none;
    }
    .legend-stack {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      max-width: 780px;
    }
    .glass {
      border: 1px solid rgba(255,255,255,0.16);
      border-radius: 8px;
      background: rgba(12, 17, 21, 0.82);
      backdrop-filter: blur(8px);
      padding: 8px 10px;
      color: var(--text);
      font-size: 12px;
    }
    aside {
      display: grid;
      gap: 10px;
      align-content: start;
    }
    .panel {
      padding: 13px;
    }
    .panel h2 {
      margin: 0 0 10px;
      font-size: 15px;
      line-height: 1.2;
    }
    .row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 8px 0;
      border-top: 1px solid rgba(255,255,255,0.08);
      font-size: 13px;
    }
    .row:first-of-type { border-top: 0; padding-top: 0; }
    .label { color: var(--muted); }
    .value {
      text-align: right;
      font-variant-numeric: tabular-nums;
      max-width: 180px;
      overflow-wrap: anywhere;
    }
    .bar {
      height: 9px;
      margin-top: 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #0c1115;
      overflow: hidden;
    }
    .fill {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, var(--danger), var(--animal), var(--human));
      transition: width 180ms ease;
    }
    .tracks {
      display: grid;
      gap: 8px;
    }
    .track {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      background: var(--surface-2);
    }
    .track-head {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      font-size: 13px;
      font-weight: 700;
    }
    .track-meta {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .note {
      color: var(--muted);
      line-height: 1.45;
      font-size: 13px;
    }
    .steps {
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .events {
      display: grid;
      gap: 6px;
      max-height: 155px;
      overflow: auto;
      padding-right: 3px;
    }
    .event {
      color: var(--muted);
      font-size: 12px;
      border-left: 2px solid var(--line);
      padding-left: 8px;
    }
    code { color: #bfe8ff; }
    @media (max-width: 1050px) {
      .grid { grid-template-columns: 1fr; }
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .stage { height: 560px; }
    }
    @media (max-width: 650px) {
      main { width: min(100vw - 18px, 1380px); padding-top: 10px; }
      header { align-items: flex-start; flex-direction: column; }
      .top-status { justify-content: flex-start; }
      .stats { grid-template-columns: 1fr; }
      .stage { height: 470px; }
      .legend { align-items: stretch; flex-direction: column; }
      h1 { font-size: 24px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>RuView Sense Lab</h1>
        <div class="sub">Experimental RSSI motion detector. It can show possible human/motion, while true skeleton tracking needs CSI hardware.</div>
      </div>
      <div class="top-status">
        <div class="chip"><span class="dot"></span><span id="status">Connecting</span></div>
        <div class="chip">Localhost <span id="host">127.0.0.1:3000</span></div>
        <div class="chip"><button id="calibrateButton" type="button">Calibrate baseline</button></div>
        <label class="chip"><input id="demoToggle" type="checkbox"> Demo skeletons</label>
      </div>
    </header>

    <section class="stats">
      <div class="stat">
        <div class="stat-label">Possible Humans</div>
        <div class="stat-value human" id="humanCount">0</div>
        <div class="stat-small" id="humanSummary">waiting for tracks</div>
      </div>
      <div class="stat">
        <div class="stat-label">Animals</div>
        <div class="stat-value animal" id="animalCount">0</div>
        <div class="stat-small" id="animalSummary">waiting for tracks</div>
      </div>
      <div class="stat">
        <div class="stat-label">Real RSSI</div>
        <div class="stat-value signal" id="rssiBig">--</div>
        <div class="stat-small" id="signalSummary">reading Windows WiFi</div>
      </div>
      <div class="stat">
        <div class="stat-label" id="scoreLabel">Motion Score</div>
        <div class="stat-value violet" id="confidenceBig">--</div>
        <div class="stat-small" id="scoreSummary">RSSI variation, updates every second</div>
      </div>
    </section>

    <section class="grid">
      <div class="stage">
        <canvas id="map"></canvas>
        <div class="legend">
          <div class="legend-stack">
            <div class="glass">Green skeletons: humans</div>
            <div class="glass">Amber body: animal</div>
            <div class="glass">Blue dots: WiFi endpoints</div>
            <div class="glass">Heat: simulated signal density</div>
          </div>
          <div class="glass" id="frameInfo">Frame 0</div>
        </div>
      </div>

      <aside>
        <div class="panel">
          <h2>Real WiFi Reading</h2>
          <div class="row"><span class="label">SSID</span><span class="value" id="ssid">--</span></div>
          <div class="row"><span class="label">Signal</span><span class="value" id="realSignal">--%</span></div>
          <div class="bar"><div class="fill" id="signalFill"></div></div>
          <div class="row"><span class="label">RSSI</span><span class="value" id="rssi">-- dBm</span></div>
          <div class="row"><span class="label">Router distance</span><span class="value" id="routerDistance">-- m</span></div>
          <div class="row"><span class="label">Motion score</span><span class="value" id="motionScore">--</span></div>
          <div class="row"><span class="label">Presence state</span><span class="value" id="presenceState">--</span></div>
          <div class="row"><span class="label">Band / channel</span><span class="value" id="bandChannel">--</span></div>
          <div class="row"><span class="label">Rx / Tx</span><span class="value" id="rates">--</span></div>
        </div>

        <div class="panel">
          <h2>Tracked Objects</h2>
          <div class="tracks" id="trackList"></div>
        </div>

        <div class="panel">
          <h2>Detection Notes</h2>
          <div class="note">
            RSSI mode can detect possible signal-path motion, not verified identity. Animal classification is not supported from RSSI. Demo skeletons are optional and simulated.
          </div>
        </div>

        <div class="panel">
          <h2>Accuracy Roadmap</h2>
          <ol class="steps">
            <li>Collect baseline RSSI with an empty room.</li>
            <li>Record labeled movement sessions.</li>
            <li>Add CSI hardware such as ESP32-S3 nodes.</li>
            <li>Use multiple sensors for position triangulation.</li>
            <li>Train a classifier for human, pet, and empty-room states.</li>
          </ol>
        </div>

        <div class="panel">
          <h2>Event Stream</h2>
          <div class="events" id="events"></div>
        </div>
      </aside>
    </section>
  </main>

  <script>
    const canvas = document.getElementById("map");
    const ctx = canvas.getContext("2d");
    let latestSignal = null;
    let latestTracking = null;
    let frame = 0;
    let demoEnabled = false;
    const trails = new Map();
    const events = [];
    const demoToggle = document.getElementById("demoToggle");
    const calibrateButton = document.getElementById("calibrateButton");
    demoToggle.checked = demoEnabled;
    demoToggle.addEventListener("change", () => {
      demoEnabled = demoToggle.checked;
      trails.clear();
      pushEvent(demoEnabled ? "demo skeleton tracking enabled" : "accurate RSSI-only mode enabled");
      refresh();
    });
    calibrateButton.addEventListener("click", async () => {
      await fetch("/api/v1/calibrate");
      trails.clear();
      pushEvent("baseline reset; keep the room still for a few seconds");
      refresh();
    });

    function setText(id, value) {
      const node = document.getElementById(id);
      if (node) node.textContent = value;
    }

    function resizeCanvas() {
      const rect = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.floor(rect.width * ratio);
      canvas.height = Math.floor(rect.height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }

    function xy(point, rect) {
      return {
        x: point[0] * rect.width,
        y: point[1] * rect.height,
      };
    }

    function pos(track, rect) {
      return {
        x: track.position.x * rect.width,
        y: track.position.y * rect.height,
      };
    }

    function heatColor(v) {
      const r = Math.floor(18 + 220 * v);
      const g = Math.floor(60 + 170 * Math.sin(v * Math.PI));
      const b = Math.floor(80 + 150 * (1 - v));
      return `rgb(${r}, ${g}, ${b})`;
    }

    function drawRoom(rect) {
      ctx.fillStyle = "#050708";
      ctx.fillRect(0, 0, rect.width, rect.height);

      if (latestSignal && latestSignal.heatmap) {
        const grid = latestSignal.heatmap;
        const rows = grid.length;
        const cols = grid[0].length;
        const cellW = rect.width / cols;
        const cellH = rect.height / rows;
        for (let y = 0; y < rows; y++) {
          for (let x = 0; x < cols; x++) {
            const v = grid[y][x];
            ctx.globalAlpha = 0.13 + v * 0.50;
            ctx.fillStyle = heatColor(v);
            ctx.fillRect(x * cellW, y * cellH, Math.ceil(cellW) + 1, Math.ceil(cellH) + 1);
          }
        }
        ctx.globalAlpha = 1;
      }

      ctx.strokeStyle = "rgba(255,255,255,0.10)";
      ctx.lineWidth = 1;
      for (let x = 0; x <= rect.width; x += rect.width / 12) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, rect.height);
        ctx.stroke();
      }
      for (let y = 0; y <= rect.height; y += rect.height / 8) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(rect.width, y);
        ctx.stroke();
      }

      const room = latestTracking && latestTracking.room;
      if (!room) return;

      for (const zone of room.zones) {
        ctx.fillStyle = "rgba(255,255,255,0.035)";
        ctx.strokeStyle = "rgba(255,255,255,0.16)";
        ctx.lineWidth = 1;
        const x = zone.x * rect.width;
        const y = zone.y * rect.height;
        const w = zone.w * rect.width;
        const h = zone.h * rect.height;
        ctx.fillRect(x, y, w, h);
        ctx.strokeRect(x, y, w, h);
        ctx.fillStyle = "rgba(238,245,247,0.62)";
        ctx.font = "12px Segoe UI";
        ctx.fillText(zone.label, x + 8, y + 18);
      }

      for (const sensor of room.sensors) {
        const x = sensor.x * rect.width;
        const y = sensor.y * rect.height;
        ctx.fillStyle = "#64b5ff";
        ctx.beginPath();
        ctx.arc(x, y, 8, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "rgba(100,181,255,0.28)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, 26, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = "rgba(238,245,247,0.80)";
        ctx.font = "12px Segoe UI";
        ctx.fillText(sensor.label, x + 12, y - 10);
      }
    }

    function drawTrail(track, rect) {
      const p = pos(track, rect);
      if (!trails.has(track.id)) trails.set(track.id, []);
      const trail = trails.get(track.id);
      trail.push(p);
      while (trail.length > 32) trail.shift();

      const color = track.type === "human" ? "97,211,148" : "255,189,89";
      for (let i = 1; i < trail.length; i++) {
        ctx.strokeStyle = `rgba(${color}, ${i / trail.length * 0.35})`;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(trail[i - 1].x, trail[i - 1].y);
        ctx.lineTo(trail[i].x, trail[i].y);
        ctx.stroke();
      }
    }

    function drawBone(a, b, joints, rect, color) {
      const pa = xy(joints[a], rect);
      const pb = xy(joints[b], rect);
      ctx.strokeStyle = color;
      ctx.lineWidth = 4;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
    }

    function drawHuman(track, rect) {
      drawTrail(track, rect);
      const joints = track.skeleton;
      const color = "rgba(97,211,148,0.95)";
      const glow = "rgba(97,211,148,0.22)";
      const p = pos(track, rect);
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.ellipse(p.x, p.y + 24, 54, 22, 0, 0, Math.PI * 2);
      ctx.fill();

      [["head","neck"],["neck","chest"],["chest","pelvis"],["chest","left_elbow"],["left_elbow","left_hand"],["chest","right_elbow"],["right_elbow","right_hand"],["pelvis","left_knee"],["left_knee","left_foot"],["pelvis","right_knee"],["right_knee","right_foot"]].forEach(pair => drawBone(pair[0], pair[1], joints, rect, color));

      Object.keys(joints).forEach(key => {
        const point = xy(joints[key], rect);
        ctx.fillStyle = key === "head" ? "#eef5f7" : "#61d394";
        ctx.beginPath();
        ctx.arc(point.x, point.y, key === "head" ? 9 : 5, 0, Math.PI * 2);
        ctx.fill();
      });

      drawLabel(track, rect, "#61d394");
    }

    function drawAnimal(track, rect) {
      drawTrail(track, rect);
      const body = track.body;
      const color = "rgba(255,189,89,0.96)";
      const p = pos(track, rect);
      ctx.fillStyle = "rgba(255,189,89,0.18)";
      ctx.beginPath();
      ctx.ellipse(p.x, p.y + 12, 48, 18, 0, 0, Math.PI * 2);
      ctx.fill();

      [["tail","body_back"],["body_back","body_front"],["body_front","head"],["body_front","front_leg"],["body_back","back_leg"]].forEach(pair => drawBone(pair[0], pair[1], body, rect, color));

      Object.keys(body).forEach(key => {
        const point = xy(body[key], rect);
        ctx.fillStyle = key === "head" ? "#fff1cf" : "#ffbd59";
        ctx.beginPath();
        ctx.arc(point.x, point.y, key === "head" ? 7 : 5, 0, Math.PI * 2);
        ctx.fill();
      });

      drawLabel(track, rect, "#ffbd59");
    }

    function drawLabel(track, rect, color) {
      const p = pos(track, rect);
      const text = `${track.label} ${(track.confidence * 100).toFixed(0)}%`;
      const distanceText = track.distance_from_router_m === undefined ? "" : ` | ${track.distance_from_router_m} m`;
      const labelText = `${text}${distanceText}`;
      ctx.font = "13px Segoe UI";
      const width = ctx.measureText(labelText).width + 16;
      ctx.fillStyle = "rgba(5,7,8,0.82)";
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      const x = Math.max(8, Math.min(rect.width - width - 8, p.x - width / 2));
      const y = Math.max(10, p.y - 72);
      ctx.beginPath();
      ctx.roundRect(x, y, width, 28, 8);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#eef5f7";
      ctx.fillText(labelText, x + 8, y + 18);
    }

    function draw() {
      const rect = canvas.getBoundingClientRect();
      drawRoom(rect);
      if (latestTracking) {
        for (const track of latestTracking.tracks) {
          if (track.type === "human") drawHuman(track, rect);
          if (track.type === "animal") drawAnimal(track, rect);
        }
        if (!latestTracking.tracks.length) {
          ctx.fillStyle = "rgba(238,245,247,0.78)";
          ctx.font = "15px Segoe UI";
          ctx.fillText("RSSI-only mode: no verified human or animal tracks", 22, rect.height - 76);
          ctx.fillStyle = "rgba(158,177,186,0.78)";
          ctx.font = "13px Segoe UI";
          ctx.fillText("Enable demo skeletons to study tracking visuals.", 22, rect.height - 52);
        }
      }
      requestAnimationFrame(draw);
    }

    function updateTrackList(tracks) {
      const list = document.getElementById("trackList");
      list.innerHTML = "";
      if (!tracks.length) {
        const div = document.createElement("div");
        div.className = "track";
        div.innerHTML = `
          <div class="track-head"><span>No verified tracks</span><span>RSSI-only</span></div>
          <div class="track-meta" style="grid-template-columns:1fr;">
            <span>No skeleton track is available from RSSI alone. Watch Possible Humans and Motion Score for RSSI movement detection.</span>
          </div>`;
        list.appendChild(div);
        return;
      }
      for (const track of tracks) {
        const div = document.createElement("div");
        div.className = "track";
        const colorClass = track.type === "human" ? "human" : "animal";
        const detail = track.type === "animal" ? (track.species_guess || "animal") : "skeleton";
        div.innerHTML = `
          <div class="track-head"><span class="${colorClass}">${track.label}</span><span>${(track.confidence * 100).toFixed(0)}%</span></div>
          <div class="track-meta">
            <span>${detail}</span>
            <span>x ${track.position.x.toFixed(2)}</span>
            <span>${track.distance_from_router_m ?? "--"} m from router</span>
          </div>`;
        list.appendChild(div);
      }
    }

    function pushEvent(text) {
      const stamp = new Date().toLocaleTimeString();
      events.unshift(`${stamp} - ${text}`);
      while (events.length > 8) events.pop();
      const node = document.getElementById("events");
      node.innerHTML = events.map(e => `<div class="event">${e}</div>`).join("");
    }

    let lastCounts = "";

    async function refresh() {
      try {
        const demoParam = demoEnabled ? "?demo=1" : "?demo=0";
        const [signalRes, trackingRes, vitalsRes] = await Promise.all([
          fetch(`/api/v1/sensing/latest${demoParam}`),
          fetch(`/api/v1/tracking/current${demoParam}`),
          fetch("/api/v1/vital-signs"),
        ]);
        latestSignal = await signalRes.json();
        latestTracking = await trackingRes.json();
        const vitals = await vitalsRes.json();
        const counts = latestTracking.counts;
        const tracks = latestTracking.tracks;
        const wifi = latestSignal.wifi || {};
        const analysis = latestSignal.rssi_analysis || {};
        const motionScore = analysis.movement_score ?? 0;
        const confidence = tracks.length ? tracks.reduce((sum, t) => sum + t.confidence, 0) / tracks.length : 0;

        setText("status", "Live");
        setText("humanCount", counts.humans);
        setText("animalCount", counts.animals);
        if (demoEnabled) {
          setText("humanSummary", counts.humans === 1 ? "1 demo human track" : `${counts.humans} demo human tracks`);
          setText("animalSummary", counts.animals === 1 ? "1 demo animal track" : `${counts.animals} demo animal tracks`);
          setText("scoreLabel", "Demo Confidence");
          setText("confidenceBig", `${(confidence * 100).toFixed(0)}%`);
          setText("scoreSummary", "simulated CSI-style tracking confidence");
        } else {
          setText("humanSummary", counts.humans === 1 ? "possible moving person/object" : "no RSSI human/motion trigger");
          setText("animalSummary", "not supported by RSSI");
          setText("scoreLabel", "Motion Score");
          setText("confidenceBig", `${motionScore}`);
          setText("scoreSummary", analysis.detection_label || "RSSI variation, not classification");
        }
        setText("rssiBig", wifi.rssi_dbm === null || wifi.rssi_dbm === undefined ? "--" : wifi.rssi_dbm);
        setText("ssid", wifi.ssid || "simulation");
        setText("realSignal", latestSignal.real_signal_percent === null ? "not available" : `${latestSignal.real_signal_percent}%`);
        setText("rssi", wifi.rssi_dbm === null || wifi.rssi_dbm === undefined ? "-- dBm" : `${wifi.rssi_dbm} dBm`);
        setText("routerDistance", latestSignal.router_distance_estimate_m === null ? "-- m" : `${latestSignal.router_distance_estimate_m} m estimate`);
        setText("motionScore", `${motionScore}/100`);
        setText("presenceState", (analysis.presence_state || "--").replaceAll("-", " "));
        setText("bandChannel", `${wifi.band || "--"} / ${wifi.channel || "--"}`);
        setText("rates", wifi.receive_rate_mbps && wifi.transmit_rate_mbps ? `${wifi.receive_rate_mbps} / ${wifi.transmit_rate_mbps} Mbps` : "--");
        setText("signalSummary", `${latestSignal.real_signal_percent ?? "--"}% real, ${latestSignal.learning_signal_percent}% model`);
        setText("frameInfo", `Frame ${++frame} | ${demoEnabled ? "demo tracking" : "RSSI-only"} | updates 1s`);
        document.getElementById("signalFill").style.width = `${latestSignal.real_signal_percent || latestSignal.learning_signal_percent || 0}%`;
        updateTrackList(tracks);

        const countKey = `${demoEnabled}:${counts.humans}:${counts.animals}:${analysis.presence_state}`;
        if (countKey !== lastCounts) {
          if (demoEnabled) {
            pushEvent(`demo tracks: humans ${counts.humans}, animals ${counts.animals}`);
          } else {
            pushEvent(`RSSI detector: possible humans ${counts.humans}, animals unsupported, ${analysis.detection_label || "reading RSSI"}`);
          }
          lastCounts = countKey;
        }
      } catch (error) {
        setText("status", "Disconnected");
      }
    }

    if (!CanvasRenderingContext2D.prototype.roundRect) {
      CanvasRenderingContext2D.prototype.roundRect = function(x, y, w, h, r) {
        const radius = Math.min(r, w / 2, h / 2);
        this.beginPath();
        this.moveTo(x + radius, y);
        this.arcTo(x + w, y, x + w, y + h, radius);
        this.arcTo(x + w, y + h, x, y + h, radius);
        this.arcTo(x, y + h, x, y, radius);
        this.arcTo(x, y, x + w, y, radius);
        this.closePath();
      };
    }

    window.addEventListener("resize", resizeCanvas);
    resizeCanvas();
    refresh();
    setInterval(refresh, 1000);
    draw();
  </script>
</body>
</html>
"""


class LocalHandler(BaseHTTPRequestHandler):
    server_version = "RuViewPythonLocalhost/1.0"

    def log_message(self, format, *args):
        print("%s - %s" % (self.address_string(), format % args))

    def write_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def write_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = dict(parse_qsl(parsed.query))
        demo = query.get("demo") == "1"

        if path == "/" or path == "/index.html":
            self.write_html(HTML_V2)
            return

        if path == "/health":
            self.write_json(
                {
                    "ok": True,
                    "app": "ruview-python-localhost",
                    "uptime_seconds": round(time.time() - APP_STARTED_AT, 2),
                }
            )
            return

        if path == "/api/v1/sensing/latest":
            self.write_json(latest_payload(demo=demo))
            return

        if path == "/api/v1/calibrate":
            clear_rssi_history()
            self.write_json(
                {
                    "ok": True,
                    "message": "RSSI baseline cleared",
                    "timestamp": time.time(),
                }
            )
            return

        if path == "/api/v1/vital-signs":
            self.write_json(vitals_payload())
            return

        if path == "/api/v1/tracking/current":
            self.write_json(tracking_payload(demo=demo))
            return

        if path == "/api/v1/pose/current":
            self.write_json(pose_payload(demo=demo))
            return

        self.write_json({"error": "not found", "path": path}, status=404)


def main():
    parser = argparse.ArgumentParser(description="Direct Python localhost RuView-style demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), LocalHandler)
    url = f"http://{args.host}:{args.port}"

    if args.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print("")
    print("RuView Python Localhost is running")
    print(f"Open: {url}")
    print("API:  /health")
    print("API:  /api/v1/sensing/latest")
    print("Stop: press Ctrl+C in this window")
    print("")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
        print("Stopping RuView Python Localhost...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
