#!/bin/bash

pip install -r requirements.txt

cd aiplaylist

python manage.py makemigrations
python manage.py migrate
python manage.py runserver