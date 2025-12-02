#!/bin/bash

pip install -r requirements.txt > /dev/null

cd src || exit
cd /workspaces/ai_playlist/AIPlaylistGenerator/src
PYTHONPATH=. python3 scripts/seed_saved_playlists.py
yes yes | python manage.py collectstatic > /dev/null
python manage.py makemigrations
python manage.py migrate
python3 manage.py runserver_plus --cert-file cert.crt 0.0.0.0:8000