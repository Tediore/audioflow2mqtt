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
MQTT_QOS = int(os.getenv('MQTT_QOS', 1))
BASE_TOPIC = os.getenv('BASE_TOPIC', 'audioflow2mqtt')
HOME_ASSISTANT = os.getenv('HOME_ASSISTANT', True)
DEVICE_IP = os.getenv('DEVICE_IP')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
DISCOVERY_PORT = int(os.getenv('DISCOVERY_PORT', 54321))

client = mqtt_client.Client(BASE_TOPIC)

class NetworkDiscovery:
    def __init__(self):
        self.ping = b'afping'

    def nwk_discover_send(self):
        """Send discovery UDP packet to broadcast address"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        try:
            self.sock.bind(('0.0.0.0', DISCOVERY_PORT))
        except Exception as e:
            logging.error(f'Unable to bind port {DISCOVERY_PORT}: {e}')
            sys.exit()
        
        try:
            self.sock.sendto(self.ping,('<broadcast>', 10499))
            logging.debug('Sending UDP broadcast')
        except Exception as e:
            logging.error(f'Unable to send broadcast packet: {e}')
            sys.exit()

    def nwk_discover_receive(self):
        """Listen for discovery response from Audioflow device"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.pong = ""
        try:
            self.sock.bind(('0.0.0.0', DISCOVERY_PORT))
            logging.debug(f'Opening port {DISCOVERY_PORT}')
        except Exception as e:
            logging.error(f'Unable to bind port {DISCOVERY_PORT}: {e}')
            logging.error(f'Make sure nothing is currently using port {DISCOVERY_PORT}')
            sys.exit()
        try:
            data = self.sock.recvfrom(1024)
            self.pong, self.info = data
            self.pong = self.pong.decode('utf-8')
        except AttributeError as e:
            logging.debug(f"I don't know why this happens sometimes, but I don't want it crashing the program: {e}")
        sleep(5)
        self.sock.close()

