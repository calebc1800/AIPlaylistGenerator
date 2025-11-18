#!/bin/bash

pip install -r requirements.txt > /dev/null

cd src || exit

yes yes | python manage.py collectstatic > /dev/null
python manage.py makemigrations
python manage.py migrate
python3 manage.py runserver_plus --cert-file cert.crt 0.0.0.0:8888