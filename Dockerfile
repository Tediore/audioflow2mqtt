FROM python:3

COPY qemu-arm-static /usr/bin/qemu-arm-static

ADD audioflow2mqtt.py /

RUN python3 -m pip install paho.mqtt requests

CMD [ "python", "./audioflow2mqtt.py" ]
