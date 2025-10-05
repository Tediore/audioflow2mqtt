import aiomqtt
import asyncio
import httpx
import json
import logging
import os
import socket
import sys
from pathlib import Path
from threading import Thread
import yaml

VERSION = '0.8.0'
CONFIG_FILE = Path('config.yaml')


class Config:
    """Configuration management"""
    
    @staticmethod
    def load():
        """Load configuration from file or environment variables"""
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as file:
                config = yaml.safe_load(file)
                mqtt = config.get('mqtt', {})
                gen = config.get('general', {})
            
            return {
                'mqtt_host': mqtt.get('host'),
                'mqtt_port': mqtt.get('port', 1883),
                'mqtt_user': mqtt.get('user'),
                'mqtt_password': mqtt.get('password'),
                'mqtt_qos': mqtt.get('qos', 1),
                'base_topic': mqtt.get('base_topic', 'audioflow2mqtt'),
                'home_assistant': mqtt.get('home_assistant', True),
                'device_ips': gen.get('devices'),
                'log_level': gen.get('log_level', 'INFO').upper(),
                'discovery_port': gen.get('discovery_port', 54321),
                'config_source': 'file'
            }
        else:
            device_ips = os.getenv('DEVICE_IPS') or os.getenv('DEVICES')
            if device_ips:
                device_ips = device_ips.split(',')
            
            return {
                'mqtt_host': os.getenv('MQTT_HOST'),
                'mqtt_port': int(os.getenv('MQTT_PORT', 1883)),
                'mqtt_user': os.getenv('MQTT_USER'),
                'mqtt_password': os.getenv('MQTT_PASSWORD'),
                'mqtt_qos': int(os.getenv('MQTT_QOS', 1)),
                'base_topic': os.getenv('BASE_TOPIC', 'audioflow2mqtt'),
                'home_assistant': os.getenv('HOME_ASSISTANT', 'True').lower() == 'true',
                'device_ips': device_ips,
                'log_level': os.getenv('LOG_LEVEL', 'INFO').upper(),
                'discovery_port': int(os.getenv('DISCOVERY_PORT', 54321)),
                'config_source': 'environment'
            }


