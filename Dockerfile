FROM python:3

ADD audioflow2mqtt.py /

RUN pip install paho.mqtt requests pyyaml

CMD [ "python", "./audioflow2mqtt.py" ]
