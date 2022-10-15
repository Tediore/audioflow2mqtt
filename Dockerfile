FROM python:3

COPY qemu-arm-static /usr/bin/qemu-arm-static

ADD audioflow2mqtt.py /

RUN pip install paho.mqtt requests pyyaml

CMD [ "python", "./audioflow2mqtt.py" ]