#!/bin/bash

pip install -r requirements.txt > /dev/null

cd src || exit

yes yes | python3 manage.py collectstatic > /dev/null
python3 manage.py makemigrations
python3 manage.py migrate
python3 scripts/seed_saved_playlists.py || exit 1
python3 manage.py runserver_plus --cert-file cert.crt 0.0.0.0:8000
