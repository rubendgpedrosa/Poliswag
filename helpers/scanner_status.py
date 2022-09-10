import helpers.globals as globals
from helpers.utilities import build_embed_object_title_description

from helpers.utilities import build_query

boxUsersData = [
    {"owner": "Faynn", "boxes": ["Tx9s1", "a95xF1"], "mention": "98846248865398784"},
    {"owner": "JMBoy", "boxes": ["Tx9s1_JMBoy", "Tx9s2_JMBoy", "Tx9s3_JMBoy"], "mention": "308000681271492610"},
    {"owner": "Ethix", "boxes": ["Tx9s1_Ethix"], "mention": "313738342904627200"},
    {"owner": "Anakin", "boxes": ["Tx9s1_Anakin"], "mention": "339466204638871552"}
]

async def check_boxes_issues():
    # 4500 since it's 900 seconds + 1 hour (vps timezone differences)
    # execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, build_query("SELECT settings_device.name FROM trs_status LEFT JOIN settings_device ON trs_status.device_id = settings_device.device_id WHERE trs_status.device_id < 14 AND TIMESTAMPDIFF(SECOND, trs_status.lastProtoDateTime, NOW()) > 4500;"))
    # boxStatusResults = globals.DOCKER_CLIENT.exec_start(execId)
    # listBoxStatusResults = str(boxStatusResults).split("\\n").remove("b''")
    listBoxStatusResults = []
    if listBoxStatusResults is not None and len(listBoxStatusResults) > 1:
        for box in listBoxStatusResults:
            # Edge case where we replace this value since it's different in the db
            if box == "PoGoLeiria":
                box = "Tx9s3_JMBoy"
            # for boxuser in boxUsersData:
            #     if box in boxuser["boxes"]:
            #         user = globals.CLIENT.fetch_user(boxuser["mention"])
            #         await globals.CLIENT.send(user, "Box " + box + " precisa ser reiniciada.")
        await rename_voice_channel(len(listBoxStatusResults))
        return
    await rename_voice_channel(0)

#await rename_voice_channel(message.content)
async def rename_voice_channel(totalBoxesFailing):
    message = "SCANNER: 🟢"
    if totalBoxesFailing > 0 and totalBoxesFailing < 7:
        message = "SCANNER: 🟡"
    if totalBoxesFailing == 7:
        message = "SCANNER: 🔴"
    voiceChannel = globals.CLIENT.get_channel(globals.VOICE_CHANNEL_ID)
    if voiceChannel.name != message:
        await voiceChannel.edit(name=message)

async def check_map_status():
#70mins since the mysql timezone and vps timezone have an hour differente. 60mins + 10mins
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, build_query("SELECT last_scanned FROM trs_spawn WHERE last_scanned > NOW() - INTERVAL 70 MINUTE ORDER BY last_scanned DESC LIMIT 1;"))
    pokemonScanResults = globals.DOCKER_CLIENT.exec_start(execId)
    if len(str(pokemonScanResults).split("\\n")) == 1:
        globals.DOCKER_CLIENT.restart(globals.RUN_CONTAINER)
        globals.DOCKER_CLIENT.restart(globals.REDIS_CONTAINER)
        channel = globals.CLIENT.get_channel(globals.MOD_CHANNEL_ID)
        await channel.send(embed=build_embed_object_title_description(
            "ANOMALIA DETECTADA!", 
            "Reboot efetuado para corrigir anomalia na mapa"
            )
        )
