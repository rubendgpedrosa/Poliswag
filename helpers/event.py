import requests

from bs4 import BeautifulSoup
import discord

import helpers.globals as globals

def handle_event_roles(message):
    if message.channel.id == globals.EVENT_CHANNEL_ID:
        if message.content.lower() == 'gold':
            event_role = discord.utils.get(message.guild.roles, name="Gold")
        elif message.content.lower() == 'silver':
            event_role = discord.utils.get(message.guild.roles, name="Silver")
        #elif str(message.author.id) not in ADMIN_USERS_IDS:
            #await message.delete()
    #await message.author.add_roles(event_role, atomic=True)
    #await message.add_reaction("✅")

def fetch_events():
    html = requests.get('https://www.leekduck.com/events/')
    processedHTML = BeautifulSoup(html.text, 'html.parser')
    targetDivs = processedHTML.find_all("div", {"class": "event-text"})
    
    eventMessages = "__**EVENTOS ATIVOS E FUTUROS EVENTOS**__\n\n"
    for targetDiv in targetDivs:
        eventMessages = eventMessages + "**" + targetDiv.find("h2", {"class": ""}).text + "**" + "\n"
        eventMessages = eventMessages + "> " + targetDiv.find("p", {"class": ""}).text + "\n"

    return eventMessages
