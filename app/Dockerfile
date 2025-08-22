# Use slim Python base (Debian bookworm to get unoconv availability)
FROM python:3.11-slim-bookworm

# Prevent interactive tzdata prompt and speed up pip
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    HOME=/tmp \
    UNO_PATH=/usr/lib/libreoffice/program \
    PYTHONPATH=/usr/lib/libreoffice/program

# Install LibreOffice and fonts needed for PDF conversion
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    libreoffice-core \
    libreoffice-common \
    python3-uno \
    unoconv \
    fonts-dejavu \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy dependency files first
COPY requirements.txt ./

# Install Python dependencies (use PEP 517 to avoid legacy setup.py builds)
RUN pip install --upgrade pip setuptools wheel build && \
    pip install --use-pep517 -r requirements.txt

# Copy the rest of the app
COPY . .

# Ensure start script is executable
RUN chmod +x /app/start.sh || true

# Cloud Run sets PORT env var
ENV PORT=8080
EXPOSE 8080

# Start LibreOffice listener then run Gunicorn
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-b", ":8080", "main:app"]

