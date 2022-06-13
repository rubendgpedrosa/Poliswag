import helpers.globals as globals

#await rename_voice_channel(message.content)
async def rename_voice_channel(name):
    await globals.CLIENT.get_channel(globals.VOICE_CHANNEL_ID).edit(name=name)
