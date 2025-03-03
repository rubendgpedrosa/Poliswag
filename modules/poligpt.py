# ⚠️🚨 OUTDATED FILE 🚨⚠️
# This file is no longer used/outdated and needs rework or removal.
# TODO: Refactor or remove this file.


from llama_index import GPTSimpleVectorIndex, SimpleDirectoryReader
import os
from datetime import datetime


async def load_index():
    if os.path.exists("/root/poliswag/data/index.json"):
        index = GPTSimpleVectorIndex.load_from_disk("/root/poliswag/data/index.json")
    else:
        documents = SimpleDirectoryReader("/root/poliswag/data").load_data()
        index = GPTSimpleVectorIndex.from_documents(documents)
        index.save_to_disk("/root/poliswag/data/index.json")
    return index


async def get_response(question):
    index = await load_index()
    current_time = datetime.now().strftime("%H:%M:%S")
    response = index.query(
        f"Data: {current_time}| Pergunta: {question}. Responde em Portugues, em bullet points. Nao traduzas items de jogo tais como lure, incense , raids, lucky egg e afins."
    )
    return response