class Device:
    def __init__(self):
        self.zone_count = 0
        self.switch_names = []
        self.retry_count = 0
        self.timeout = 3
        self.states = ['off', 'on']

    def get_device_info(self, device_url):
        """Get info about Audioflow device"""
        try:
            self.device_info = requests.get(url=device_url + 'switch', timeout=self.timeout)
            self.device_info = json.loads(self.device_info.text)
            self.zone_info = requests.get(url=device_url + 'zones', timeout=self.timeout)
            self.zone_info = json.loads(self.zone_info.text)
            self.serial_no = self.device_info['serial']
            self.model = self.device_info['model']
            self.zone_count = int(self.model[-2:-1]) # Second to last character in device model name shows zone count
            self.name = self.device_info['name']
            if nwk_discovery:
                logging.info(f"Audioflow model {self.model} with name {self.name} and serial number {self.serial_no} discovered at {n.info[0]}")
            else:
                logging.info(f"Audioflow model {self.model} with name {self.name} and serial number {self.serial_no} found at {DEVICE_IP}")
            client.publish(f'{BASE_TOPIC}/{self.serial_no}/status', 'online', MQTT_QOS, True)
        except:
            logging.error('No Audioflow device found.')
            sys.exit()
        
        for x in range(1,self.zone_count+1):
            self.zone_name = self.zone_info['zones'][int(x)-1]['name']
            if self.zone_name == "":
                self.switch_names.append(f'Zone {x}')
            else:
                self.switch_names.append(self.zone_info['zones'][int(x)-1]['name'])

    def get_one_zone(self, zone_no):
        """Get info about one zone and publish to MQTT"""
        try:
            self.zones = requests.get(url=device_url + 'zones', timeout=self.timeout)
            self.zones = json.loads(self.zones.text)
        except Exception as e:
            logging.error(f'Unable to communicate with Audioflow device: {e}')
        
        try:
            client.publish(f'{BASE_TOPIC}/{self.serial_no}/zone_state/{zone_no}', str(self.zones['zones'][int(zone_no)-1]['state']), MQTT_QOS)
            client.publish(f'{BASE_TOPIC}/{self.serial_no}/zone_enabled/{zone_no}', str(self.zones['zones'][int(zone_no)-1]['enabled']), MQTT_QOS)
        except Exception as e:
            logging.error(f'Unable to publish zone state: {e}')

    def get_all_zones(self):
        """Get info about all zones"""
        try:
            self.zones = requests.get(url=device_url + 'zones', timeout=self.timeout)
            self.zones = json.loads(self.zones.text)
            d.publish_all_zones()
            if self.retry_count > 0:
                logging.info('Reconnected to Audioflow device.')
            self.retry_count = 0
            client.publish(f'{BASE_TOPIC}/{self.serial_no}/status', 'online', MQTT_QOS, True)
        except Exception as e:
            if self.retry_count < 3:
                logging.error(f'Unable to communicate with Audioflow device: {e}')
            self.retry_count += 1
            if self.retry_count > 2:
                client.publish(f'{BASE_TOPIC}/{self.serial_no}/status', 'offline', MQTT_QOS, True)
                if self.retry_count < 4:
                    logging.warning('Audioflow device unreachable; marking as offline.')
                    logging.warning('Trying to reconnect every 10 sec in the background...')

    def publish_all_zones(self):
        """Publish info about all zones to MQTT"""
        try:
            for x in range(1,self.zone_count+1):
                client.publish(f'{BASE_TOPIC}/{self.serial_no}/zone_state/{x}', str(self.zones['zones'][int(x)-1]['state']), MQTT_QOS)
                client.publish(f'{BASE_TOPIC}/{self.serial_no}/zone_enabled/{x}', str(self.zones['zones'][int(x)-1]['enabled']), MQTT_QOS)
        except Exception as e:
            logging.error(f'Unable to publish all zone states: {e}')

    def set_zone_state(self, zone_no, zone_state):
        """Change state of one zone"""
        if self.zones['zones'][int(zone_no)-1]['enabled'] == 0:
            logging.warning(f'Zone {zone_no} is disabled.')
        else:
            if zone_state in ['on', 'off', 'toggle']:
                try:
                    current_state = self.zones['zones'][int(zone_no)-1]['state']
                    if zone_state in self.states:
                        data = self.states.index(zone_state)
                    else:
                        data = 1 if current_state == 'off' else 0
                    requests.put(url=device_url + 'zones/' + str(zone_no), data=str(data), timeout=self.timeout)
                    d.get_one_zone(zone_no=zone_no) # Device does not send new state after state change, so we get the new state and publish it to MQTT
                except Exception as e:
                    logging.error(f'Set zone state failed: {e}')
            else:
                logging.warning(f'"{zone_state}" is not a valid command. Valid commands are on, off, toggle')

    def set_all_zone_states(self, zone_state):
        """Turn all zones on or off"""
        if zone_state in self.states:
            try:
                data = '1 1 1 1' if zone_state == 'on' else '0 0 0 0'
                requests.put(url=device_url + 'zones', data=str(data), timeout=self.timeout)
                d.get_all_zones() # Device does not send new state after state change, so we get the new state and publish it to MQTT
            except Exception as e:
                logging.error(f'Set all zone states failed: {e}')
        elif zone_state == 'toggle':
            logging.warning(f'Toggle command can only be used for one zone.')
        else:
            logging.warning(f'"{zone_state}" is not a valid command. Valid commands are on, off')

    def set_zone_enable(self, zone_no, zone_enable):
        """Enable or disable zone"""
        if int(zone_enable) in [0, 1]:
            try:
                # Audioflow device expects the zone name in the same payload when enabling/disabling zone, so we append the existing name here
                requests.put(url=device_url + 'zonename/' + str(zone_no), data=str(str(zone_enable) + str(self.switch_names[int(zone_no)-1]).strip()), timeout=self.timeout)
                d.get_one_zone(zone_no=zone_no)
            except Exception as e:
                logging.error(f'Enable/disable zone failed: {e}')

    def poll_device(self):
        """Poll for Audioflow device information every 10 seconds in case button(s) is/are pressed on device"""
        while True:
            sleep(10)
            d.get_all_zones()

    def mqtt_discovery(self):
        """Send Home Assistant MQTT discovery payloads"""
        if HOME_ASSISTANT:
            ha_switch = 'homeassistant/switch/'
            try:
                for x in range(1,self.zone_count+1):
                    # Zone state entity (switch)
                    if self.zone_info['zones'][int(x)-1]['enabled'] == 0:
                        client.publish(f'{ha_switch}{self.serial_no}/{x}/config',json.dumps({'availability': [{'topic': f'{BASE_TOPIC}/status'},{'topic': f'{BASE_TOPIC}/{self.serial_no}/status'}], 'name':f'{self.switch_names[x-1]} speakers (Disabled)', 'command_topic':f'{BASE_TOPIC}/{self.serial_no}/set_zone_state/{x}', 'state_topic':f'{BASE_TOPIC}/{self.serial_no}/zone_state/{x}', 'payload_on': 'on', 'payload_off': 'off', 'unique_id': f'{self.serial_no}{x}', 'device':{'name': f'{self.name}', 'identifiers': f'{self.serial_no}', 'manufacturer': 'Audioflow', 'model': f'{self.model}'}, 'platform': 'mqtt', 'icon': 'mdi:speaker'}), 1, True)
                    else:
                        client.publish(f'{ha_switch}{self.serial_no}/{x}/config',json.dumps({'availability': [{'topic': f'{BASE_TOPIC}/status'},{'topic': f'{BASE_TOPIC}/{self.serial_no}/status'}], 'name':f'{self.switch_names[x-1]} speakers', 'command_topic':f'{BASE_TOPIC}/{self.serial_no}/set_zone_state/{x}', 'state_topic':f'{BASE_TOPIC}/{self.serial_no}/zone_state/{x}', 'payload_on': 'on', 'payload_off': 'off', 'unique_id': f'{self.serial_no}{x}', 'device':{'name': f'{self.name}', 'identifiers': f'{self.serial_no}', 'manufacturer': 'Audioflow', 'model': f'{self.model}'}, 'platform': 'mqtt', 'icon': 'mdi:speaker'}), 1, True)
            except Exception as e:
                print(f'Unable to publish Home Assistant MQTT discovery payloads: {e}')

