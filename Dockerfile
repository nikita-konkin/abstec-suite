FROM python:3.12-slim
WORKDIR /app
ENV WINEDEBUG=-all
ENV WINEPREFIX=/wine
ENV WINEARCH=win64
ENV DISPLAY=:0
ENV XDG_RUNTIME_DIR=/tmp/runtime-root

RUN dpkg --add-architecture i386 \
 && apt-get update -o Acquire::Retries=5 -o Acquire::http::Timeout=30 \
 && apt-get install -y --no-install-recommends \
        wine \
        wine32:i386 \
        wine64 \
        winbind \
        cabextract \
        fonts-wine \
        xvfb \
 && mkdir -p $WINEPREFIX \
 && mkdir -p /tmp/runtime-root \
 && chmod 700 /tmp/runtime-root \
 && sh -c 'Xvfb :0 -screen 0 1024x768x16 &' \
 && sleep 2 \
 && wineboot --init \
 && wine reg add 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' /v Debugger /t REG_SZ /d "" /f \
 && wine reg add 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' /v Auto /t REG_SZ /d "1" /f \
 && rm -rf /var/lib/apt/lists/*

COPY run_absoltec.py generate_absoltec_launchers.py /app/
ENTRYPOINT ["python", "/app/run_absoltec.py"]
CMD ["--help"]