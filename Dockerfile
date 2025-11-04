# Use Python 3.11 as base image for development
FROM python:3.11

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=aiplaylist.settings \
    PYTHONPATH=/app/src \
    DJANGO_DEBUG=True

# Set work directory
WORKDIR /app

# Install system dependencies and debug tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    git \
    curl \
    vim \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install development tools
RUN pip install --no-cache-dir \
    django-debug-toolbar \
    ipython \
    pytest-watch

# Copy project files
COPY . .

# Expose port
EXPOSE 8000

# Start Django development server
CMD ["python", "src/manage.py", "runserver", "0.0.0.0:8000"] 