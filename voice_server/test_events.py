"""
Fakes a donation or a channel-point redemption, so the paid paths can be seen
working before a paying viewer ever hits them - a streamer cannot donate to
themselves or buy their own channel points.

Only the origin is faked: the event travels the real queue, the real endpoint
and the real thresholds, and ends up as the same phone call.

    python test_events.py                 # menu
    python test_events.py points "hello"  # one redemption
    python test_events.py donation 500 "thanks"
    python test_events.py bits 300 "good luck"
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8765"

# Read out by an American TTS voice, so the samples are English.
SAMPLES = [
    "Hey, it's me. Where are you right now?",
    "You better get down here, we have a problem.",
    "I just wanted to say thanks for the stream, man.",
    "Pick up the phone, it is important.",
]


def post(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/events/test",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def send(url: str, kind: str, amount: float, text: str,
         user: str = "TestViewer", currency: str = "", reward: str = "") -> bool:
    try:
        result = post(url, {
            "kind": kind, "user": user, "text": text,
            "amount": amount, "currency": currency, "reward": reward,
        })
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            print("  Сервер отказался: тестовые события выключены.")
            print("  Поставьте CFT_EVENTS_TEST=1 в voice_server\\events.env и перезапустите сервер.")
        elif exc.code == 404:
            print("  У сервера нет маршрута /events/test - это старая сборка voice_server.")
        else:
            print(f"  Сервер ответил {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")
        return False
    except urllib.error.URLError as exc:
        print(f"  Сервер не отвечает на {url}: {exc.reason}")
        print("  Запустите voice_server\\run_server.bat и повторите.")
        return False

    queued = result.get("queued", {})
    print(f"  Отправлено: {queued.get('kind')} от {queued.get('user')}, "
          f"в очереди {result.get('pending', '?')}")
    print(f"  Реплика: {queued.get('text')!r}")
    print("  Теперь в игре: телефон должен зазвонить через [Call] DelaySeconds секунд.")
    return True


def checklist(url: str) -> None:
    """What to check when the event was accepted but no call happened."""
    print()
    print("Если звонка нет, проверьте по порядку:")
    print("  1. В CallFromTwitch.ini: [Twitch] Enabled = true")
    print("  2. Там же включён нужный флаг - AllowPoints или AllowDonations")
    print("  3. Сумма не ниже MinDonation / MinBits")
    print("  4. Игра запущена, и мод загрузился (в чате должно было быть")
    print("     уведомление о подключении к Twitch при старте)")
    print("  5. Предыдущий звонок закончился - мод берёт по одной реплике за раз")
    print(f"  6. Сервер тот же, что в ini: {url}")


def menu(url: str) -> None:
    while True:
        print()
        print("=" * 62)
        print("  Эмулятор платных событий CallFromTwitch")
        print(f"  Сервер: {url}")
        print("=" * 62)
        print("  1. Баллы канала (channel points)")
        print("  2. Донат DonationAlerts")
        print("  3. Биты (Cheer)")
        print("  4. Награда без текста - проверить DefaultEventText")
        print("  5. Три события подряд - проверить очередь")
        print("  0. Выход")

        try:
            choice = input("\n  Выбор: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice == "0":
            return

        if choice == "5":
            print()
            for index in range(3):
                send(url, "points", 100, SAMPLES[index], user=f"Viewer{index + 1}",
                     reward="Call from Twitch")
            print("\n  Мод должен отыграть их по очереди, по одному звонку за раз.")
            continue

        if choice == "4":
            print()
            send(url, "points", 100, "", user="SilentViewer", reward="Call from Twitch")
            print("  Ожидается реплика из [Twitch] DefaultEventText.")
            continue

        if choice not in {"1", "2", "3"}:
            print("  Не понял.")
            continue

        kind = {"1": "points", "2": "donation", "3": "bits"}[choice]

        print()
        for index, sample in enumerate(SAMPLES, 1):
            print(f"    {index}. {sample}")
        raw = input("\n  Номер реплики, или впишите свою (по-английски): ").strip()

        if raw.isdigit() and 1 <= int(raw) <= len(SAMPLES):
            text = SAMPLES[int(raw) - 1]
        else:
            text = raw

        default_amount = {"points": 500, "donation": 100, "bits": 300}[kind]
        raw_amount = input(f"  Сумма [{default_amount}]: ").strip()
        try:
            amount = float(raw_amount) if raw_amount else float(default_amount)
        except ValueError:
            amount = float(default_amount)

        user = input("  Имя зрителя [TestViewer]: ").strip() or "TestViewer"

        print()
        ok = send(url, kind, amount, text, user=user,
                  currency="RUB" if kind == "donation" else "",
                  reward="Call from Twitch" if kind == "points" else "")
        if ok:
            checklist(url)


def main() -> int:
    args = sys.argv[1:]
    url = DEFAULT_URL

    # A URL anywhere in the arguments, for a server on another port.
    for arg in list(args):
        if arg.startswith("http"):
            url = arg
            args.remove(arg)

    if not args:
        menu(url)
        return 0

    kind = args[0].lower()
    if kind not in {"points", "donation", "bits"}:
        print(__doc__)
        return 2

    amount = 0.0
    text = ""
    rest = args[1:]

    if rest:
        try:
            amount = float(rest[0])
            rest = rest[1:]
        except ValueError:
            pass
    if rest:
        text = " ".join(rest)

    if amount == 0:
        amount = {"points": 500.0, "donation": 100.0, "bits": 300.0}[kind]
    if not text:
        text = SAMPLES[0]

    return 0 if send(url, kind, amount, text,
                     currency="RUB" if kind == "donation" else "",
                     reward="Call from Twitch" if kind == "points" else "") else 1


if __name__ == "__main__":
    sys.exit(main())
