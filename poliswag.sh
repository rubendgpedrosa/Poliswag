#!/bin/bash
source $HOME/.bash_profile
if pgrep -f "python3 /root/poliswag/main.py" &>/dev/null; then
    echo "it is already running"
    exit
else
    python3 /root/poliswag/main.py prod &
fi
