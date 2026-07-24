#!/bin/bash
cd "$(dirname "$0")" || exit
python3 -m venv venv
#sudo chmod +x .env/bin/activate
source venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
#nuitka --onefile --jobs=1 dnfs.py

