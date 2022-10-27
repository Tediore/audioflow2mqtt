import os
import sys
import json
import yaml
import socket
import logging
import requests
from time import sleep
from threading import Thread as t
import paho.mqtt.client as mqtt_client

config_file = os.path.exists('config.yaml')
version = '0.6.0-dev'

if config_file:
    with open('config.yaml', 'r') as file:
        config = yaml.safe_load(file)
    MQTT_HOST = config['mqtt_host'] if 'mqtt_host' in config else None
    MQTT_PORT = config['mqtt_port'] if 'mqtt_port' in config else 1883
    MQTT_USER = config['mqtt_user'] if 'mqtt_user' in config else None
    MQTT_PASSWORD = config['mqtt_password'] if 'mqtt_password' in config else None
    MQTT_QOS = config['mqtt_qos'] if 'mqtt_qos' in config else 1
    BASE_TOPIC = config['base_topic'] if 'base_topic' in config else 'audioflow2mqtt'
    HOME_ASSISTANT = config['home_assistant'] if 'home_assistant' in config else True
    DEVICE_IPS = config['device_ips'] if 'device_ips' in config else None
    LOG_LEVEL = config['log_level'].upper() if 'log_level' in config else 'INFO'
    DISCOVERY_PORT = config['discovery_port'] if 'discovery_port' in config else 54321

else:
    MQTT_HOST = os.getenv('MQTT_HOST', None)
    MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
    MQTT_USER = os.getenv('MQTT_USER', None)
    MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', None)
    MQTT_QOS = int(os.getenv('MQTT_QOS', 1))
    BASE_TOPIC = os.getenv('BASE_TOPIC', 'audioflow2mqtt')
    HOME_ASSISTANT = os.getenv('HOME_ASSISTANT', True)
    DEVICE_IPS = os.getenv('DEVICE_IPS')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
    DISCOVERY_PORT = int(os.getenv('DISCOVERY_PORT', 54321))

client = mqtt_client.Client(BASE_TOPIC)

class NetworkDiscovery:
    def __init__(self):
        self.ping = b'afping'
        self.pong = ""
        self.discovered_devices = []

    def nwk_discover_send(self):
        """Send discovery UDP packet to broadcast address"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        try:
            self.sock.bind(('0.0.0.0', DISCOVERY_PORT))
        except Exception as e:
            logging.error(f'Unable to bind port {DISCOVERY_PORT}: {e}')
            sys.exit(1)
        
        for x in range(3): # Send discovery packet three times
            logging.info(f'Sending discovery broadcast {x+1} of 3...')
            try:
                self.sock.sendto(self.ping,('<broadcast>', 10499))
            except Exception as e:
                logging.error(f'Unable to send broadcast packet: {e}')
            sleep(3)

    def nwk_discover_receive(self):
        """Listen for discovery response from Audioflow device"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.sock.bind(('0.0.0.0', DISCOVERY_PORT))
            logging.debug(f'Opening port {DISCOVERY_PORT}')
        except Exception as e:
            logging.error(f'Unable to bind port {DISCOVERY_PORT}: {e}')
            logging.error(f'Make sure nothing is currently using port {DISCOVERY_PORT}')

        try:
            while True:
                self.pong, self.info = self.sock.recvfrom(1024)
                self.pong = self.pong.decode('utf-8')
                if self.info[0] not in self.discovered_devices:
                    self.discovered_devices.append(self.info[0])
                    logging.info(f'Discovery response received from {self.info[0]}; added to list of discovered devices')
                else:
                    logging.debug(f'Discovery response received from {self.info[0]}; already in list of discovered devices')

        except Exception as e:
            print(f'Unable to receive: {e}')

