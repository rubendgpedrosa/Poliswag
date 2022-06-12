import requests, json

import helpers.globals as globals

versionUrl = "https://pgorelease.nianticlabs.com/plfe/version"

def check_current_version():    
    response = requests.get(versionUrl)

    if (response.status_code == 200):
        retrievedVersion = response.text.strip().split(".",1)[1]
        if (globals.SAVED_VERSION != retrievedVersion):
            globals.SAVED_VERSION = retrievedVersion
            with open(globals.VERSION_FILE, 'w') as file:
                file.write(retrievedVersion)
            return True
        else:
            return False
    else:
        return False

def log_error(errorString):
    with open(globals.LOG_FILE, 'w') as file:
        file.write(errorString)