class NetworkDiscovery:
    """Network discovery for Audioflow devices"""
    
    PING_MESSAGE = b'afping'
    BROADCAST_PORT = 10499
    MAX_RETRIES = 3
    RETRY_DELAY = 3
    
    def __init__(self, discovery_port):
        self.discovery_port = discovery_port
        self.discovered_devices = []
        self.sock = None

    def _create_socket(self):
        """Create and configure UDP socket"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        return sock

    def send_discovery(self):
        """Send discovery UDP packets to broadcast address"""
        self.sock = self._create_socket()
        
        try:
            self.sock.bind(('0.0.0.0', self.discovery_port))
        except OSError as e:
            logging.error(f'Unable to bind port {self.discovery_port}: {e}')
            sys.exit(1)
        
        for attempt in range(self.MAX_RETRIES):
            logging.info(f'Sending discovery broadcast {attempt + 1} of {self.MAX_RETRIES}...')
            try:
                self.sock.sendto(self.PING_MESSAGE, ('<broadcast>', self.BROADCAST_PORT))
            except OSError as e:
                logging.error(f'Unable to send broadcast packet: {e}')
            asyncio.sleep(self.RETRY_DELAY)

    def receive_discovery(self):
        """Listen for discovery responses from Audioflow devices"""
        self.sock = self._create_socket()
        
        try:
            self.sock.bind(('0.0.0.0', self.discovery_port))
            logging.debug(f'Listening on port {self.discovery_port}')
        except OSError as e:
            logging.error(f'Unable to bind port {self.discovery_port}: {e}')
            logging.error(f'Make sure nothing is currently using port {self.discovery_port}')
            return

        try:
            while True:
                data, addr = self.sock.recvfrom(1024)
                ip = addr[0]
                
                if ip not in self.discovered_devices:
                    self.discovered_devices.append(ip)
                    logging.info(f'Discovery response received from {ip}; added to list')
                else:
                    logging.debug(f'Discovery response received from {ip}; already in list')
        except Exception as e:
            logging.error(f'Unable to receive discovery responses: {e}')

    def close(self):
        """Close the socket"""
        if self.sock:
            self.sock.close()


class AudioflowDevice:
    """Manages Audioflow device communication and state"""
    
    TIMEOUT = 3
    STATES = ['off', 'on']
    SET_ALL_ZONES = {'off': '0 0 0 0', 'on': '1 1 1 1'}
    ZONE_LABELS = ['A', 'B', 'C', 'D']
    MAX_RETRIES = 3
    
    def __init__(self, config, mqtt_client):
        self.config = config
        self.mqtt_client = mqtt_client
        self.devices = {}
        self.serial_nos = []

    async def discover_device(self, ip, httpx_client, from_discovery=False):
        """Get information about an Audioflow device"""
        device_url = f'http://{ip}/'
        
        try:
            logging.debug(f'Attempting to connect to {ip}...')
            response = await httpx_client.get(
                url=f'{device_url}switch',
                timeout=self.TIMEOUT
            )
            device_info = response.json()
            logging.debug(f'Connected to {ip}.')
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            logging.error(f'Unable to connect to {ip}: {e}')
            return False

        serial_no = device_info['serial']
        model = device_info['model']
        name = device_info['name']
        
        # Initialize device data structure
        self.devices[serial_no] = {
            'device_url': device_url,
            'ip_addr': ip,
            'zones': {},
            'switch_names': [],
            'retry_count': 0,
            **device_info
        }
        self.serial_nos.append(serial_no)

        # Get zone information
        try:
            zone_response = await httpx_client.get(
                url=f'{device_url}zones',
                timeout=self.TIMEOUT
            )
            zone_info = zone_response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            logging.error(f'Unable to get zone info from {ip}: {e}')
            return False

        self.devices[serial_no]['zone_info'] = zone_info
        zone_count = len(zone_info['zones'])
        self.devices[serial_no]['zone_count'] = zone_count

        source = 'discovered at' if from_discovery else 'found at'
        logging.info(
            f"Audioflow {model} '{name}' (SN: {serial_no}) {source} {ip}"
        )
        
        # Process zone names
        for idx in range(zone_count):
            zone_name = zone_info['zones'][idx].get('name', '')
            if not zone_name:
                zone_name = f'Zone {self.ZONE_LABELS[idx]}'
            self.devices[serial_no]['switch_names'].append(zone_name)
        
        self.devices[serial_no]['zones'] = zone_info
        logging.debug(f'Device {serial_no}: {self.devices[serial_no]}')
        
        return True

    async def get_network_info(self, serial_no, httpx_client):
        """Get SSID and device signal strength"""
        device = self.devices[serial_no]
        
        if device['retry_count'] > 0:
            return
        
        try:
            response = await httpx_client.get(
                url=f"{device['device_url']}switch",
                timeout=self.TIMEOUT
            )
            device_info = response.json()
            wifi = device_info.get('wifi', '')
            
            # Parse WiFi string: "SSID [Channel] (RSSI dBm)"
            ssid = wifi[:wifi.find('[')].strip() if '[' in wifi else ''
            channel = wifi[wifi.find('[')+1:wifi.find(']')].strip() if '[' in wifi and ']' in wifi else ''
            rssi = wifi[wifi.find(']')+3:].replace('dBm','').replace(')','').strip() if ']' in wifi else ''
            
            network_info = {'ssid': ssid, 'channel': channel, 'rssi': rssi}
            
            if self.mqtt_client.is_connected:
                for key, value in network_info.items():
                    await self.mqtt_client.publish(
                        f"{self.config['base_topic']}/{serial_no}/network_info/{key}",
                        value,
                        qos=self.config['mqtt_qos']
                    )
        except Exception as e:
            logging.error(f'Unable to get network info for {serial_no}: {e}')

    async def get_zone_state(self, serial_no, zone_no, httpx_client):
        """Get state of a specific zone and publish to MQTT"""
        device = self.devices[serial_no]
        
        try:
            response = await httpx_client.get(
                url=f"{device['device_url']}zones",
                timeout=self.TIMEOUT
            )
            self.devices[serial_no]['zones'] = response.json()
        except Exception as e:
            logging.error(f'Unable to get zone info for {serial_no}: {e}')
            return

        if self.mqtt_client.is_connected:
            try:
                zones = self.devices[serial_no]['zones']['zones']
                zone_idx = int(zone_no) - 1
                base_topic = f"{self.config['base_topic']}/{serial_no}"
                
                await self.mqtt_client.publish(
                    f'{base_topic}/zone_state/{zone_no}',
                    str(zones[zone_idx]['state']),
                    qos=self.config['mqtt_qos']
                )
                await self.mqtt_client.publish(
                    f'{base_topic}/zone_enabled/{zone_no}',
                    str(zones[zone_idx]['enabled']),
                    qos=self.config['mqtt_qos']
                )
            except Exception as e:
                logging.error(f'Unable to publish zone state: {e}')

    async def get_all_zones(self, serial_no, httpx_client):
        """Get state of all zones"""
        device = self.devices[serial_no]
        
        try:
            response = await httpx_client.get(
                url=f"{device['device_url']}zones",
                timeout=self.TIMEOUT
            )
            self.devices[serial_no]['zones'] = response.json()
            await self.publish_all_zones(serial_no)
            
            # Handle reconnection
            if device['retry_count'] > 0:
                logging.info(f"Reconnected to Audioflow device at {device['ip_addr']}")
            
            self.devices[serial_no]['retry_count'] = 0
            
            if self.mqtt_client.is_connected:
                await self.mqtt_client.publish(
                    f"{self.config['base_topic']}/{serial_no}/status",
                    'online',
                    qos=self.config['mqtt_qos'],
                    retain=True
                )
        except Exception as e:
            retry_count = device['retry_count']
            
            if retry_count < self.MAX_RETRIES:
                logging.error(f"Unable to communicate with device at {device['ip_addr']}: {e}")
            
            self.devices[serial_no]['retry_count'] += 1
            
            if retry_count == self.MAX_RETRIES - 1:
                if self.mqtt_client.is_connected:
                    await self.mqtt_client.publish(
                        f"{self.config['base_topic']}/{serial_no}/status",
                        'offline',
                        qos=self.config['mqtt_qos'],
                        retain=True
                    )
                logging.warning(f"Device at {device['ip_addr']} unreachable; marking as offline")
                logging.warning(f"Trying to reconnect to {device['ip_addr']} every 10 sec...")

    async def publish_all_zones(self, serial_no):
        """Publish state of all zones to MQTT"""
        device = self.devices[serial_no]
        zones = device['zones']['zones']
        
        if not self.mqtt_client.is_connected:
            return
        
        try:
            base_topic = f"{self.config['base_topic']}/{serial_no}"
            
            for idx in range(device['zone_count']):
                zone_num = idx + 1
                await self.mqtt_client.publish(
                    f'{base_topic}/zone_state/{zone_num}',
                    str(zones[idx]['state']),
                    qos=self.config['mqtt_qos']
                )
                await self.mqtt_client.publish(
                    f'{base_topic}/zone_enabled/{zone_num}',
                    str(zones[idx]['enabled']),
                    qos=self.config['mqtt_qos']
                )
        except Exception as e:
            logging.error(f'Unable to publish all zone states: {e}')

    async def set_zone_state(self, serial_no, zone_no, zone_state, httpx_client):
        """Change state of a specific zone"""
        device = self.devices[serial_no]
        zone_idx = int(zone_no) - 1
        
        if int(zone_no) > device['zone_count']:
            logging.warning(f'{zone_no} is an invalid zone number')
            return
        
        zones = device['zones']['zones']
        if zones[zone_idx]['enabled'] == 0:
            logging.warning(f'Zone {zone_no} is disabled')
            return
        
        if zone_state not in ['on', 'off', 'toggle']:
            logging.warning(f'"{zone_state}" is not valid. Use: on, off, toggle')
            return
        
        try:
            current_state = zones[zone_idx]['state']
            
            if zone_state in self.STATES:
                data = str(self.STATES.index(zone_state))
            else:  # toggle
                data = '1' if current_state == 'off' else '0'
            
            await httpx_client.put(
                url=f"{device['device_url']}zones/{zone_no}",
                data=data,
                timeout=self.TIMEOUT
            )
            await self.get_zone_state(serial_no, zone_no, httpx_client)
        except Exception as e:
            logging.error(f"Set zone state for device at {device['ip_addr']} failed: {e}")

    async def set_all_zone_states(self, serial_no, zone_state, httpx_client):
        """Turn all zones on or off"""
        device = self.devices[serial_no]
        
        if zone_state == 'toggle':
            logging.warning('Toggle command can only be used for one zone')
            return
        
        if zone_state not in self.STATES:
            logging.warning(f'"{zone_state}" is not valid. Use: on, off')
            return
        
        try:
            data = self.SET_ALL_ZONES[zone_state]
            await httpx_client.put(
                url=f"{device['device_url']}zones",
                data=data,
                timeout=self.TIMEOUT
            )
            await self.get_all_zones(serial_no, httpx_client)
        except Exception as e:
            logging.error(f"Set all zones for device at {device['ip_addr']} failed: {e}")

    async def set_zone_enable(self, serial_no, zone_no, zone_enable, httpx_client):
        """Enable or disable a zone"""
        device = self.devices[serial_no]
        zone_idx = int(zone_no) - 1
        
        if int(zone_enable) not in [0, 1]:
            logging.warning(f'Invalid enable value: {zone_enable}. Use 0 or 1')
            return
        
        try:
            # Audioflow expects zone name in payload when enabling/disabling
            zone_name = device['switch_names'][zone_idx].strip()
            payload = f'{zone_enable}{zone_name}'
            
            await httpx_client.put(
                url=f"{device['device_url']}zonename/{zone_no}",
                data=payload,
                timeout=self.TIMEOUT
            )
            await self.get_zone_state(serial_no, zone_no, httpx_client)
        except Exception as e:
            logging.error(f"Enable/disable zone for device at {device['ip_addr']} failed: {e}")

    async def poll_device_state(self, serial_no, httpx_client):
        """Poll device state every 10 seconds"""
        while True:
            await asyncio.sleep(10)
            await self.get_all_zones(serial_no, httpx_client)

    async def poll_network_info(self, serial_no, httpx_client):
        """Poll network information every 60 seconds"""
        while True:
            await asyncio.sleep(60)
            await self.get_network_info(serial_no, httpx_client)

    async def publish_ha_discovery(self, serial_no):
        """Send Home Assistant MQTT discovery payloads"""
        if not self.config['home_assistant']:
            return
        
        device = self.devices[serial_no]
        base_topic = self.config['base_topic']
        
        device_info = {
            'name': device['name'],
            'identifiers': serial_no,
            'manufacturer': 'Audioflow',
            'model': device['model'],
            'sw_version': device['version']
        }
        
        availability = [
            {'topic': f'{base_topic}/status'},
            {'topic': f'{base_topic}/{serial_no}/status'}
        ]
        
        try:
            # Switch entities for each zone
            for idx in range(device['zone_count']):
                zone_num = idx + 1
                zone = device['zone_info']['zones'][idx]
                zone_name = device['switch_names'][idx]
                
                name_suffix = ' (Disabled)' if zone['enabled'] == 0 else ''
                entity_name = f'{zone_name} speakers{name_suffix}'
                
                config = {
                    'availability': availability,
                    'name': entity_name,
                    'command_topic': f'{base_topic}/{serial_no}/set_zone_state/{zone_num}',
                    'state_topic': f'{base_topic}/{serial_no}/zone_state/{zone_num}',
                    'payload_on': 'on',
                    'payload_off': 'off',
                    'unique_id': f'{serial_no}{zone_num}',
                    'icon': 'mdi:speaker',
                    'device': device_info,
                    'platform': 'mqtt'
                }
                
                await self.mqtt_client.publish(
                    f'homeassistant/switch/{serial_no}/{zone_num}/config',
                    json.dumps(config),
                    qos=1,
                    retain=True
                )
            
            # Button entities for all zones on/off
            for state in ['off', 'on']:
                entity_name = f'Turn all zones {state}'
                
                config = {
                    'availability': availability,
                    'name': entity_name,
                    'command_topic': f'{base_topic}/{serial_no}/set_zone_state',
                    'payload_press': state,
                    'unique_id': f'{serial_no}_all_zones_{state}',
                    'icon': f'mdi:power-{state}',
                    'device': device_info,
                    'platform': 'mqtt'
                }
                
                await self.mqtt_client.publish(
                    f'homeassistant/button/{serial_no}/all_zones_{state}/config',
                    json.dumps(config),
                    qos=1,
                    retain=True
                )
            
            # Sensor entities for network info
            network_sensors = {
                'ssid': {'name': 'SSID', 'icon': 'mdi:access-point-network'},
                'channel': {'name': 'Wi-Fi channel', 'icon': 'mdi:access-point'},
                'rssi': {'name': 'RSSI', 'icon': 'mdi:signal'}
            }
            
            for key, info in network_sensors.items():
                config = {
                    'availability': availability,
                    'name': info['name'],
                    'state_topic': f'{base_topic}/{serial_no}/network_info/{key}',
                    'icon': info['icon'],
                    'unique_id': f'{serial_no}{key}',
                    'device': device_info,
                    'platform': 'mqtt'
                }
                
                await self.mqtt_client.publish(
                    f'homeassistant/sensor/{serial_no}/{key}/config',
                    json.dumps(config),
                    qos=1,
                    retain=True
                )
            
            logging.debug(f'Published HA discovery for {serial_no}')
        except Exception as e:
            logging.error(f'Unable to publish HA discovery payloads: {e}')


class MqttClient:
    """MQTT client wrapper with reconnection logic"""
    
    def __init__(self, config):
        self.config = config
        self.is_connected = False
        self.reconnect_attempts = 0
        self.reconnect_interval = 10
        self.client = None
        self.audioflow_device = None

    async def connect(self):
        """Connect to MQTT broker"""
        try:
            await self.client.publish(
                f"{self.config['base_topic']}/status",
                'online',
                qos=1,
                retain=True
            )
            logging.info('Connected to MQTT broker')
            self.is_connected = True
            self.reconnect_attempts = 0
        except aiomqtt.MqttError as e:
            logging.error(f'Unable to connect to MQTT broker: {e}')
            self.is_connected = False

    async def subscribe(self):
        """Subscribe to device topics"""
        try:
            for serial_no in self.audioflow_device.serial_nos:
                await self.client.publish(
                    f"{self.config['base_topic']}/{serial_no}/status",
                    'online',
                    qos=self.config['mqtt_qos'],
                    retain=True
                )
                await self.client.subscribe(f"{self.config['base_topic']}/{serial_no}/#")
            
            logging.debug('Subscribed to MQTT topics')
            self.is_connected = True
        except aiomqtt.MqttError as e:
            logging.error(f'Unable to subscribe to MQTT topics: {e}')
            self.is_connected = False

    async def publish_discovery(self):
        """Publish Home Assistant discovery payloads"""
        try:
            for serial_no in self.audioflow_device.serial_nos:
                await self.audioflow_device.publish_ha_discovery(serial_no)
            logging.debug('Published HA MQTT discovery payloads')
        except aiomqtt.MqttError as e:
            logging.error(f'Unable to publish MQTT discovery payloads: {e}')

    async def listen(self, httpx_client):
        """Listen for MQTT messages"""
        try:
            async for msg in self.client.messages:
                payload = msg.payload.decode('utf-8')
                topic = str(msg.topic)
                
                base_topic = self.config['base_topic']
                
                # Extract serial number from topic
                topic_parts = topic.split('/')
                serial_no = topic_parts[1] if len(topic_parts) > 1 else None
                
                if not serial_no:
                    continue
                
                if 'set_zone_state' in topic:
                    if topic.endswith('state'):  # All zones
                        await self.audioflow_device.set_all_zone_states(
                            serial_no, payload, httpx_client
                        )
                    else:  # Specific zone
                        zone_no = topic.split('/')[-1]
                        await self.audioflow_device.set_zone_state(
                            serial_no, zone_no, payload, httpx_client
                        )
                elif 'set_zone_enable' in topic:
                    zone_no = topic.split('/')[-1]
                    await self.audioflow_device.set_zone_enable(
                        serial_no, zone_no, payload, httpx_client
                    )
        except aiomqtt.MqttError:
            self.is_connected = False

    async def run(self, httpx_client):
        """Main MQTT client loop"""
        try:
            will = aiomqtt.Will(
                f"{self.config['base_topic']}/status",
                'offline',
                1,
                True
            )
            
            async with aiomqtt.Client(
                hostname=self.config['mqtt_host'],
                port=self.config['mqtt_port'],
                username=self.config['mqtt_user'],
                password=self.config['mqtt_password'],
                will=will
            ) as self.client:
                await self.connect()
                await self.subscribe()
                await self.publish_discovery()
                await self.listen(httpx_client)
        except aiomqtt.MqttError as e:
            logging.error(f'MQTT error: {e}')
            self.is_connected = False

    async def reconnect_loop(self, httpx_client):
        """Handle MQTT reconnection"""
        while True:
            await asyncio.sleep(self.reconnect_interval)
            
            if not self.is_connected:
                await self.run(httpx_client)
                
                if not self.is_connected:
                    if self.reconnect_attempts < 12:
                        self.reconnect_attempts += 1
                    
                    self.reconnect_interval = self.reconnect_attempts * 10
                    logging.error(
                        f'Attempting to reconnect to MQTT broker in '
                        f'{self.reconnect_interval} seconds...'
                    )

    async def publish(self, topic, payload, qos=0, retain=False):
        """Publish a message (wrapper for convenience)"""
        if self.client:
            await self.client.publish(topic, payload, qos=qos, retain=retain)


async def discover_devices(config):
    """Discover Audioflow devices on the network"""
    if config['device_ips']:
        logging.info(
            f"Device IP(s) configured; network discovery disabled: "
            f"{', '.join(config['device_ips'])}"
        )
        return config['device_ips']
    
    logging.info('No device IPs configured; starting network discovery...')
    
    discovery = NetworkDiscovery(config['discovery_port'])
    
    # Start listening in background thread
    listener_thread = Thread(target=discovery.receive_discovery, daemon=True)
    listener_thread.start()
    
    # Send discovery broadcasts
    discovery.send_discovery()
    
    if discovery.discovered_devices:
        logging.info(f"Network discovery complete: found {len(discovery.discovered_devices)} device(s)")
        discovery.close()
        return discovery.discovered_devices
    else:
        logging.error('No Audioflow devices found')
        logging.error('Ensure host networking is enabled and devices are on the same subnet')
        discovery.close()
        sys.exit(1)


async def main():
    """Main application entry point"""
    # Load configuration
    config = Config.load()
    
    # Setup logging
    log_level = config['log_level']
    if log_level not in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
        log_level = 'INFO'
        logging.warning(f"Invalid log level '{config['log_level']}'; using INFO")
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s %(levelname)s: %(message)s'
    )
    
    logging.info(f'=== audioflow2mqtt version {VERSION} started ===')
    logging.info(f"Configuration loaded from {config['config_source']}")
    
    # Validate MQTT configuration
    if not config['mqtt_host']:
        logging.error('MQTT broker host not specified')
        logging.error('Exiting...')
        sys.exit(1)
    
    # Discover devices
    device_ips = await discover_devices(config)
    
    # Initialize MQTT client
    mqtt_client = MqttClient(config)
    
    # Initialize Audioflow device manager
    audioflow = AudioflowDevice(config, mqtt_client)
    mqtt_client.audioflow_device = audioflow
    
    # Create HTTP client
    httpx_client = httpx.AsyncClient()
    
    # Discover all devices
    for ip in device_ips:
        await audioflow.discover_device(ip, httpx_client, from_discovery=not config['device_ips'])
    
    # Create polling tasks
    device_state_tasks = [
        audioflow.poll_device_state(sn, httpx_client)
        for sn in audioflow.serial_nos
    ]
    
    network_info_tasks = [
        audioflow.poll_network_info(sn, httpx_client)
        for sn in audioflow.serial_nos
    ]
    
    # Run everything concurrently
    await asyncio.gather(
        mqtt_client.run(httpx_client),
        mqtt_client.reconnect_loop(httpx_client),
        *device_state_tasks,
        *network_info_tasks
    )


if __name__ == '__main__':
    asyncio.run(main())
