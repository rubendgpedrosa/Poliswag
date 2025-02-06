#!/bin/bash
#source $HOME/.bash_profile

LOGDIR="/root/Poliswag/logs"
LOGFILE="$LOGDIR/runtime.log"

# Ensure the log directory exists
mkdir -p $LOGDIR

# Check if the script is already running
if pgrep -f "/usr/bin/python3 /root/Poliswag/main.py" &>/dev/null; then
    exit
else
    # Redirect stdout and stderr to the log file, with timestamps
    {
        echo "Starting Poliswag at $(date)"
        /usr/bin/python3 /root/Poliswag/main.py prod
    } >> $LOGFILE 2>&1 &
fi
