import helpers.globals as globals
from helpers.utilities import build_embed_object_title_description

boxUsersData = [
    {"owner": "Faynn", "boxes": ["Tx9s1", "a95xF1"], "mention": "98846248865398784"},
    {"owner": "JMBoy", "boxes": ["Tx9s1_JMBoy", "Tx9s2_JMBoy", "Tx9s3_JMBoy"], "mention": "308000681271492610"},
    {"owner": "Ethix", "boxes": ["Tx9s1_Ethix"], "mention": "313738342904627200"},
    {"owner": "Anakin", "boxes": ["Tx9s1_Anakin"], "mention": "339466204638871552"}
]

async def check_boxes_issues():
    # execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, 'mysql -uroot -pStrongPassword -D rocketdb -e "SELECT settings_device.name FROM trs_status LEFT JOIN settings_device ON trs_status.device_id = settings_device.device_id WHERE trs_status.device_id < 14 AND TIMESTAMPDIFF(SECOND, trs_status.lastProtoDateTime, NOW()) > 900;"')
    # boxStatusResults = globals.DOCKER_CLIENT.exec_start(execId)
    # listBoxStatusResults = str(boxStatusResults).split("\\n").remove("b''")
    listBoxStatusResults = ["Tx9s1", "a95xF1"]
    if len(listBoxStatusResults) > 1:
        for box in listBoxStatusResults:
            # We replace this value since it's different in the db
            if box == "PoGoLeiria":
                box = "Tx9s3_JMBoy"
            for boxuser in boxUsersData:
                if box in boxuser["boxes"]:
                    user = await globals.CLIENT.fetch_user(boxuser["mention"])
                    message = await user.send(embed=build_embed_object_title_description("Box " + box + " está com problemas."))
        await rename_voice_channel(len(listBoxStatusResults))

#await rename_voice_channel(message.content)
async def rename_voice_channel(totalBoxesFailing):
    message = ""
    if totalBoxesFailing == 0:
        message = "Boxes com problemas: " + str(totalBoxesFailing)
    await globals.CLIENT.get_channel(globals.VOICE_CHANNEL_ID).edit(name=f"{totalBoxesFailing}")

async def check_map_status():
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, f'mysql -uroot -pStrongPassword -D rocketdb -e "SELECT last_scanned FROM trs_spawn WHERE last_scanned > NOW() - INTERVAL 10 MINUTE ORDER BY last_scanned DESC LIMIT 1;"')
    pokemonScanResults = globals.DOCKER_CLIENT.exec_start(execId)
    if len(str(pokemonScanResults).split("\\n")) != 1:
        globals.DOCKER.restart(globals.RUN_CONTAINER)
        globals.DOCKER.restart(globals.REDIS_CONTAINER)
        channel = globals.CLIENT.get_channel(globals.MOD_CHANNEL_ID)
        await channel.send(embed=build_embed_object_title_description(
            "ANOMALIA DETECTADA!", 
            "Reboot efetuado para corrigir anomalia na mapa"
            )
        )
