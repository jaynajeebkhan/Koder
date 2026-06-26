# WiFi Sensing Study Guide

## What This Python Demo Is Showing

The dashboard has one real input: Windows WiFi signal strength from `netsh wlan show interfaces`.

Real values:

- SSID: the WiFi network name.
- Signal percent: Windows' user-friendly WiFi strength number.
- RSSI dBm: the real received signal strength. Closer to 0 is stronger. Example: `-45 dBm` is strong, `-63 dBm` is usable, `-80 dBm` is weak.
- Band/channel: the radio band and channel your adapter is using.

Simulated values:

- Heatmap.
- Presence.
- Pose.
- Breathing and movement.
- Model confidence.

These simulated values are for studying how a RuView-style UI might behave. They are not camera output.

## New Sense Lab UI

The upgraded dashboard separates the learning model into clearer parts:

- Humans: accurate RSSI-only mode shows `0` verified humans because RSSI cannot count humans.
- Animals: accurate RSSI-only mode shows `0` verified animals because RSSI cannot classify animals.
- Demo skeletons: optional simulated human tracks shown as moving green skeletons.
- Demo animal body: optional simulated amber body line, shown only when demo mode is enabled.
- Real RSSI: the real WiFi signal reading from Windows.
- Router distance: a rough estimate from RSSI. It estimates router-to-PC distance, not person distance, unless demo skeleton mode is enabled.
- Motion score: RSSI variation over the recent baseline. It is not human/animal classification.
- Room map: a simulated room with router and PC sensor markers.
- Event stream: simple timeline showing count changes.

The skeletons move in real time only in demo mode so you can understand how tracking UI works. They are not yet real detections from your room.

## Why A Normal Laptop Cannot Become A WiFi Camera

A normal WiFi adapter usually exposes only RSSI, which is one number about signal strength. A camera needs thousands or millions of spatial measurements.

Advanced WiFi sensing uses CSI, short for Channel State Information. CSI contains detailed phase/amplitude data across WiFi subcarriers. With CSI, multiple antennas, calibration, and machine learning, researchers can estimate motion, breathing, or rough pose.

## How To Make It More Accurate

For this Python demo:

1. Keep the PC in one fixed place.
2. Use 5 GHz if available because it has more channels and less interference than 2.4 GHz.
3. Collect baseline readings with no movement for 2-5 minutes.
4. Compare movement readings against the baseline.
5. Smooth the RSSI with a moving average.
6. Record timestamps, RSSI, channel, and known activity labels.

For real RuView-style sensing:

1. Use CSI-capable hardware such as ESP32-S3 CSI nodes or supported Linux CSI hardware.
2. Use at least 2 nodes, preferably more.
3. Keep transmitter, receiver, room layout, and WiFi channel fixed during training.
4. Collect labeled data for each activity you want to detect.
5. Train and test on separate sessions so the model does not just memorize one room setup.
6. Validate accuracy with consent from everyone in the test area.

## Study Path

Learn in this order:

1. Python basics: HTTP server, JSON, subprocess, time-series data.
2. WiFi basics: RSSI, dBm, channel, 2.4 GHz vs 5 GHz.
3. Signal processing: smoothing, noise, moving averages, FFT.
4. CSI basics: amplitude, phase, subcarriers, antennas.
5. Machine learning: classification, regression, train/test split.
6. Ethics and privacy: consent, notices, data retention, and safe testing.

## Good First Experiment

Open the dashboard and click `Calibrate baseline`. Keep the room still for 8-10 seconds. Then walk slowly between the WiFi router and the laptop/PC for 20-30 seconds. Watch `Motion Score` and `Possible Humans`.

If the score stays near `0`, your router-to-PC signal is stable and RSSI is not enough to detect that movement from this position. Move the PC/router, try a different WiFi band, or add CSI hardware.

RSSI-only detection cannot detect a still person reliably. It can only notice signal changes that may be caused by a moving person, object, wall reflection, interference, or WiFi rate changes.
