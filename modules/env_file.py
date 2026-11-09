"""Update one KEY=value line of the bot's .env in place (owner settings that
must survive a restart, such as OWNER_DM_DEFAULT). Other lines, comments and
the file mode are kept; the value is never logged."""

import os
import re
from pathlib import Path

_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


def set_env_value(key, value, path=".env"):
    if not _KEY.match(key) or "\n" in str(value):
        raise ValueError("invalid env key or value")
    file = Path(path)
    lines = file.read_text().splitlines() if file.exists() else []
    line = f"{key}={value}"
    for index, existing in enumerate(lines):
        if existing.split("=", 1)[0].strip() == key:
            lines[index] = line
            break
    else:
        lines.append(line)
    mode = file.stat().st_mode & 0o777 if file.exists() else 0o600
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, mode)
    tmp.replace(file)
