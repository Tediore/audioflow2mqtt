FROM python:3

ADD audioflow2mqtt.py /

RUN pip install aiomqtt httpx paho.mqtt pyyaml

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD [ "python", "./audioflow2mqtt.py" ]
