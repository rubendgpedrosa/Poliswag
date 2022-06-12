def handle_event_roles(message):
    if message.channel.id == EVENT_CHANNEL_ID:
        if message.content.lower() == 'gold':
            event_role = discord.utils.get(message.guild.roles, name="Gold")
        elif message.content.lower() == 'silver':
            event_role = discord.utils.get(message.guild.roles, name="Silver")
        #elif str(message.author.id) not in ADMIN_USERS_IDS:
            #await message.delete()
    #await message.author.add_roles(event_role, atomic=True)
    #await message.add_reaction("✅")