FROM tiangolo/uwsgi-nginx-flask:python3.7
ADD . /app
WORKDIR /app
RUN pip install -r requirements.txt
RUN python users_db.py
RUN pip install gunicorn