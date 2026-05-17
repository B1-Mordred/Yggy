from __future__ import annotations

import logging
import sys

from .config import BridgeSettings, load_channel_configs


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = BridgeSettings.from_env()
    channels = load_channel_configs(settings.config_root)
    if not settings.discord_enabled:
        logging.info("Discord bridge disabled; exiting")
        return 0
    if not settings.discord_bot_token:
        logging.error("DISCORD_BOT_TOKEN is required for the Discord channel bridge")
        return 2
    if not settings.bragi_api_key:
        logging.error("CHANNEL_BRIDGE_BRAGI_API_KEY or BRAGI_API_KEY is required")
        return 2
    from .discord_runtime import run_discord_bridge

    run_discord_bridge(settings, channels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
