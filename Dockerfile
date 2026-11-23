FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    HOST=0.0.0.0 \
    PORT=8766

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -U yt-dlp

WORKDIR /app

COPY seamless_shorts_agent.py START_HERE.bat START_HERE.command README.txt /app/
COPY web/ /app/web/

RUN mkdir -p /app/shorts_workspace/1_source_shorts /app/shorts_workspace/2_repurposed_shorts \
    && python seamless_shorts_agent.py --make-demo-song

EXPOSE 8766

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8766}/api/health" || exit 1

CMD ["sh", "-c", "exec python seamless_shorts_agent.py --ui --no-browser --host \"${HOST:-0.0.0.0}\" --port \"${PORT:-8766}\""]
