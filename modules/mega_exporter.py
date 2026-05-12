import io
import json
import logging
import time
from pathlib import Path

import requests

from modules.config import Config

TEMP_EVO_LABELS = {1: "Mega", 2: "Mega X", 3: "Mega Y", 4: "Primal", 5: "Mega Z"}
GEN_LABELS = {
    "Kanto": "I",
    "Johto": "II",
    "Hoenn": "III",
    "Sinnoh": "IV",
    "Unova": "V",
    "Kalos": "VI",
    "Alola": "VII",
    "Galar": "VIII",
    "Paldea": "IX",
}
POKEAPI_BASE = "https://pokeapi.co/api/v2/pokemon"


def _key_to_pokeapi_slugs(key: str, ndex: int) -> list[str]:
    """Return PokeAPI slugs to try in order, falling back to base form by ndex."""
    if key.startswith("primal-"):
        name = key[len("primal-") :]
        return [f"{name}-primal", name, str(ndex)]
    name = key[len("mega-") :]
    if name.endswith("-x"):
        base = name[:-2]
        return [f"{base}-mega-x", f"{base}-mega", base, str(ndex)]
    if name.endswith("-y"):
        base = name[:-2]
        return [f"{base}-mega-y", f"{base}-mega", base, str(ndex)]
    return [f"{name}-mega", name, str(ndex)]


def _fetch_webp(key: str, ndex: int, dest: Path) -> bool:
    """Fetch official artwork from PokeAPI and save as WebP. Returns True on success."""
    for slug in _key_to_pokeapi_slugs(key, ndex):
        try:
            r = requests.get(f"{POKEAPI_BASE}/{slug}", timeout=10)
            if r.status_code != 200:
                continue
            url = (
                r.json()
                .get("sprites", {})
                .get("other", {})
                .get("official-artwork", {})
                .get("front_default")
            )
            if not url:
                continue
            img_bytes = requests.get(url, timeout=15).content
            from PIL import Image

            img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
            img.save(dest, "WEBP", quality=85)
            logging.info(
                f"MegaExporter: sprite {key} saved via {slug} ({dest.stat().st_size}b)"
            )
            return True
        except Exception as e:
            logging.debug(f"MegaExporter: sprite {key}/{slug} failed: {e}")
    logging.warning(f"MegaExporter: no sprite found for {key}, marking as skip")
    dest.with_suffix(".skip").touch()
    return False


class MegaExporter:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.output_path = Path(Config.MEGA_JSON_OUTPUT)
        self.sprites_dir = Path(Config.MEGA_SPRITES_DIR)

    def export(self):
        masterfile = getattr(self.poliswag.quest_search, "masterfile_data", None)
        if not masterfile or "pokemon" not in masterfile:
            logging.warning("MegaExporter: masterfile not loaded yet, skipping")
            return False

        entries = []
        for dex_id_str, poke in masterfile["pokemon"].items():
            temp_evos = poke.get("tempEvolutions", {})
            if not temp_evos:
                continue

            ndex = int(dex_id_str)
            base_name = poke.get("name", f"#{ndex}")
            generation = GEN_LABELS.get(poke.get("generation", ""), "?")
            base_types = [
                v.get("typeName")
                for v in poke.get("types", [])
                if isinstance(v, dict) and v.get("typeName")
            ]

            for tevo_id_str, tevo in temp_evos.items():
                tevo_id = int(tevo_id_str)
                label_prefix = TEMP_EVO_LABELS.get(tevo_id)
                if not label_prefix:
                    continue

                types = [
                    v.get("typeName")
                    for v in tevo.get("types", [])
                    if isinstance(v, dict) and v.get("typeName")
                ] or base_types

                if label_prefix in ("Mega X", "Mega Y", "Mega Z"):
                    variant = label_prefix[-1]
                    label = f"Mega {base_name} {variant}"
                else:
                    label = f"{label_prefix} {base_name}"

                key = label.lower().replace(" ", "-")
                entries.append(
                    {
                        "key": key,
                        "label": label,
                        "ndex": ndex,
                        "gen": generation,
                        "types": types,
                        "category": "primal" if label_prefix == "Primal" else "mega",
                        "released": tevo.get("firstEnergyCost") is not None,
                    }
                )

        entries.sort(key=lambda e: (e["ndex"], e["key"]))

        # Write JSON
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)
        logging.info(f"MegaExporter: wrote {len(entries)} entries → {self.output_path}")

        # Download sprites for any new entries
        self.sprites_dir.mkdir(parents=True, exist_ok=True)
        missing = [
            e
            for e in entries
            if not (self.sprites_dir / f"{e['key']}.webp").exists()
            and not (self.sprites_dir / f"{e['key']}.skip").exists()
        ]
        if missing:
            logging.info(f"MegaExporter: downloading {len(missing)} new sprite(s)")
            for e in missing:
                _fetch_webp(e["key"], e["ndex"], self.sprites_dir / f"{e['key']}.webp")
                time.sleep(0.4)

        return True
