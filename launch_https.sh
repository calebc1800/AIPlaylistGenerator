#!/bin/bash

pip install -r requirements.txt > /dev/null

cd src || exit

yes yes | python manage.py collectstatic > /dev/null
python manage.py makemigrations
python manage.py migrate
gunicorn aiplaylist.wsgi:application
