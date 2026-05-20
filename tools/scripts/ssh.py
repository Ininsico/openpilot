#!/usr/bin/env python3
import os
import sys
import argparse
import queue
import re
import socket
import subprocess
import threading

from openpilot.common.basedir import BASEDIR
from openpilot.tools.lib.auth_config import get_token
from openpilot.tools.lib.api import CommaApi

LOCAL_CONNECT_TIMEOUT = 0.25
SSH_PORT = 22

def get_serial(device):
  for key in ("serial", "serial_number", "serialNumber", "hardware_serial", "hardwareSerial"):
    serial = device.get(key)
    if isinstance(serial, str) and re.fullmatch("[0-9a-zA-Z-]+", serial):
      return serial
  return None

def format_command(command):
  return " ".join([f"'{c}'" if " " in c else c for c in command])

def can_connect(host, port, timeout):
  result = queue.Queue(maxsize=1)

  def connect():
    try:
      with socket.create_connection((host, port), timeout=timeout):
        result.put(True)
    except OSError:
      result.put(False)

  threading.Thread(target=connect, daemon=True).start()
  try:
    return result.get(timeout=timeout)
  except queue.Empty:
    return False

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="A helper for connecting to devices over the comma prime SSH proxy.\
      Adding your SSH key to your SSH config is recommended for more convenient use; see https://docs.comma.ai/how-to/connect-to-comma/.")
  parser.add_argument("device", help="device name or dongle id")
  parser.add_argument("--host", help="ssh jump server host", default="ssh.comma.ai")
  parser.add_argument("--port", help="ssh jump server port", default=22, type=int)
  parser.add_argument("--key", help="ssh key", default=os.path.join(BASEDIR, "system/hardware/tici/id_rsa"))
  parser.add_argument("--debug", help="enable debug output", action="store_true")
  args = parser.parse_args()

  r = CommaApi(get_token()).get("v1/me/devices")
  devices = {x['dongle_id']: x for x in r}

  if not re.match("[0-9a-zA-Z]{16}", args.device):
    user_input = args.device.replace(" ", "").lower()
    matches = {k: v['alias'] for k, v in devices.items() if isinstance(v.get('alias'), str) and user_input in v['alias'].replace(" ", "").lower()}
    if len(matches) == 1:
      dongle_id = list(matches.keys())[0]
    else:
      print(f"failed to look up dongle id for \"{args.device}\"", file=sys.stderr)
      if len(matches) > 1:
        print("found multiple matches:", file=sys.stderr)
        for k, v in matches.items():
          print(f"  \"{v}\" ({k})", file=sys.stderr)
      exit(1)
  else:
    dongle_id = args.device

  name = dongle_id
  if dongle_id in devices:
    name = f"{devices[dongle_id].get('alias')} ({dongle_id})"

  serial = get_serial(devices.get(dongle_id, {}))
  local_host = f"comma-{serial}" if serial else None
  if local_host and can_connect(local_host, SSH_PORT, LOCAL_CONNECT_TIMEOUT):
    local_command = [
      "ssh",
      "-i", args.key,
      "-o", "ConnectTimeout=1",
      "-o", "ConnectionAttempts=1",
    ]
    if args.debug:
      local_command += ["-v"]
    local_command += [
      f"comma@{local_host}",
    ]

    print(f"connecting to {name} at {local_host} ...")
    if args.debug:
      print(format_command(local_command))

    try:
      ret = subprocess.run(local_command).returncode
    except KeyboardInterrupt:
      sys.exit(130)

    if ret != 255:
      sys.exit(ret)

    print(f"local connection failed, connecting to {name} through {args.host}:{args.port} ...")
  else:
    print(f"connecting to {name} through {args.host}:{args.port} ...")

  command = [
    "ssh",
    "-i", args.key,
    "-o", f"ProxyCommand=ssh -i {args.key} -W %h:%p -p %p %h@{args.host}",
    "-p", str(args.port),
  ]
  if args.debug:
    command += ["-v"]
  command += [
    f"comma@comma-{dongle_id}",
  ]
  if args.debug:
    print(format_command(command))
  os.execvp(command[0], command)
