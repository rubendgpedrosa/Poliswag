import discord, datetime

import helpers.globals as globals

## VARIABLES FOR CACHING SYSTEM REGARDING SPAM
messagesReceived = []
messagesToDelete = {}
messagesFlaggedToBeDeleted = []
authorToMute = ""

def handle_mute(message, client):
    if message.content in messagesFlaggedToBeDeleted and str(message.author.id) not in globals.ADMIN_USERS_IDS:
        try:
            channel = client.get_channel(globals.MOD_CHANNEL_ID)
            user = message.author
            #await user.add_roles(muted_role, reason="Desiludiste o Lord Poliswag com o teu spam", atomic=True)
            embed = discord.Embed(title=str(user) + " levou mute por spam!", description=message.content, color=color)
            embed.set_thumbnail(url=user.avatar_url)
            embed.add_field(name="DiscordId", value=user.id, inline=False)
            #await channel.send(embed=embed)
            #await message.delete()
        except:
            print('Failed at initial message')
    elif len(message.content) > 8 and str(message.author.id) not in globals.ADMIN_USERS_IDS and message.channel.id != globals.QUEST_CHANNEL_ID:
        handleMessageReceived(message)

def handle_to_delete(message, client):
    if message.channel.id != globals.MOD_CHANNEL_ID and str(message.author.id) not in globals.ADMIN_USERS_IDS:
        toDelete = checkIfDuplicate(message)
        if toDelete and message.content not in messagesFlaggedToBeDeleted:
            messagesFlaggedToBeDeleted.append(message.content)

            for msgDel in messagesReceived:
                if msgDel["message"] in messagesFlaggedToBeDeleted:
                    try:
                        channelMsg = client.get_channel(msgDel["channelId"])
                        #msgd = await channelMsg.fetch_message(msgDel["messageId"])
                        #await msgd.delete()
                    except:
                        print('Failed deleting for loop')
            try:
                channel = client.get_channel(globals.MOD_CHANNEL_ID)
                user = message.author
                #await user.add_roles(muted_role, reason="Desiludiste o Lord Poliswag com o teu spam", atomic=True)
                embed = discord.Embed(title=str(user) + " levou mute por spam!", description=message.content, color=color)
                embed.set_thumbnail(url=user.avatar_url)
                embed.add_field(name="DiscordId", value=user.id, inline=False)
                #await channel.send(embed=embed)
            except:
                print('Already deleted')
            # clearMessages(msgDel["authorId"])

def handleMessageReceived(message):
    if len(messagesReceived) > 50:
        messagesReceived.pop(0)
    messagesReceived.append({ "authorId": message.author.id,"messageId": message.id,"message": message.content, "timestamp": datetime.now().timestamp(), "channelId": message.channel.id })

def checkIfDuplicate(message):
    counter = 0
    for msgRec in messagesReceived:
        if msgRec["message"] == message.content and message.author.id == msgRec["authorId"] and msgRec["timestamp"] - datetime.now().timestamp() < 5:
            counter += 1
    return counter > 2

def clearMessages(authorId):
    i = 0
    while i < len(messagesReceived):
        if messagesReceived[i]["authorId"] == authorId:
            messagesReceived.pop(i)
