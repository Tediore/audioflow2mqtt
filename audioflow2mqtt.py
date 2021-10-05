import os
import sys
import json
import socket
import logging
import requests
from time import sleep
import paho.mqtt.client as mqtt
from threading import Thread as t

MQTT_HOST = os.getenv('MQTT_HOST')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_USER = os.getenv('MQTT_USER')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD')
MQTT_CLIENT = os.getenv('MQTT_CLIENT', 'audioflow2mqtt')
MQTT_QOS = int(os.getenv('MQTT_QOS', 1))
DEVICE_IP = os.getenv('DEVICE_IP')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
UDP_PORT = os.getenv('UDP_PORT', 54321)

pong = ""
device_ip = ""
device_info = ""
mqtt_connected = False

if DEVICE_IP != None:
    logging.info('Device IP set; discovery is disabled.')
    discovery = False
    device_ip = DEVICE_IP
else:
    logging.debug('No device IP set; discovery is enabled.')
    discovery = True

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
        sock.bind(('0.0.0.0', UDP_PORT))
    except Exception as e:
        logging.error(f'Unable to bind port {UDP_PORT}: {e}')
        sys.exit()
    
    try:
        sock.sendto(ping,('<broadcast>', 10499))
    except Exception as e:
        logging.error(f'Unable to send broadcast packet: {e}')
        sys.exit()

def discover_receive():
    # Listen for UDP response from Audioflow device
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('0.0.0.0', UDP_PORT))
    except Exception as e:
        logging.error(f'Unable to bind port {UDP_PORT}: {e}')

    while True:
        global discover_stop
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
    client = mqtt.Client(MQTT_CLIENT)
    try:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        client.will_set('audioflow2mqtt/status', 'offline', 0, True)
        client.connect(MQTT_HOST, MQTT_PORT)
        client.loop_start()
        client.publish('audioflow2mqtt/status', 'online', 0, True)
        logging.info('Connected to MQTT broker.')
        mqtt_connected = True
    except Exception as e:
        logging.error(f'Unable to connect to MQTT broker: {e}')
        sys.exit()

def get_device_info(device_url):
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
    # Change state of zone
    global device_url
    requests.put(device_url + 'zones/' + str(zone_no), data=str(zone_state))
    get_all_zones()

def set_zone_enable(zone_no, zone_enable, zone_name):
    # Enable or disable zone and change zone name
    global device_url
    requests.put(device_url + 'zonename/' + str(zone_no), data=str(str(zone_enable) + str(zone_name)))
    get_all_zones()

def poll_device():
    get_all_zones()
    sleep(5)

if discovery:
    discover_rx = t(target=discover_receive)
    discover_rx.start()
    discover_send()
    sleep(2)

if not discovery:
    device_url = f'http://{DEVICE_IP}/'
    get_device_info(device_url)
elif 'afpong' in pong:
    device_url = f'http://{info[0]}/'
    get_device_info(device_url)
    logging.info(f"Audioflow model {device_info['model']} with name {device_info['name']} discovered at {info[0]}")
    device_ip = info[0]
    discover_rx.join()
    logging.debug('Discovery stopped.')
else:
    logging.error('No Audioflow device found.')
    sys.exit()