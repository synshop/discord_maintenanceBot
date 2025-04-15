FROM python:3.13.1-alpine3.21
RUN mkdir /app
COPY . /app
WORKDIR /app
RUN apk add py3-pip
RUN pip3 install --no-cache --upgrade wheel
RUN pip3 install --no-cache --upgrade -r requirements.txt
CMD [ "python3", "./maintenance_bot.py"]