import aiohttp
import json
import os
from modules.config import Config


async def fetch_data(endpoint_key, log_fn=None, timeout=20, method="GET", data=None):
    """
    Fetch data from a named endpoint defined in Config.ENDPOINTS.
    In DEV mode, returns mock JSON from the mock_data/ directory instead.
    Returns parsed JSON (dict/list/str) or None on failure.
    """

    def _log(msg, level="ERROR"):
        if log_fn:
            log_fn(msg, level)

    if not Config.IS_PRODUCTION and endpoint_key not in ["all_down", "events"]:
        mock_file_map = {
            "scanner_status": "scanner_status.json",
            "device_status": "device_status.json",
            "account_status": "account_status.json",
            "leiria_quest_scanning": "leiria_quest_scanning.json",
            "marinha_quest_scanning": "marinha_quest_scanning.json",
        }
        try:
            file_path = os.path.join(
                Config.MOCK_DATA_DIR, mock_file_map.get(endpoint_key, "default.json")
            )
            with open(file_path, "r") as f:
                return json.load(f)
        except Exception as e:
            _log(f"[DEV] Error loading mock data for {endpoint_key}: {e}")
            return None

    endpoint_url = Config.ENDPOINTS.get(endpoint_key)
    if not endpoint_url:
        _log(f"No URL defined for endpoint: {endpoint_key}")
        return None

    async with aiohttp.ClientSession() as session:
        try:
            async with session.request(
                method,
                endpoint_url,
                json=data,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")

                if "application/json" in content_type:
                    return await response.json()

                text = await response.text()
                if not text.strip():
                    _log(f"Empty response from {endpoint_key}")
                    return None

                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    if endpoint_key == "events" and text:
                        return text
                    _log(
                        f"Error decoding JSON from {endpoint_key}. Content-Type: {content_type}"
                    )
                    return None

        except aiohttp.ClientResponseError as e:
            _log(f"HTTP error from {endpoint_key}: {e.status} - {e.message}")
            return None
        except Exception as e:
            _log(f"Error fetching data from {endpoint_key}: {e}")
            return None
