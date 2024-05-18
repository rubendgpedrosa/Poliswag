#!/bin/bash
#source $HOME/.bash_profile
if pgrep -f "python3 /root/Poliswag/main.py" &>/dev/null; then
    echo "it is already running"
    exit
else
    python3 /root/Poliswag/main.py prod &
fi
