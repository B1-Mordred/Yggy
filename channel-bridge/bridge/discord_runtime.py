from __future__ import annotations

import logging
from typing import Any

import discord

from .bragi_client import BragiClient, BragiClientError, build_discord_payload
from .config import BridgeSettings, ChannelConfig, resolve_discord_channel, split_discord_reply


logger = logging.getLogger(__name__)


class BragiDiscordClient(discord.Client):
    def __init__(self, *, settings: BridgeSettings, channels: list[ChannelConfig], bragi: BragiClient) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.channels = channels
        self.bragi = bragi

    async def on_ready(self) -> None:
        logger.info("discord bridge connected as %s", self.user)
        if self.settings.discord_nickname:
            for guild in self.guilds:
                try:
                    member = guild.me
                    if member and member.nick != self.settings.discord_nickname:
                        await member.edit(nick=self.settings.discord_nickname, reason="Yggy Bragi channel bridge identity")
                except Exception as exc:  # pragma: no cover - Discord permissions vary.
                    logger.warning("could not set Discord nickname in guild %s: %s", guild.id, exc.__class__.__name__)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        registry_channel_id = discord_registry_channel_id(message)
        author_id = str(message.author.id)
        try:
            resolve_discord_channel(
                self.channels,
                channel_id=registry_channel_id,
                author_id=author_id,
            )
        except (LookupError, PermissionError):
            return

        history = await self._history_for_message(message)
        payload = build_discord_payload(
            channel_id=registry_channel_id,
            author_id=author_id,
            author_name=getattr(message.author, "display_name", None),
            content=message.content or "",
            message_id=str(message.id),
            timestamp=message.created_at.isoformat() if message.created_at else None,
            is_bot=False,
            attachments=attachment_metadata(message),
            history=history,
        )
        try:
            result = await self.bragi.send_discord_message(payload)
        except BragiClientError as exc:
            logger.warning("Bragi rejected Discord message: %s", exc)
            await message.channel.send(
                "Bragi could not process that through the channel bridge. The gate remains shut, which is annoying but safer.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        except Exception as exc:  # pragma: no cover - network/runtime guard.
            logger.exception("Discord bridge failed to call Bragi: %s", exc.__class__.__name__)
            await message.channel.send(
                "Bragi is unreachable right now. The messenger tripped over the drawbridge.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        reply = str(result.get("reply") or "").strip()
        for chunk in split_discord_reply(reply, limit=self.settings.discord_reply_limit):
            await message.channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())

    async def _history_for_message(self, message: discord.Message) -> list[dict[str, Any]]:
        if self.settings.discord_history_limit <= 0:
            return []
        entries: list[dict[str, Any]] = []
        try:
            async for item in message.channel.history(limit=self.settings.discord_history_limit, before=message, oldest_first=False):
                content = (item.content or "").strip()
                if not content:
                    continue
                role = "assistant" if self.user and item.author.id == self.user.id else "user"
                entries.append({"role": role, "content": content[:6000]})
        except Exception as exc:  # pragma: no cover - Discord permissions vary.
            logger.warning("could not read Discord history: %s", exc.__class__.__name__)
            return []
        entries.reverse()
        return entries[-self.settings.discord_history_limit :]


def discord_registry_channel_id(message: discord.Message) -> str:
    parent_id = getattr(message.channel, "parent_id", None)
    return str(parent_id or message.channel.id)


def attachment_metadata(message: discord.Message) -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    for attachment in message.attachments:
        metadata.append(
            {
                "filename": attachment.filename,
                "size": attachment.size,
                "content_type": attachment.content_type,
            }
        )
    return metadata


def run_discord_bridge(settings: BridgeSettings, channels: list[ChannelConfig]) -> None:
    bragi = BragiClient(
        base_url=settings.bragi_base_url,
        api_key=settings.bragi_api_key,
        timeout=settings.http_timeout_seconds,
    )
    client = BragiDiscordClient(settings=settings, channels=channels, bragi=bragi)
    client.run(settings.discord_bot_token)
