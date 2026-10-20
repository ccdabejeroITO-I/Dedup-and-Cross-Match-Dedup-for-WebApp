#!/bin/sh
set -eu
mkdir -p /etc/nginx/certs
if [ ! -s /etc/nginx/certs/server.crt ] || [ ! -s /etc/nginx/certs/server.key ]; then
    openssl req -x509 -nodes -newkey rsa:3072 -days 365 \
        -keyout /etc/nginx/certs/server.key -out /etc/nginx/certs/server.crt \
        -subj '/CN=localhost/O=CrossMatch Local' \
        -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
    chmod 600 /etc/nginx/certs/server.key
fi
