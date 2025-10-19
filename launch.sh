#!/bin/bash

pip install -r requirements.txt

cd aiplaylist || exit

yes yes | python manage.py collectstatic
python manage.py makemigrations
python manage.py migrate
python manage.py runserver