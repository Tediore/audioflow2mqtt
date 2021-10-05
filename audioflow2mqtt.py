import os
import sys
import json
import socket
import requests
from threading import Thread as t
from time import sleep
import logging

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
UDP_PORT = os.getenv('UDP_PORT', 54321)

pong = ""
device_ip = ""

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
    # Listen for UDP response (afpong) from Audioflow device
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


discover_rx = t(target=discover_receive)
discover_rx.start()
discover_send()
sleep(1)

if 'afpong' in pong:
    device_url = f'http://{info[0]}/'
    device_info = requests.get(device_url + 'switch')
    device_info = json.loads(device_info.text)
    print(f"Audioflow model {device_info['model']} with name {device_info['name']} discovered at {info[0]}")
    device_ip = info[0]
    discover_rx.join()
    logging.debug('Discovery stopped.')
else:
    logging.error('No Audioflow device found.')
    sys.exit()

set_zone_state(1,1)
set_zone_enable(1,1,'Testing')