def mqtt_connect():
    """Connect to MQTT broker and set LWT"""
    try:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        client.will_set('audioflow2mqtt/status', 'offline', 1, True)
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        client.publish('audioflow2mqtt/status', 'online', 1, True)
    except Exception as e:
        logging.error(f'Unable to connect to MQTT broker: {e}')
        sys.exit()

def on_connect(client, userdata, flags, rc):
    # The callback for when the client receives a CONNACK response from the MQTT broker.
    logging.info('Connected to MQTT broker with result code ' + str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe(f'{BASE_TOPIC}/{d.serial_no}/#')
    d.mqtt_discovery()

def on_message(client, userdata, msg):
    """Listen for MQTT payloads and forward to Audioflow device"""
    payload = msg.payload.decode('utf-8')
    topic = str(msg.topic)
    switch_no = topic[-1:]
    if 'set_zone_state' in topic:
        if topic.endswith('e'): # if no zone number is present in topic
            d.set_all_zone_states(zone_state=payload)
        else:
            d.set_zone_state(zone_no=switch_no, zone_state=payload)
    elif 'set_zone_enable' in topic:
        d.set_zone_enable(zone_no=switch_no, zone_enable=payload)

if MQTT_HOST == None:
    logging.error('Please specify the IP address or hostname of your MQTT broker.')
    sys.exit()

if LOG_LEVEL.lower() not in ['debug', 'info', 'warning', 'error']:
    logging.basicConfig(level='INFO', format='%(asctime)s %(levelname)s: %(message)s')
    logging.warning(f'Selected log level "{LOG_LEVEL}" is not valid; using default')
else:
    logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s %(levelname)s: %(message)s')

d = Device()
n = NetworkDiscovery()

if DEVICE_IP != None:
    logging.info('Device IP set; network discovery is disabled.')
    nwk_discovery = False
    device_ip = DEVICE_IP
    device_url = f'http://{DEVICE_IP}/'
    d.get_device_info(device_url=device_url)
else:
    nwk_discovery = True
    logging.info('No device IP set; network discovery is enabled.')
    nwk_discover_rx = t(target=n.nwk_discover_receive, daemon=True)
    nwk_discover_rx.start()
    n.nwk_discover_send()
    sleep(2)
    if 'afpong' in n.pong:
        device_url = f'http://{n.info[0]}/'
        d.get_device_info(device_url=device_url)
        device_ip = n.info[0]
        logging.info('Network discovery stopped')
    else:
        logging.error('No Audioflow device found.')
        logging.error('Confirm that you have host networking enabled and that the Audioflow device is on the same subnet.')
        sys.exit()

mqtt_connect()
d.get_all_zones()
polling_thread = t(target=d.poll_device, daemon=True)
polling_thread.start()
client.loop_forever()