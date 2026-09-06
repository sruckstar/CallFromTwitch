"""
Paid Twitch events: channel-point redemptions and money donations.

Neither can be read the way chat is. A redemption arrives only over EventSub,
which wants the broadcaster's own OAuth token, and a money donation never
touches Twitch at all - it belongs to the service that took the payment
(DonationAlerts here).

Both live here rather than in the C# mod, which has neither a JSON parser nor
a websocket helper it could ship without dragging more DLLs into scripts\.
The sockets are held here, every event is normalised into one shape, and the
mod picks the result up with a plain HTTP GET.

The queue is small and lossy at the front: redemptions that piled up while the
game was closed are stale, and replaying them all at once would be worse.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("cft.events")
log.setLevel(logging.INFO)

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws"
HELIX_URL = "https://api.twitch.tv/helix"
DONATIONALERTS_WS_URL = "wss://centrifugo.donationalerts.com/connection/websocket"
DONATIONALERTS_API = "https://www.donationalerts.com/api/v1"


@dataclass
class Event:
    """
    One thing a viewer did that may become a phone call.

    kind is what the mod filters on: "points", "donation" or "bits".
    """

    kind: str
    user: str
    text: str
    amount: float = 0.0
    currency: str = ""
    reward: str = ""
    source: str = ""
    received_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "user": self.user,
            "text": self.text,
            "amount": self.amount,
            "currency": self.currency,
            "reward": self.reward,
            "source": self.source,
            "received_at": self.received_at,
        }


class EventQueue:
    """
    Thread-safe hand-off between the socket threads and the HTTP handlers.

    A plain list under a lock rather than asyncio.Queue: the producers run on
    an event loop of their own in a background thread, while the consumer is a
    synchronous FastAPI handler on the server's loop.
    """

    def __init__(self, max_size: int = 50) -> None:
        self._items: list[Event] = []
        self._lock = threading.Lock()
        self._max_size = max_size
        self._dropped = 0

    def put(self, event: Event) -> None:
        with self._lock:
            # The oldest, not the newest: an event from ten minutes ago has
            # missed its moment, the one that just arrived has not.
            while len(self._items) >= self._max_size:
                self._items.pop(0)
                self._dropped += 1
            self._items.append(event)
        log.info("event: %s from %s (%s) %r", event.kind, event.user,
                 event.reward or f"{event.amount:g}{event.currency}", event.text[:60])

    def drain(self, limit: int = 10) -> list[Event]:
        with self._lock:
            taken = self._items[:limit]
            del self._items[:limit]
            return taken

    def stats(self) -> dict:
        with self._lock:
            return {"pending": len(self._items), "dropped": self._dropped}


class _Worker:
    """A background thread running one asyncio loop for all socket clients."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._tasks: list[Callable[[], Any]] = []

    def add(self, coro_factory: Callable[[], Any]) -> None:
        self._tasks.append(coro_factory)

    def start(self) -> None:
        if not self._tasks or self._thread is not None:
            return

        self._thread = threading.Thread(target=self._run, name="cft.events", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        for factory in self._tasks:
            self._loop.create_task(factory())
        try:
            self._loop.run_forever()
        except Exception:
            log.exception("event loop died")


def _http_json(url: str, headers: dict, data: Optional[bytes] = None) -> dict:
    """
    One blocking JSON call, run off the loop with asyncio.to_thread.

    urllib rather than requests: three REST calls do not justify another
    dependency on top of torch and friends.
    """
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


class TwitchEventSub:
    """
    Channel-point redemptions, over Twitch's EventSub websocket.

    The order is fixed by Twitch: open the socket, wait for session_welcome to
    learn the session id, then call Helix to create the subscription naming
    that id. Subscribing before the welcome frame fails.

    The token must be the broadcaster's, not a bot account's, and must carry
    channel:read:redemptions.
    """

    def __init__(self, queue: EventQueue, client_id: str, token: str,
                 channel: str, reward_filter: str = "") -> None:
        self._queue = queue
        self._client_id = client_id.strip()
        self._token = token.strip().removeprefix("oauth:")
        self._channel = channel.strip().lstrip("#").lower()
        # Empty means every reward counts.
        self._reward_filter = reward_filter.strip().lower()
        self._user_id: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._token and self._channel)

    def _headers(self) -> dict:
        return {
            "Client-Id": self._client_id,
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _resolve_user_id(self) -> str:
        if self._user_id:
            return self._user_id

        url = f"{HELIX_URL}/users?login={urllib.parse.quote(self._channel)}"
        payload = await asyncio.to_thread(_http_json, url, self._headers())
        data = payload.get("data") or []
        if not data:
            raise RuntimeError(f"Twitch does not know a channel named '{self._channel}'")

        self._user_id = data[0]["id"]
        return self._user_id

    async def _subscribe(self, session_id: str) -> None:
        user_id = await self._resolve_user_id()
        body = json.dumps({
            "type": "channel.channel_points_custom_reward_redemption.add",
            "version": "1",
            "condition": {"broadcaster_user_id": user_id},
            "transport": {"method": "websocket", "session_id": session_id},
        }).encode("utf-8")

        await asyncio.to_thread(_http_json, f"{HELIX_URL}/eventsub/subscriptions",
                                self._headers(), body)
        log.info("EventSub: subscribed to channel-point redemptions on #%s", self._channel)

    async def run(self) -> None:
        import websockets

        url = EVENTSUB_WS_URL
        attempt = 0
        while True:
            try:
                async with websockets.connect(url, ping_interval=None) as socket:
                    attempt = 0
                    url = await self._pump(socket)
                    if url is None:
                        url = EVENTSUB_WS_URL
            except Exception as exc:
                attempt += 1
                delay = min(60, 2 ** min(attempt, 6))
                log.warning("EventSub disconnected (%s); retrying in %ss", exc, delay)
                url = EVENTSUB_WS_URL
                await asyncio.sleep(delay)

    async def _pump(self, socket) -> Optional[str]:
        """
        Reads one connection to its end. Returns a URL to reconnect to when
        Twitch asks us to move (session_reconnect), else None.
        """
        subscribed = False

        async for raw in socket:
            message = json.loads(raw)
            metadata = message.get("metadata", {})
            payload = message.get("payload", {})
            kind = metadata.get("message_type")

            if kind == "session_welcome":
                session_id = payload["session"]["id"]
                if not subscribed:
                    await self._subscribe(session_id)
                    subscribed = True
                continue

            if kind == "session_keepalive":
                # Twitch's liveness beat; it needs no reply.
                continue

            if kind == "session_reconnect":
                new_url = payload["session"].get("reconnect_url")
                log.info("EventSub: Twitch asked us to reconnect")
                return new_url

            if kind == "revocation":
                log.error("EventSub: subscription revoked (%s) - the token was "
                          "invalidated or the scope was removed",
                          payload.get("subscription", {}).get("status"))
                return None

            if kind == "notification":
                self._handle_notification(payload)

        return None

    def _handle_notification(self, payload: dict) -> None:
        event = payload.get("event", {})
        reward = (event.get("reward") or {}).get("title", "")

        if self._reward_filter and reward.strip().lower() != self._reward_filter:
            return

        self._queue.put(Event(
            kind="points",
            user=event.get("user_name") or event.get("user_login") or "viewer",
            # The reward's prompt box; a reward without one arrives empty.
            text=(event.get("user_input") or "").strip(),
            amount=float((event.get("reward") or {}).get("cost", 0)),
            currency="points",
            reward=reward,
            source="twitch",
        ))


class DonationAlerts:
    """
    Money donations, over the DonationAlerts Centrifugo socket.

    Twitch has no part in this. Getting onto the feed takes three steps: read
    the user's socket token from the REST API, connect and say that token,
    then ask DonationAlerts to sign the private channel subscription -
    Centrifugo will not let a client subscribe to a $-prefixed channel itself.

    The token is a DonationAlerts OAuth token with the
    oauth-donation-subscribe scope, unrelated to the Twitch token above.
    """

    def __init__(self, queue: EventQueue, token: str) -> None:
        self._queue = queue
        self._token = token.strip()

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def _profile(self) -> dict:
        payload = await asyncio.to_thread(_http_json, f"{DONATIONALERTS_API}/user/oauth",
                                          self._headers())
        return payload.get("data", {})

    async def run(self) -> None:
        import websockets

        attempt = 0
        while True:
            try:
                profile = await self._profile()
                user_id = profile["id"]
                socket_token = profile["socket_connection_token"]

                async with websockets.connect(DONATIONALERTS_WS_URL) as socket:
                    attempt = 0
                    await self._pump(socket, user_id, socket_token)
            except Exception as exc:
                attempt += 1
                delay = min(60, 2 ** min(attempt, 6))
                log.warning("DonationAlerts disconnected (%s); retrying in %ss", exc, delay)
                await asyncio.sleep(delay)

    async def _pump(self, socket, user_id: int, socket_token: str) -> None:
        # Centrifugo's protocol is id-matched request/response over one socket.
        await socket.send(json.dumps({"params": {"token": socket_token}, "id": 1}))
        welcome = json.loads(await socket.recv())
        client_id = welcome["result"]["client"]

        channel = f"$alerts:donation_{user_id}"
        signed = await asyncio.to_thread(
            _http_json,
            f"{DONATIONALERTS_API}/centrifuge/subscribe",
            self._headers(),
            json.dumps({"channels": [channel], "client": client_id}).encode("utf-8"),
        )
        token = signed["channels"][0]["token"]

        await socket.send(json.dumps({
            "params": {"channel": channel, "token": token},
            "method": 1,
            "id": 2,
        }))
        log.info("DonationAlerts: listening for donations")

        async for raw in socket:
            message = json.loads(raw)
            data = (message.get("result") or {}).get("data") or {}
            donation = data.get("data") if isinstance(data.get("data"), dict) else data
            if not donation or "username" not in donation:
                continue

            self._queue.put(Event(
                kind="donation",
                user=donation.get("username") or "Anonymous",
                text=(donation.get("message") or "").strip(),
                amount=float(donation.get("amount") or 0),
                currency=donation.get("currency") or "",
                source="donationalerts",
            ))


class EventHub:
    """
    Everything the mod can ask about, behind one object.

    Clients that are not configured never start, so nothing fails loudly for a
    feature nobody switched on.
    """

    def __init__(self, config) -> None:
        self.queue = EventQueue(max_size=config.events_max_queue)
        self._worker = _Worker()
        self._started: list[str] = []
        self._config = config

    def start(self) -> None:
        if not self._config.events_enabled:
            return

        try:
            import websockets  # noqa: F401
        except ImportError:
            log.warning("events: the 'websockets' package is missing - channel points and "
                        "donations are off. Install it with: pip install websockets")
            return

        eventsub = TwitchEventSub(
            self.queue,
            self._config.twitch_client_id,
            self._config.twitch_token,
            self._config.twitch_channel,
            self._config.points_reward,
        )
        if eventsub.configured:
            self._worker.add(eventsub.run)
            self._started.append("twitch-points")

        alerts = DonationAlerts(self.queue, self._config.donationalerts_token)
        if alerts.configured:
            self._worker.add(alerts.run)
            self._started.append("donationalerts")

        self._worker.start()
        if self._started:
            log.info("events: %s", ", ".join(self._started))

    @property
    def sources(self) -> list[str]:
        return list(self._started)

    def post_test(self, kind: str, user: str, text: str,
                  amount: float, currency: str, reward: str) -> Event:
        """
        Injects a synthetic event as though it had arrived from Twitch.

        A streamer cannot donate to themselves or buy their own channel
        points, so this is the only way to see the paid paths work before a
        paying viewer hits them. It travels the real queue by the real route.
        """
        event = Event(
            kind=kind,
            user=user,
            text=text,
            amount=amount,
            currency=currency or ("points" if kind == "points" else ""),
            reward=reward,
            source="test",
        )
        self.queue.put(event)
        return event
