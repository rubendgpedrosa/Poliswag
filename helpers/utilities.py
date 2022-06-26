import requests, json, random
from datetime import datetime

import discord

import helpers.globals as globals

versionUrl = "https://pgorelease.nianticlabs.com/plfe/version"

def prepare_environment(env):
    if env == "prod":
        return ".env"
    elif env == "dev":
        return "dev.env"
    else:
        print("Invalid environment, usage: python3 main.py (dev|prod)")
        quit()

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
    with open(globals.LOG_FILE, 'a') as file:
        file.write(errorString)

def build_embed_object_title_description(title, description = "", footer = None):
    embed = discord.Embed(title=title, description=description, color=0x7b83b4)
    if footer != None:
        embed.set_footer(text=footer)
    return embed

def build_query(query):
    return f'mysql -u{globals.DB_USER} -p{globals.DB_PASSWORD} -D {globals.DB_NAME} -e "{query}"'

def log_actions(message):
    f = open(globals.LOG_FILE, "a")
    f.write("{0} -- {1}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M"), message))
    f.close()