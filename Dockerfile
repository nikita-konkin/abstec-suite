FROM python:3.12-slim

WORKDIR /app

ENV WINEDEBUG=-all
ENV WINEPREFIX=/wine
ENV WINEARCH=win64
ENV XDG_RUNTIME_DIR=/tmp/runtime-root

RUN dpkg --add-architecture i386 \
 && apt-get update -o Acquire::Retries=5 -o Acquire::http::Timeout=30 \
 && apt-get install -y --no-install-recommends \
        wine \
        wine32:i386 \
        winbind \
        cabextract \
        fonts-wine \
 && mkdir -p $WINEPREFIX \
 && mkdir -p /tmp/runtime-root \
 && chmod 700 /tmp/runtime-root \
 && wineboot --init \
 && rm -rf /var/lib/apt/lists/*

COPY run_absoltec.py generate_absoltec_launchers.py /app/

ENTRYPOINT ["python", "/app/run_absoltec.py"]
CMD ["--help"]