#!/bin/bash

pip install -r requirements.txt > /dev/null

# Move to the project src directory relative to this script
SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}/src" || exit 1

python manage.py makemigrations
python manage.py migrate
yes yes | python manage.py collectstatic > /dev/null

PYTHONPATH=. python3 scripts/seed_saved_playlists.py

gunicorn aiplaylist.wsgi:application