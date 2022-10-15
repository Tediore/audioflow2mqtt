# Audioflow to MQTT Gateway

audioflow2mqtt enables local control of your Audioflow speaker switch(es) via MQTT. It supports Home Assistant MQTT discovery for easy integration. It can also automatically discover the Audioflow devices on your network via UDP discovery, or you can specify the IP address of the Audioflow devices if you don't want to use UDP discovery.

<br>

# Configuration
audioflow2mqtt can be configured using environment variables or by using a configuration file named **config.yaml**. Example config.yaml with all possible configuration options:
```yaml
mqtt_host: 10.0.0.2
mqtt_port: 1883
mqtt_user: user
mqtt_password: password
mqtt_qos: 1
base_topic: audioflow2mqtt
home_assistant: True
device_ips:
- 10.0.1.100
discovery_port: 54321
log_level: debug
```

**Configuration options (please note that the variables are lowercase if using config.yaml):**

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `MQTT_HOST` | None | True |IP address or hostname of the MQTT broker to connect to. |
| `MQTT_PORT` | 1883 | True | The port the MQTT broker is bound to. |
| `MQTT_USER` | None | False | The user to send to the MQTT broker. |
| `MQTT_PASSWORD` | None | False | The password to send to the MQTT broker. |
| `MQTT_QOS` | 1 | False | The MQTT QoS level. |
| `BASE_TOPIC` | audioflow2mqtt | True | The topic prefix to use for all payloads. |
| `HOME_ASSISTANT` | True | False | Set to `True` to enable Home Assistant MQTT discovery or `False` to disable. |
| `DEVICE_IPS` | None | Depends* | IP address(es) of your Audioflow device(s). Must be a comma-separated string (if multiple). <br>\* Required if you don't plan to use UDP discovery. |
| `DISCOVERY_PORT` | 54321 | False | The port to open on the host to send/receive UDP discovery packets. |
| `LOG_LEVEL` | info | False | Set minimum log level. Valid options are `debug`, `info`, `warning`, and `error` |

<br>

# How to run

**Docker via `docker-compose` with config.yaml**

1. Create your docker-compose.yaml (or add to existing). Example docker-compose.yaml:
```yaml
version: '3'
services:
  audioflow2mqtt:
    container_name: audioflow2mqtt
    image: tediore/audioflow2mqtt:stable
    volumes:
    - /path/to/config.yaml:/config.yaml
    restart: unless-stopped
    network_mode: host # only required if device_ips is not set in config.yaml
```
2. `docker-compose up -d audioflow2mqtt`

<br>

**Docker via `docker run` with config.yaml**

Example `docker run` command:
```
docker run --name audioflow2mqtt \
-v /path/to/config.yaml:/config.yaml \
--network host \ # only required if device_ips is not set in config.yaml
tediore/audioflow2mqtt:stable
```

<br>

**Docker via `docker-compose` without config.yaml**

1. Create your docker-compose.yaml (or add to existing). Example docker-compose.yaml with all environmental variables:
```yaml
version: '3'
services:
  audioflow2mqtt:
    container_name: audioflow2mqtt
    image: tediore/audioflow2mqtt:stable
    environment:
    - MQTT_HOST=10.0.0.2
    - MQTT_PORT=1883
    - MQTT_USER=user
    - MQTT_PASSWORD=password
    - MQTT_QOS=1
    - BASE_TOPIC=audioflow2mqtt
    - HOME_ASSISTANT=True
    - DEVICE_IPS=10.0.1.100,10.0.1.101
    - DISCOVERY_PORT=54321
    - LOG_LEVEL=debug
    restart: unless-stopped
    network_mode: host # only required if DEVICE_IPS is not set
```
2. `docker-compose up -d audioflow2mqtt`

<br>

**Docker via `docker run` without config.yaml**

Example `docker run` command with all environment variables:
```
docker run --name audioflow2mqtt \
-e MQTT_HOST=10.0.0.2 \
-e MQTT_PORT=1883 \
-e MQTT_USER=user \
-e MQTT_PASSWORD=password \
-e MQTT_QOS=1 \
-e BASE_TOPIC=audioflow2mqtt \
-e HOME_ASSISTANT=True \
-e DEVICE_IPS=10.0.1.100,10.0.1.101 \
-e LOG_LEVEL=debug \
--network host \ # only required if DEVICE_IPS is not set
tediore/audioflow2mqtt:stable
```

<br>

**Bare metal (not recommended)**
1. Set the necessary environment variables or create config.yaml
2. `git clone https://github.com/Tediore/audioflow2mqtt`
3. `cd audioflow2mqtt`
4. `python3 audioflow2mqtt.py`

<br>

# Home Assistant
audioflow2mqtt supports Home Assistant MQTT discovery which creates a Device for the Audioflow switch and entities for each zone as well as sensors for SSID, RSSI (signal strength), and Wi-Fi channel.

![Home Assistant Device screenshot](ha_screenshot.png)

<br>

# MQTT topic structure and examples
The command topic syntax is `BASE_TOPIC/serial_number/command/zone_number` where `BASE_TOPIC` is the base topic you define, `serial_number` is the device serial number (found on the sticker on the bottom of the device), `command` is one of the below commands, and `zone_number` is the zone you want to control (zone A on the switch is zone number 1, zone B is zone number 2, and so on).

Valid commands are `set_zone_state` and `set_zone_enable`. The examples below assume the base topic is the default (`audioflow2mqtt`) and the serial number is `0123456789`.

**Turn zone B (zone number 2) on or off, or toggle between states**

Topic: `audioflow2mqtt/0123456789/set_zone_state/2`

Valid payloads: `on`, `off`, `toggle`

**Turn all zones on or off**

Topic: `audioflow2mqtt/0123456789/set_zone_state` (note the lack of a zone number at the end of the topic)

Valid payloads: `on`, `off`

**Enable or disable zone A (zone number 1)**
_This might not really be something you would need, but I figured I'd add it anyway_

Topic: `audioflow2mqtt/0123456789/set_zone_enable/1`

Valid payloads: `1` for enabled, `0` for disabled

<br>

When the zone state or enabled/disabled status is changed, audioflow2mqtt publishes the result to the following topics:

**Zone state:** `audioflow2mqtt/0123456789/zone_state/ZONE`

**Zone enabled/disabled:** `audioflow2mqtt/0123456789/zone_enabled/ZONE`

<br>

Network info is published to the following topics:

**SSID:** `audioflow2mqtt/0123456789/network_info/ssid`

**Wi-fi channel:** `audioflow2mqtt/0123456789/network_info/channel`

**RSSI:** `audioflow2mqtt/0123456789/network_info/rssi`

<br>

# Important notes
When running separate instances for multiple devices, you will need to set a **different base topic for each instance**. Also, while audioflow2mqtt does support UDP discovery of Audioflow devices, creating a DHCP reservation for your Audioflow device(s) and setting `DEVICE_IPS` is recommended. UDP discovery will only work if the Audioflow device is on the same subnet as the machine audioflow2mqtt is running on.

<br>
<a href="https://www.buymeacoffee.com/tediore" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="41" width="174"></a>

<br>

# TODO
1. ~~Handle Audioflow device disconnects/reconnects~~
2. Add support for re-discovery of Audioflow switch if its IP address changes
3. ~~Add support for multiple Audioflow switches? Not sure how many people would have more than one.~~
4. You tell me!