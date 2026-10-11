"""Extract explicit delivery instructions without letting agents choose channels."""

import re
import unicodedata


def normalized_name(value):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(c)
    )


def delivery_reference(prompt):
    # Use the last delivery directive, so a later correction takes precedence.
    pattern = re.compile(
        r"\b(?:send|sent|sned|deliver|reply|respond|post|publish|envia\w*|manda\w*|"
        r"publica\w*|responde\w*)\s+(?:(?:it|this|me|the image|a imagem|isto)\s+)?"
        r"(?:(?:via|by|por|to|in|into|on|para|no|na|em|pelo)\s+)?"
        r"(?:(?:the\s+)?(?:channel|canal)\s+)?"
        r"(?P<target>DM\b|DMs\b|direct messages?\b|mensagem privada\b|"
        r"<#\d+>|#[\w-]+|moderação\b|moderacao\b)",
        re.I,
    )
    named_channel = re.compile(
        r"\b(?:send|sent|sned|deliver|reply|respond|post|publish|envia\w*|manda\w*|"
        r"publica\w*|responde\w*)\s+(?:(?:it|this|me|the image|a imagem|isto)\s+)?"
        r"(?:(?:via|by|por|to|in|into|on|para|no|na|em|pelo)\s+)?"
        r"(?:the\s+)?(?:channel|canal)\s+(?P<target>[\w-]+)",
        re.I,
    )
    matches = sorted(
        [*pattern.finditer(prompt), *named_channel.finditer(prompt)],
        key=lambda match: match.start(),
    )
    matches = [
        match
        for match in matches
        if not re.search(
            r"(?:do not|don't|dont|never|not|nao|nunca)\s+$",
            normalized_name(prompt[: match.start()]),
        )
    ]
    if not matches:
        return None
    target = matches[-1]["target"]
    if re.fullmatch(r"DMs?|direct messages?|mensagem privada", target, re.I):
        return "dm"
    return target
