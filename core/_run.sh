#!/bin/bash
cd "$(dirname "$0")" || exit
./nidus.out -i /etc/Nidus/init.txt --serve 5200

