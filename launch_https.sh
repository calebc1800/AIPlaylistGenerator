#!/bin/bash

pip install -r requirements.txt > /dev/null

# Move to the project src directory relative to this script
SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}/src" || exit 1

python manage.py makemigrations
python manage.py migrate
PYTHONPATH=. python3 scripts/seed_saved_playlists.py
yes yes | python manage.py collectstatic > /dev/null
python3 manage.py runserver_plus --cert-file cert.crt 0.0.0.0:8000
