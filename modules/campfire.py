"""Lê os nomes de treinador de capturas do Campfire (`!sorteio campfire`).

As capturas vão para o Claude (Messages API, visão) numa só chamada, com
saída JSON estruturada: `{"names": [...]}`. Leitura local (tesseract) foi
testada em 2026-11-10 e trocava letras em 6 a 10 de 57 nomes (`l/1/i`,
`0/@`), o que também estragava a deduplicação entre capturas.
"""

import base64
import json

import anthropic

from modules.config import Config

# Formatos de imagem que a API aceita, e o limite dela por imagem.
MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 10

PROMPT = (
    "Estas imagens são capturas da lista de participantes de um encontro do "
    "Campfire (Pokémon GO). Escreve todos os nomes de treinador que vês, em "
    "todas as colunas, de cima para baixo, exatamente como estão escritos "
    "(maiúsculas, números, espaços e hífenes incluídos). Ignora ícones, "
    "estrelas, títulos, botões e contadores. Se um nome aparece em mais de uma "
    "captura, escreve-o as vezes que aparecer: a deduplicação é feita depois."
)
SCHEMA = {
    "type": "object",
    "properties": {"names": {"type": "array", "items": {"type": "string"}}},
    "required": ["names"],
    "additionalProperties": False,
}


class CampfireError(Exception):
    """A leitura falhou; a mensagem diz porquê, em português."""


def enabled():
    return bool(Config.ANTHROPIC_API_KEY)


async def read_names(images, client=None):
    """Os nomes de todas as capturas (`[(bytes, media_type), ...]`), pela
    ordem em que aparecem, ainda com repetições."""
    if not images:
        return []
    if client is None:
        if not enabled():
            raise CampfireError("Falta a ANTHROPIC_API_KEY no .env do Poliswag.")
        # A chave de utilizador não pertence a um workspace: o cabeçalho diz
        # qual usar (o mesmo do Hermiswag).
        headers = (
            {"anthropic-workspace-id": Config.ANTHROPIC_WORKSPACE_ID}
            if Config.ANTHROPIC_WORKSPACE_ID
            else None
        )
        client = anthropic.AsyncAnthropic(
            api_key=Config.ANTHROPIC_API_KEY,
            default_headers=headers,
            timeout=90,
            max_retries=2,
        )
    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        }
        for data, media_type in images[:MAX_IMAGES]
    ]
    content.append({"type": "text", "text": PROMPT})
    try:
        response = await client.beta.messages.create(
            model=Config.CAMPFIRE_MODEL,
            max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIStatusError as error:
        raise CampfireError(f"A API respondeu {error.status_code}.") from error
    except anthropic.APIConnectionError as error:
        raise CampfireError("Não consegui falar com a API.") from error
    if response.stop_reason == "refusal":
        raise CampfireError("O modelo recusou ler a imagem.")
    if response.stop_reason == "max_tokens":
        raise CampfireError("Demasiados nomes numa só vez: envia menos capturas.")
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        names = json.loads(text)["names"]
    except (ValueError, KeyError, TypeError) as error:
        raise CampfireError("A resposta não trazia a lista de nomes.") from error
    return [str(n).strip() for n in names if str(n).strip()]
