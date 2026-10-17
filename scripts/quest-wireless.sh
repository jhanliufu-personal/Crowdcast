#!/bin/sh
# Connect to the Quest over Wi-Fi with adb, and forward its localhost:8000 to this Mac's server: the same as
# `adb reverse` over USB, without the cable. The Quest and the Mac must be on a network that lets devices reach
# each other (many guest and venue networks don't; a phone hotspot or travel router does).
#
#   scripts/quest-wireless.sh                 # with the Quest plugged in (needed again after it reboots)
#   QUEST_IP=20.20.20.215 scripts/quest-wireless.sh   # reconnect without the cable, if it's still in Wi-Fi mode
#
# Then open http://localhost:8000 in the Quest's browser. Accept the "Allow USB debugging" prompt in the headset
# if it appears (tick "Always allow"), then run this again.
set -e
PORT=${PORT:-8000}

USB=$(adb devices | awk 'NR > 1 && $2 == "device" && $1 !~ /:/ { print $1; exit }')
if [ -n "$USB" ]; then
  IP=$(adb -s "$USB" shell ip -4 addr show wlan0 | awk '/inet / { split($2, a, "/"); print a[1] }')
  [ -n "$IP" ] || { echo "The Quest isn't on Wi-Fi." >&2; exit 1; }
  if ! adb devices | grep -q "^$IP:5555[[:space:]]*device"; then
    adb -s "$USB" tcpip 5555 > /dev/null  # restarts adb on the Quest, listening on Wi-Fi as well
    sleep 3
  fi
else
  IP=${QUEST_IP:?No Quest on USB. Plug it in, or set QUEST_IP to reconnect over Wi-Fi.}
fi

adb connect "$IP:5555" > /dev/null
if ! adb devices | grep -q "^$IP:5555[[:space:]]*device"; then
  echo "Connected to $IP but not authorized: accept the debugging prompt in the headset, then run this again." >&2
  exit 1
fi
adb -s "$IP:5555" reverse tcp:"$PORT" tcp:"$PORT"
echo "Quest at $IP over Wi-Fi. In its browser, open http://localhost:$PORT (you can unplug the cable)."
