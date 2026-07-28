#!/bin/bash
cd "$(dirname "$0")" || exit
openssl req -x509 -newkey rsa:2048 -nodes -keyout server.key -out server.crt -days 365 -subj "/CN=localhost"
