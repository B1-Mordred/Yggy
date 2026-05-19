from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

import discord

from .audit_client import ChannelAuditClient, build_channel_event
from .bragi_client import BragiClient, BragiClientError, build_discord_payload
from .config import BridgeSettings, ChannelConfig, env_ref_value, is_placeholder_value, resolve_discord_channel, split_discord_reply


logger = logging.getLogger(__name__)


class BragiDiscordClient(discord.Client):
    def __init__(
        self,
        *,
        settings: BridgeSettings,
        channels: list[ChannelConfig],
        bragi: BragiClient,
        audit: ChannelAuditClient | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.channels = channels
        self.bragi = bragi
        self.audit = audit
        self._followup_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        if self.settings.followups_enabled:
            self._followup_task = asyncio.create_task(self._followup_loop())

    async def close(self) -> None:
        if self._followup_task:
            self._followup_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._followup_task
        await super().close()

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
        registry_channel_id = discord_registry_channel_id(message)
        is_dm = discord_message_is_dm(message)
        author_id = str(message.author.id)
        if message.author.bot:
            await self._record_channel_event(
                build_channel_event(
                    channel_type="discord",
                    status="ignored",
                    channel_id=registry_channel_id,
                    author_id=author_id,
                    message_id=message.id,
                    blocked_reason="bot_author",
                    metadata={"is_bot": True},
                )
            )
            return
        channel: ChannelConfig | None = None
        try:
            channel = resolve_discord_channel(
                self.channels,
                channel_id=registry_channel_id,
                author_id=author_id,
                is_dm=is_dm,
            )
        except LookupError:
            await self._record_channel_event(
                build_channel_event(
                    channel_type="discord",
                    status="blocked",
                    channel_id=registry_channel_id,
                    author_id=author_id,
                    message_id=message.id,
                    blocked_reason="unknown_channel",
                    metadata={"reason": "discord channel is not registered or enabled", "is_dm": is_dm},
                )
            )
            return
        except PermissionError:
            await self._record_channel_event(
                build_channel_event(
                    channel_type="discord",
                    status="blocked",
                    channel_id=registry_channel_id,
                    author_id=author_id,
                    message_id=message.id,
                    blocked_reason="unauthorized_user",
                    metadata={"reason": "discord author is not allowed for this channel", "is_dm": is_dm},
                )
            )
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
            is_dm=is_dm,
            attachments=attachment_metadata(message),
            history=history,
        )
        try:
            result = await self.bragi.send_discord_message(payload)
        except BragiClientError as exc:
            logger.warning("Bragi rejected Discord message: %s", exc)
            await self._record_channel_event(
                build_channel_event(
                    channel_type="discord",
                    channel_config_id=channel.id,
                    channel_id=registry_channel_id,
                    author_id=author_id,
                    message_id=message.id,
                    request_preview=message.content or "",
                    status="rejected",
                    blocked_reason="bragi_rejected",
                    metadata={
                        "is_dm": is_dm,
                        "attachment_count": len(message.attachments),
                        "history_count": len(history),
                    },
                )
            )
            await message.channel.send(
                "Bragi could not process that through the channel bridge. The gate remains shut, which is annoying but safer.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        except Exception as exc:  # pragma: no cover - network/runtime guard.
            logger.exception("Discord bridge failed to call Bragi: %s", exc.__class__.__name__)
            await self._record_channel_event(
                build_channel_event(
                    channel_type="discord",
                    channel_config_id=channel.id,
                    channel_id=registry_channel_id,
                    author_id=author_id,
                    message_id=message.id,
                    request_preview=message.content or "",
                    status="failed",
                    blocked_reason="bragi_unreachable",
                    metadata={
                        "is_dm": is_dm,
                        "attachment_count": len(message.attachments),
                        "history_count": len(history),
                    },
                )
            )
            await message.channel.send(
                "Bragi is unreachable right now. The messenger tripped over the drawbridge.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        reply = str(result.get("reply") or "").strip()
        classification = result.get("classification") if isinstance(result.get("classification"), dict) else {}
        forwarded_to_yggdrasil = bool(classification.get("forwarded_to_yggdrasil"))
        await self._record_channel_event(
            build_channel_event(
                channel_type="discord",
                channel_config_id=str(result.get("channel_config_id") or channel.id),
                channel_id=registry_channel_id,
                author_id=author_id,
                message_id=message.id,
                request_preview=message.content or "",
                route=str(classification.get("route") or "") or None,
                required_capability=str(classification.get("required_capability") or "") or None,
                forwarded_to_yggdrasil=forwarded_to_yggdrasil,
                status="forwarded" if forwarded_to_yggdrasil else "replied",
                reply_preview=reply,
                metadata={
                    "is_dm": is_dm,
                    "attachment_count": len(message.attachments),
                    "history_count": len(history),
                    "requires_followup": bool(result.get("requires_followup")),
                },
            )
        )
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

    async def _record_channel_event(self, payload: dict[str, Any]) -> None:
        if not self.audit or not self.audit.enabled:
            return
        try:
            await self.audit.record_event(payload)
        except Exception as exc:  # pragma: no cover - audit must not block replies.
            logger.warning("could not record channel audit event: %s", exc.__class__.__name__)

    async def _followup_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self._send_due_followups()
            except asyncio.CancelledError:  # pragma: no cover - shutdown path.
                raise
            except Exception as exc:  # pragma: no cover - background guard.
                logger.warning("could not process Bragi followups: %s", exc.__class__.__name__)
            await asyncio.sleep(self.settings.followup_poll_seconds)

    async def _send_due_followups(self) -> None:
        audiences = sorted({channel.audience for channel in self.channels if channel.enabled and channel.type == "discord"})
        for audience in audiences:
            followups = await self.bragi.pending_followups(user_id=audience, channel="discord", limit=self.settings.followup_limit)
            for followup in followups:
                channel_config, discord_channel_id = self._followup_channel_for_user(str(followup.get("user_id") or audience))
                if not channel_config or not discord_channel_id:
                    continue
                target = await self._discord_channel(discord_channel_id)
                if not target:
                    continue
                message = str(followup.get("message") or "").strip()
                if not message:
                    continue
                for chunk in split_discord_reply(message, limit=self.settings.discord_reply_limit):
                    await target.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                intake_id = str(followup.get("intake_id") or "")
                user_id = str(followup.get("user_id") or audience)
                if intake_id:
                    await self.bragi.mark_followup_sent(user_id=user_id, intake_id=intake_id)
                await self._record_channel_event(
                    build_channel_event(
                        channel_type="discord",
                        channel_config_id=channel_config.id,
                        channel_id=discord_channel_id,
                        route="bragi_intake_followup",
                        required_capability="draft_task",
                        forwarded_to_yggdrasil=False,
                        status="replied",
                        reply_preview=message,
                        metadata={"intake_id": intake_id, "followup": True},
                    )
                )

    def _followup_channel_for_user(self, user_id: str) -> tuple[ChannelConfig | None, str]:
        for channel in self.channels:
            if not channel.enabled or channel.type != "discord" or channel.audience != user_id:
                continue
            channel_id = env_ref_value(channel.channel_id_ref)
            if not is_placeholder_value(channel_id):
                return channel, channel_id
        return None, ""

    async def _discord_channel(self, channel_id: str) -> Any | None:
        try:
            numeric_id = int(channel_id)
        except ValueError:
            return None
        channel = self.get_channel(numeric_id)
        if channel is not None:
            return channel
        try:
            return await self.fetch_channel(numeric_id)
        except Exception as exc:  # pragma: no cover - Discord permissions/network vary.
            logger.warning("could not fetch Discord followup channel: %s", exc.__class__.__name__)
            return None


def discord_registry_channel_id(message: discord.Message) -> str:
    parent_id = getattr(message.channel, "parent_id", None)
    return str(parent_id or message.channel.id)


def discord_message_is_dm(message: discord.Message) -> bool:
    return isinstance(message.channel, discord.DMChannel)


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
    audit = ChannelAuditClient(
        base_url=settings.automation_api_base_url,
        api_key=settings.automation_api_key,
        timeout=settings.http_timeout_seconds,
    )
    if not audit.enabled:
        logger.warning("channel audit logging is disabled because the automation-api key is missing")
    client = BragiDiscordClient(settings=settings, channels=channels, bragi=bragi, audit=audit)
    client.run(settings.discord_bot_token)
