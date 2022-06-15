import os

import helpers.globals as globals

#await rename_voice_channel(message.content)
async def rename_voice_channel(name):
    await globals.CLIENT.get_channel(globals.VOICE_CHANNEL_ID).edit(name=name)

def restart_pokestop_scanning():
    os.system('docker stop pokemon_mad')
    os.system('docker exec -i pokemon_rocketdb mysql -uroot -pStrongPassword  <<< "use rocketdb; DELETE FROM pokemon WHERE disappear_time < DATE_SUB(NOW(), INTERVAL 48 HOUR); TRUNCATE TABLE trs_quest; TRUNCATE TABLE trs_visited;"')
    os.system('docker restart pokemon_mad')

# def start_pokemon_scanning():
#     os.system('docker stop pokemon_mad')
#     os.system('docker exec -i pokemon_rocketdb mysql -uroot -pStrongPassword  <<< "use rocketdb; UPDATE settings_device SET walker_id = 2 WHERE walker_id = 6; UPDATE settings_device SET walker_id = 7 WHERE  walker_id = 8;"')
#     os.system('docker start pokemon_mad')
