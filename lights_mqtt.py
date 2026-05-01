#!/usr/bin/env python3
import os, subprocess
import paho.mqtt.client as mqtt

from lights_config import LIGHTS_SCRIPT

BROKER = os.getenv("MQTT_HOST", "127.0.0.1")
PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC = os.getenv("MQTT_TOPIC", "lights/cmd")
LIGHTS = os.getenv("LIGHTS_BIN", str(LIGHTS_SCRIPT))

def on_message(client, userdata, msg):
    cmd = msg.payload.decode().strip()
    if not cmd:
        return
    # Example payloads:
    #   warm
    #   off
    #   fade night 10
    subprocess.run([LIGHTS, *cmd.split()], check=False)

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.on_message = on_message
c.connect(BROKER, PORT, 60)
c.subscribe(TOPIC)
c.loop_forever()
