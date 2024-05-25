#!/bin/bash
#source $HOME/.bash_profile
if pgrep -f "/usr/bin/python3 /root/Poliswag/main.py" &>/dev/null; then
    exit
else
    /usr/bin/python3 /root/Poliswag/main.py prod &
fi
