FROM python:3.12.13-slim

WORKDIR /app

ENV WINEDEBUG=-all
ENV WINEPREFIX=/wine
ENV WINEARCH=win32
ENV DISPLAY=:0
ENV XDG_RUNTIME_DIR=/tmp/runtime-root
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt

RUN python -m pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

RUN dpkg --add-architecture i386 \
 && apt-get update -o Acquire::Retries=5 -o Acquire::http::Timeout=30 \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        cabextract \
        fonts-wine \
        xkb-data \
        xvfb \
        wine \
        wine32:i386 \
 && mkdir -p "$WINEPREFIX" /tmp/runtime-root \
 && chmod 700 /tmp/runtime-root \
 && rm -rf /var/lib/apt/lists/*

COPY run_absoltec.py /app/
COPY entrypoint.sh /app/entrypoint.sh
COPY TayAbsTEC_24.04.17 /data/workdir

RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD []
