import os
import sys
import json
import socket
import logging
import requests
from time import sleep
from threading import Thread as t
import paho.mqtt.client as mqtt_client

MQTT_HOST = os.getenv('MQTT_HOST')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_USER = os.getenv('MQTT_USER')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD')
MQTT_CLIENT = os.getenv('MQTT_CLIENT', 'audioflow2mqtt')
MQTT_QOS = int(os.getenv('MQTT_QOS', 1))
BASE_TOPIC = os.getenv('BASE_TOPIC', 'audioflow2mqtt')
HOME_ASSISTANT = os.getenv('HOME_ASSISTANT', True)
DEVICE_IP = os.getenv('DEVICE_IP')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
DISCOVERY_PORT = os.getenv('DISCOVERY_PORT', 54321)

pong = ""
device_ip = ""
device_info = ""
switch_names = []
discovery = True
states = {
    'off': '0',
    'on': '1'
}

client = mqtt_client.Client(MQTT_CLIENT)

if LOG_LEVEL.lower() not in ['debug', 'info', 'warning', 'error']:
    logging.basicConfig(level='INFO', format='%(asctime)s %(levelname)s: %(message)s')
    logging.warning(f'Selected log level "{LOG_LEVEL}" is not valid; using default')
else:
    logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s %(levelname)s: %(message)s')

if DEVICE_IP != None:
    logging.info('Device IP set; discovery is disabled.')
    discovery = False
    device_ip = DEVICE_IP
else:
    logging.debug('No device IP set; discovery is enabled.')

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
    global pong
    global info
    try:
        sock.bind(('0.0.0.0', DISCOVERY_PORT))
    except Exception as e:
        logging.error(f'Unable to bind port {DISCOVERY_PORT}: {e}')
    while udp_discover:
        try:
            data = sock.recvfrom(1024)
            pong, info = data
            pong = pong.decode('utf-8')
        except AttributeError as e:
            logging.debug(f"I don't know why this happens, but I don't want it crashing the program: {e}")

def mqtt_connect():
    # Connect to MQTT broker, set LWT, and start loop
    try:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        # client.will_set('audioflow2mqtt/status', 'offline', 0, True)
        client.on_connect = on_mqtt_connect
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        # client.publish('audioflow2mqtt/status', 'online', 0, True)
    except Exception as e:
        logging.error(f'Unable to connect to MQTT broker: {e}')
        sys.exit()

def on_mqtt_connect(client, userdata, flags, rc):
    # The callback for when the client receives a CONNACK response from the server.
    logging.info('Connected to MQTT broker with result code ' + str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe(f'{BASE_TOPIC}/{serial_no}/' + '#')
    mqtt_discovery()

def on_message(client, userdata, msg):
    payload = msg.payload.decode('utf-8')
    topic = str(msg.topic)
    switch_no = topic[topic.find('/set')-1]
    if topic.endswith('set_zone_state'):
        set_zone_state(switch_no, payload)
    elif topic.endswith('set_zone_enable'):
        set_zone_enable(switch_no, payload)

def get_device_info(device_url):
    # Get info about Audioflow device
    global device_info
    global serial_no
    global model
    global name
    global zone_count
    try:
        device_info = requests.get(device_url + 'switch')
        device_info = json.loads(device_info.text)
        zone_info = requests.get(device_url + 'zones')
        zone_info = json.loads(zone_info.text)
        serial_no = device_info['serial']
        model = device_info['model']
        zone_count = int(model[-2:-1]) # Second to last character in device model name shows zone count
        name = device_info['name']
        if discovery:
            logging.info(f"Audioflow model {model} with name {name} discovered at {info[0]}")
        else:
            logging.info(f"Audioflow model {model} with name {name} found at {DEVICE_IP}")
    except:
        logging.error('No Audioflow device found.')
        sys.exit()
    
    for x in range(1,zone_count+1):
        switch_names.append(zone_info['zones'][int(x)-1]['name'])

def get_one_zone(zone_no):
    # Get info about one zone
    global device_url
    zones = requests.get(device_url + 'zones')
    zones = json.loads(zones.text)
    client.publish(f'{BASE_TOPIC}/{serial_no}/{zone_no}/zone_state', str(zones['zones'][int(zone_no)-1]['state']))

def get_all_zones():
    # Get info about all zones
    global device_url
    global switch_names
    zones = requests.get(device_url + 'zones')
    zones = json.loads(zones.text)
    for x in range(1,zone_count+1):
        client.publish(f'{BASE_TOPIC}/{serial_no}/{x}/zone_state', str(zones['zones'][int(x)-1]['state']))

def set_zone_state(zone_no, zone_state):
    # Change state of one zone
    global device_url
    data = states[zone_state]
    try:
        data = states[zone_state]
        r = requests.put(device_url + 'zones/' + str(zone_no), data=str(data))
    except Exception as e:
        print(f'Error:{e}')
    get_one_zone(zone_no)

def set_zone_enable(zone_no, zone_enable): # WIP
    # Enable or disable zone
    global device_url
    try:
        requests.put(device_url + 'zonename/' + str(zone_no), data=str(str(zone_enable) + ' ' + str(switch_names[int(zone_no)-1])))
    except Exception as e:
        print(e)
    get_one_zone(zone_no)

def poll_device():
    # Poll for Audioflow device information every 10 seconds in case button(s) is/are pressed on device
    while True:
        sleep(10)
        get_all_zones()

def mqtt_discovery():
    # Send Home Assistant MQTT discovery payloads
    if HOME_ASSISTANT:
        ha_switch = 'homeassistant/switch/'
        try:
            for x in range(1,zone_count+1):
                client.publish(f'{ha_switch}{serial_no}/{x}/config',json.dumps({'availability':'[]', 'name':f'{switch_names[x-1]}', 'command_topic':f'{BASE_TOPIC}/{serial_no}/{x}/set_zone_state', 'state_topic':f'{BASE_TOPIC}/{serial_no}/zones', 'value_template':f'{{ value_json["zones"][{x}]["state"] }}', 'payload_on': 'on', 'payload_off': 'off', 'unique_id': f'{serial_no}', 'device':{'name': f'{name}', 'identifiers': f'{serial_no}', 'manufacturer': 'Audioflow', 'model': f'{model}'}, 'platform': 'mqtt', 'suggested_area': 'Home'}))
        except Exception as e:
            print(f'Unable to publish: {e}')

if discovery:
    udp_discover = True
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
    udp_discover = False
    logging.info('UDP discovery stopped')
else:
    logging.error('No Audioflow device found.')
    sys.exit()

mqtt_connect()
polling_thread = t(target=poll_device)
polling_thread.start()
client.loop_forever()