class AudioflowDevice:
    def __init__(self):
        self.timeout = 3
        self.states = ['off', 'on']
        self.set_all_zones = {'off': '0 0 0 0', 'on': '1 1 1 1'}
        self.devices = {}
        self.serial_nos = []

    def get_device_info(self, device_url, ip):
        """Get info about Audioflow device(s)"""
        device = True
        try:
            logging.debug(f'Attempting to connect to {ip}')
            device_info = requests.get(url=device_url + 'switch', timeout=self.timeout)
            logging.debug(f'Connected to {ip}.')
        except Exception as e:
            logging.error(f'Unable to connect to {ip}')
            device = False

        if device:
            device_info = json.loads(device_info.text)
            serial_no = device_info['serial']
            model = device_info['model']
            name = device_info['name']
            self.devices[serial_no] = {}
            self.devices[serial_no]['device_url'] = device_url
            self.devices[serial_no]['ip_addr'] = ip
            self.devices[serial_no]['zones'] = {}
            self.devices[serial_no]['switch_names'] = []
            self.devices[serial_no]['retry_count'] = 0
            self.serial_nos.append(serial_no)

            for item in device_info:
                self.devices[serial_no][item] = device_info[item]

            zone_info = requests.get(url=device_url + 'zones', timeout=self.timeout)
            zone_info = json.loads(zone_info.text)
            self.devices[serial_no]['zone_info'] = zone_info
            zone_count = len(zone_info['zones'])
            self.devices[serial_no]['zone_count'] = zone_count


            message = 'discovered at ' if nwk_discovery else 'found at '
            message += f'{ip}'
            logging.info(f"Audioflow model {model} with name {name} and serial number {serial_no} {message}")
            client.publish(f'{BASE_TOPIC}/{serial_no}/status', 'online', MQTT_QOS, True)
        
            for x in range(1,zone_count+1):
                zone_name = zone_info['zones'][int(x)-1]['name']
                self.devices[serial_no]['zones'][x] = zone_name
                if zone_name == "":
                    self.devices[serial_no]['switch_names'].append(f'Zone {x}')
                else:
                    self.devices[serial_no]['switch_names'].append(zone_name)

    def get_network_info(self, serial_no):
        """
        Get SSID and device signal strength
        String parsing :(
        """
        device_url = self.devices[serial_no]['device_url']
        retry_count = self.devices[serial_no]['retry_count']
        if not retry_count:
            try:
                device_info = requests.get(url=device_url + 'switch', timeout=self.timeout)    
            except Exception as e:
                logging.error(f'Unable to get network info: {e}')
            device_info = json.loads(device_info.text)
            wifi = device_info['wifi']
            ssid = wifi[:wifi.find('[')].strip()
            channel = wifi[wifi.find('[')+1:wifi.find(']')].strip()
            rssi = wifi[wifi.find(']')+3:].replace('dBm','').replace(')','').strip()
            network_info = {'ssid': ssid, 'channel': channel, 'rssi': rssi}

            try:
                for x in network_info.keys():
                    client.publish(f'{BASE_TOPIC}/{serial_no}/network_info/{x}', network_info[x], MQTT_QOS)
            except Exception as e:
                logging.error(f'Unable to publish network info: {e}')

    def get_one_zone(self, serial_no, zone_no):
        """Get info about one zone and publish to MQTT"""
        device_url = self.devices[serial_no]['device_url']
        try:
            zones = requests.get(url=device_url + 'zones', timeout=self.timeout)
            self.devices[serial_no]['zones'] = json.loads(zones.text)
        except Exception as e:
            logging.error(f'Unable to get zone info: {e}')
        
        try:
            zones = self.devices[serial_no]['zones']['zones']
            client.publish(f'{BASE_TOPIC}/{serial_no}/zone_state/{zone_no}', str(zones[int(zone_no)-1]['state']), MQTT_QOS)
            client.publish(f'{BASE_TOPIC}/{serial_no}/zone_enabled/{zone_no}', str(zones[int(zone_no)-1]['enabled']), MQTT_QOS)
        except Exception as e:
            logging.error(f'Unable to publish zone state: {e}')

    def get_all_zones(self, serial_no):
        """Get info about all zones"""
        device_url = self.devices[serial_no]['device_url']
        ip = self.devices[serial_no]['ip_addr']
        retry_count = self.devices[serial_no]['retry_count']
        try:
            zones = requests.get(url=device_url + 'zones', timeout=self.timeout)
            self.devices[serial_no]['zones'] = json.loads(zones.text)
            d.publish_all_zones(serial_no)
            if retry_count > 0:
                logging.info(f'Reconnected to Audioflow device at {ip}.')
            self.devices[serial_no]['retry_count'] = 0
            client.publish(f'{BASE_TOPIC}/{serial_no}/status', 'online', MQTT_QOS, True)
        except Exception as e:
            if retry_count < 3:
                logging.error(f'Unable to communicate with Audioflow device at {ip}: {e}')
            self.devices[serial_no]['retry_count'] += 1
            if retry_count == 3:
                client.publish(f'{BASE_TOPIC}/{serial_no}/status', 'offline', MQTT_QOS, True)
                logging.warning(f'Audioflow device at {ip} unreachable; marking as offline.')
                logging.warning(f'Trying to reconnect to {ip} every 10 sec in the background...')

    def publish_all_zones(self, serial_no):
        """Publish info about all zones to MQTT"""
        zone_count = self.devices[serial_no]['zone_count']
        zones = self.devices[serial_no]['zones']['zones']
        try:
            for x in range(1,zone_count+1):
                client.publish(f'{BASE_TOPIC}/{serial_no}/zone_state/{x}', str(zones[int(x)-1]['state']), MQTT_QOS)
                client.publish(f'{BASE_TOPIC}/{serial_no}/zone_enabled/{x}', str(zones[int(x)-1]['enabled']), MQTT_QOS)
        except Exception as e:
            logging.error(f'Unable to publish all zone states: {e}')

    def set_zone_state(self, serial_no, zone_no, zone_state):
        """Change state of one zone"""
        zone_count = self.devices[serial_no]['zone_count'] 
        zones = self.devices[serial_no]['zones']['zones']
        device_url = self.devices[serial_no]['device_url']
        ip = self.devices[serial_no]['ip_addr']
        if int(zone_no) > zone_count:
            logging.warning(f'{zone_no} is an invalid zone number.')
        elif zones[int(zone_no)-1]['enabled'] == 0:
            logging.warning(f'Zone {zone_no} is disabled.')
        else:
            if zone_state in ['on', 'off', 'toggle']:
                try:
                    current_state = zones[int(zone_no)-1]['state']
                    if zone_state in self.states:
                        data = self.states.index(zone_state)
                    else:
                        data = 1 if current_state == 'off' else 0
                    requests.put(url=device_url + 'zones/' + str(zone_no), data=str(data), timeout=self.timeout)
                    d.get_one_zone(serial_no, zone_no) # Device does not send new state after state change, so we get the new state and publish it to MQTT
                except Exception as e:
                    logging.error(f'Set zone state for device at {ip} failed: {e}')
            else:
                logging.warning(f'"{zone_state}" is not a valid command. Valid commands are on, off, toggle')

    def set_all_zone_states(self, serial_no, zone_state):
        """Turn all zones on or off"""
        device_url = self.devices[serial_no]['device_url']
        ip = self.devices[serial_no]['ip_addr']
        if zone_state in self.states:
            try:
                data = self.set_all_zones[zone_state]
                requests.put(url=device_url + 'zones', data=str(data), timeout=self.timeout)
                d.get_all_zones(serial_no) # Device does not send new state after state change, so we get the new state and publish it to MQTT
            except Exception as e:
                logging.error(f'Set all zone states for device at {ip} failed: {e}')
        elif zone_state == 'toggle':
            logging.warning(f'Toggle command can only be used for one zone.')
        else:
            logging.warning(f'"{zone_state}" is not a valid command. Valid commands are on, off')

    def set_zone_enable(self, serial_no, zone_no, zone_enable):
        """Enable or disable zone"""
        device_url = self.devices[serial_no]['device_url']
        switch_names = self.devices[serial_no]['switch_names']
        ip = self.devices[serial_no]['ip_addr']
        if int(zone_enable) in [0, 1]:
            try:
                # Audioflow device expects the zone name in the same payload when enabling/disabling zone, so we append the existing name here
                requests.put(url=device_url + 'zonename/' + str(zone_no), data=str(str(zone_enable) + str(switch_names[int(zone_no)-1]).strip()), timeout=self.timeout)
                d.get_one_zone(serial_no, zone_no)
            except Exception as e:
                logging.error(f'Enable/disable zone for device at {ip} failed: {e}')

    def poll_device_state(self):
        """Poll for Audioflow device information every 10 seconds in case button(s) is/are pressed on device"""
        while True:
            sleep(10)
            for serial_no in self.serial_nos:
                d.get_all_zones(serial_no)

    def poll_network_info(self):
        """Poll for Audioflow device network information every 60 seconds"""
        while True:
            sleep(60)
            for serial_no in self.serial_nos:
                d.get_network_info(serial_no)

    def mqtt_discovery(self, serial_no):
        """Send Home Assistant MQTT discovery payloads"""
        if HOME_ASSISTANT:
            zone_count = self.devices[serial_no]['zone_count']
            zone_info = self.devices[serial_no]['zone_info']['zones']
            name = self.devices[serial_no]['name']
            model = self.devices[serial_no]['model']
            fw_version = self.devices[serial_no]['version']
            switch_names = self.devices[serial_no]['switch_names']
            ha_switch = 'homeassistant/switch/'
            ha_button = 'homeassistant/button/'
            ha_sensor = 'homeassistant/sensor/'
            try:
                # HA switch entities
                for x in range(1,zone_count+1):
                    name_suffix = ' (Disabled)' if zone_info[int(x)-1]['enabled'] == 0 else '' # append "(Disabled)" to the end of the default entity name if zone is disabled
                    entity_name = f'{switch_names[x-1]} speakers{name_suffix}'
                    client.publish(f'{ha_switch}{serial_no}/{x}/config',json.dumps({
                        'availability': [
                            {'topic': f'{BASE_TOPIC}/status'},
                            {'topic': f'{BASE_TOPIC}/{serial_no}/status'}
                            ], 
                        'name': entity_name, 
                        'object_id': f'{entity_name} {serial_no}',
                        'command_topic': f'{BASE_TOPIC}/{serial_no}/set_zone_state/{x}', 
                        'state_topic': f'{BASE_TOPIC}/{serial_no}/zone_state/{x}', 
                        'payload_on': 'on', 
                        'payload_off': 'off', 
                        'unique_id': f'{serial_no}{x}', 
                        'icon': 'mdi:speaker',
                        'device': {
                            'name': f'{name}', 
                            'identifiers': f'{serial_no}', 
                            'manufacturer': 'Audioflow', 
                            'model': f'{model}', 
                            'sw_version': f'{fw_version}'}, 
                            'platform': 'mqtt'
                            }), 1, True)

                # HA button entities
                for x in ['off', 'on']:
                    entity_name = f'Turn all zones {x}'
                    client.publish(f'{ha_button}{serial_no}/all_zones_{x}/config',json.dumps({
                        'availability': [
                            {'topic': f'{BASE_TOPIC}/status'},
                            {'topic': f'{BASE_TOPIC}/{serial_no}/status'}
                            ], 
                        'name': entity_name,
                        'object_id': f'{entity_name} {serial_no}',
                        'command_topic': f'{BASE_TOPIC}/{serial_no}/set_zone_state', 
                        'payload_press': x, 
                        'unique_id': f'{serial_no}_all_zones_{x}', 
                        'icon': f'mdi:power-{x}',
                        'device': {
                            'name': f'{name}', 
                            'identifiers': f'{serial_no}', 
                            'manufacturer': 'Audioflow', 
                            'model': f'{model}', 
                            'sw_version': f'{fw_version}'}, 
                            'platform': 'mqtt'
                            }), 1, True)

                # HA sensor entities
                network_info_names = {
                                        'ssid': {'name': 'SSID', 'icon': 'mdi:access-point-network'},
                                        'channel': {'name': 'Wi-Fi channel', 'icon': 'mdi:access-point'},
                                        'rssi': {'name': 'RSSI', 'icon': 'mdi:signal'}
                                        }
                for x in network_info_names.keys():
                    entity_name = f"{network_info_names[x]['name']}"
                    client.publish(f'{ha_sensor}{serial_no}/{x}/config',json.dumps({
                        'availability': [
                            {'topic': f'{BASE_TOPIC}/status'},
                            {'topic': f'{BASE_TOPIC}/{serial_no}/status'}
                            ], 
                        'name': entity_name,
                        'object_id': f'{entity_name} {serial_no}',
                        'state_topic': f'{BASE_TOPIC}/{serial_no}/network_info/{x}',
                        'icon': f"{network_info_names[x]['icon']}",
                        'unique_id': f'{serial_no}{x}',
                        'device': {
                            'name': f'{name}', 
                            'identifiers': f'{serial_no}', 
                            'manufacturer': 'Audioflow', 
                            'model': f'{model}', 
                            'sw_version': f'{fw_version}'}, 
                            'platform': 'mqtt',
                            }), 1, True)

            except Exception as e:
                print(f'Unable to publish Home Assistant MQTT discovery payloads: {e}')

