FROM python:3

ADD audioflow2mqtt.py /

RUN pip install aiomqtt httpx paho.mqtt pyyaml

CMD [ "python", "./audioflow2mqtt.py" ]
