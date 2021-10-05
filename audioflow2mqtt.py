import os
import sys
import json
import socket
import logging
import requests
from time import sleep
import paho.mqtt.client as mqtt_client
from threading import Thread as t

MQTT_HOST = os.getenv('MQTT_HOST')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_USER = os.getenv('MQTT_USER')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD')
MQTT_CLIENT = os.getenv('MQTT_CLIENT', 'audioflow2mqtt')
MQTT_QOS = int(os.getenv('MQTT_QOS', 1))
DEVICE_IP = os.getenv('DEVICE_IP')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
DISCOVERY_PORT = os.getenv('DISCOVERY_PORT', 54321)

pong = ""
device_ip = ""
device_info = ""
mqtt_connected = False
discovery = True

client = mqtt_client.Client(MQTT_CLIENT)

sub_topics = [
    'switch/',
    'zones/',
    'zonename/'
]

for index, topic in enumerate(sub_topics):
    sub_topics[index] = 'audioflow2mqtt/' + sub_topics[index]

if DEVICE_IP != None:
    logging.info('Device IP set; discovery is disabled.')
    discovery = False
    device_ip = DEVICE_IP
else:
    logging.debug('No device IP set; discovery is enabled.')

if LOG_LEVEL.lower() not in ['debug', 'info', 'warning', 'error']:
    logging.basicConfig(level='INFO', format='%(asctime)s %(levelname)s: %(message)s')
    logging.warning(f'Selected log level "{LOG_LEVEL}" is not valid; using default')
else:
    logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s %(levelname)s: %(message)s')

def discover_send():
    # Send discovery UDP packet to broadcast address
    global dscv_sent
    ping = b'afping'
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    try:
        sock.bind(('0.0.0.0', DISCOVERY_PORT))
    except Exception as e:
        logging.error(f'Unable to bind port {DISCOVERY_PORT}: {e}')
        sys.exit()
    
    try:
        sock.sendto(ping,('<broadcast>', 10499))
    except Exception as e:
        logging.error(f'Unable to send broadcast packet: {e}')
        sys.exit()

def discover_receive():
    # Listen for discovery response from Audioflow device
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('0.0.0.0', DISCOVERY_PORT))
    except Exception as e:
        logging.error(f'Unable to bind port {DISCOVERY_PORT}: {e}')

    while True:
        global pong
        global info
        data = sock.recvfrom(1024)
        pong, info = data
        pong = pong.decode('utf-8')
        sleep(5)
        break

def mqtt_connect():
    # Connect to MQTT broker, set LWT, and start loop
    global mqtt_connected
    try:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        # client.will_set('audioflow2mqtt/status', 'offline', 0, True)
        client.on_connect = on_mqtt_connect
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        client.loop_start()
        # client.publish('audioflow2mqtt/status', 'online', 0, True)
        logging.info('Connected to MQTT broker.')
        mqtt_connected = True
    except Exception as e:
        logging.error(f'Unable to connect to MQTT broker: {e}')
        sys.exit()

def on_mqtt_connect(client, userdata, flags, rc):
    # The callback for when the client receives a CONNACK response from the server.
    logging.debug('Connected with result code ' + str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    for topic in sub_topics:
        client.subscribe(topic + '#')

def on_message(client, userdata, msg):
    payload = str(msg.payload.decode('utf-8'))
    topic = str(msg.topic)
    print(topic)
    print(payload)
    if 'zones' in topic and payload in ['0', '1'] and topic[-1:] in ['0', '1', '2', '3']:
        set_zone_state(topic[-1:], payload)

def get_device_info(device_url):
    # Get info about Audioflow device
    global device_info
    try:
        device_info = requests.get(device_url + 'switch')
        device_info = json.loads(device_info.text)
        if discovery:
            logging.info(f"Audioflow model {device_info['model']} with name {device_info['name']} discovered at {info[0]}")
        else:
            logging.info(f"Audioflow model {device_info['model']} with name {device_info['name']} found at {DEVICE_IP}")
    except:
        logging.error('No Audioflow device found.')
        sys.exit()

def get_one_zone(zone_no):
    # Get info about one zone
    global device_url
    zone = requests.get(device_url + 'zones/' + str(zone_no))
    zone = json.loads(zone.text)

def get_all_zones():
    # Get info about all zones
    global device_url
    zones = requests.get(device_url + 'zones')
    zones = json.loads(zones.text)
    print(zones)

def set_zone_state(zone_no, zone_state):
    # Change state of one zone
    global device_url
    requests.put(device_url + 'zones/' + str(zone_no), data=str(zone_state))
    get_all_zones()

def set_zone_enable(zone_no, zone_enable, zone_name):
    # Enable or disable zone and change zone name
    global device_url
    requests.put(device_url + 'zonename/' + str(zone_no), data=str(str(zone_enable) + str(zone_name)))
    get_all_zones()

def poll_device():
    # Poll for Audioflow device information every 10 seconds in case button(s) is/are pressed on device
    while True:
        get_all_zones()
        sleep(10)

if discovery:
    discover_rx = t(target=discover_receive)
    discover_rx.start()
    discover_send()
    sleep(2)

if not discovery:
    device_url = f'http://{DEVICE_IP}/'
    get_device_info(device_url)
elif discovery and 'afpong' in pong:
    device_url = f'http://{info[0]}/'
    get_device_info(device_url)
    device_ip = info[0]
    discover_rx.join()
    logging.debug('Discovery stopped.')
else:
    logging.error('No Audioflow device found.')
    sys.exit()

mqtt_connect()
polling_thread = t(target=poll_device)
polling_thread.start()
client.loop_forever()