def mqtt_connect():
    """Connect to MQTT broker and set LWT"""
    try:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        client.will_set(f'{BASE_TOPIC}/status', 'offline', 1, True)
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        client.publish(f'{BASE_TOPIC}/status', 'online', 1, True)
    except Exception as e:
        logging.error(f'Unable to connect to MQTT broker: {e}')
        sys.exit(1)

def on_connect(client, userdata, flags, rc):
    # The callback for when the client receives a CONNACK response from the MQTT broker.
    logging.info('Connected to MQTT broker with result code ' + str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    for serial_no in d.serial_nos:
        client.subscribe(f'{BASE_TOPIC}/{serial_no}/#')
        d.mqtt_discovery(serial_no)

def on_message(client, userdata, msg):
    """Listen for MQTT payloads and forward to Audioflow device"""
    payload = msg.payload.decode('utf-8')
    topic = str(msg.topic)
    serial_no = topic[topic.find(BASE_TOPIC)+len(BASE_TOPIC)+1:topic.find('/set')]
    switch_no = topic[-1:]
    if 'set_zone_state' in topic:
        if topic.endswith('e'): # if no zone number is present in topic
            d.set_all_zone_states(serial_no, payload)
        else:
            d.set_zone_state(serial_no, switch_no, payload)
    elif 'set_zone_enable' in topic:
        d.set_zone_enable(serial_no, switch_no, payload)

if __name__ == '__main__':
    logging.basicConfig(level='INFO', format='%(asctime)s %(levelname)s: %(message)s')
    logging.info(f'=== audioflow2mqtt version {version} started ===')

    if LOG_LEVEL.lower() not in ['debug', 'info', 'warning', 'error']:
        logging.warning(f'Selected log level "{LOG_LEVEL}" is not valid; using default (info)')
    else:
        logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s %(levelname)s: %(message)s')

    if config_file:
        logging.info('Configuration file found.')
    else:
        logging.info('No configuration file found; loading environment variables.')

    if MQTT_HOST == None:
        logging.error('Please specify the IP address or hostname of your MQTT broker.')
        logging.error('Exiting...')
        sys.exit(1)

    if DEVICE_IPS != None:
        nwk_discovery = False
        device_ips = DEVICE_IPS
        if not config_file:
            device_ips = DEVICE_IPS.split(',')
        s = 's' if len(device_ips) > 1 else ''
        logging.info(f'Device IP{s} set; network discovery is disabled.')
    else:
        nwk_discovery = True

    d = AudioflowDevice()
    n = NetworkDiscovery()

    if nwk_discovery:
        device_ips = []
        logging.info('No device IPs set; network discovery is enabled.')
        nwk_discover_rx = t(target=n.nwk_discover_receive, daemon=True)
        nwk_discover_rx.start()
        n.nwk_discover_send()
        if n.discovered_devices:
            device_ips = n.discovered_devices
            logging.info('Network discovery stopped')
            n.sock.close()
        else:
            logging.error('No Audioflow devices found.')
            logging.error('Confirm that you have host networking enabled and that the Audioflow device is on the same subnet.')
            n.sock.close()
            sys.exit(1)

    for ip in device_ips:
        device_url = f'http://{ip}/'
        d.get_device_info(device_url, ip)

    mqtt_connect()
    for serial_no in d.serial_nos:
        d.get_all_zones(serial_no)
        d.get_network_info(serial_no)
    device_polling_thread = t(target=d.poll_device_state, daemon=True)
    nwk_info_polling_thread = t(target=d.poll_network_info, daemon=True)
    device_polling_thread.start()
    nwk_info_polling_thread.start()
    client.loop_forever()