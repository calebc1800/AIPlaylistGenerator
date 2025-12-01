FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=aiplaylist.settings \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static files (needed if using WhiteNoise)
RUN python src/manage.py collectstatic --noinput
# migrate
RUN python src/manage.py makemigrations
RUN python src/manage.py migrate


EXPOSE 8000
# move to the src directory to run gunicorn server
WORKDIR /app/src
CMD ["gunicorn", "aiplaylist.wsgi:application"]
