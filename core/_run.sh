#!/bin/bash
cd "$(dirname "$0")" || exit
./nidus.out -i init.txt --serve 5200

