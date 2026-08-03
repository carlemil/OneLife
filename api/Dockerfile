FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Drop root. Nothing here needs it: the API listens on 8000 (unprivileged) and the
# only paths it writes are the /content and /games bind mounts, which Docker Desktop
# exposes world-writable. A remote-code bug then lands as a nobody, not as root with
# the daemon socket's blast radius. Fixed uid so the ownership is predictable if the
# mounts are ever hosted on Linux, where bind-mount permissions are real.
RUN useradd --create-home --uid 10001 onelife && chown -R onelife:onelife /app
USER onelife

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
