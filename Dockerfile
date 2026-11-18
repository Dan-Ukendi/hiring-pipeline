# jobpipe in a container: no Python to install on the host.
#
# The image holds the program only. Your folder (config/, local/, profile/, .env, data/,
# documents/) is mounted at /work, so the reports and the CVs land in your folder on the host.
# Usage: see "Avec Docker" in README.md, or docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    TZ=Europe/Brussels

# A TrueType font with accents for the built-in PDF renderer (LibreOffice is not needed).
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/jobpipe
COPY pyproject.toml ./
COPY src ./src
# tzdata: the Brussels time zone, which the slim image does not carry.
RUN pip install . tzdata && rm -rf /opt/jobpipe

WORKDIR /work
ENTRYPOINT ["jobpipe"]
CMD ["--help"]
