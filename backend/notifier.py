# -*- coding: utf-8 -*-
"""Telegram: рассылка уведомлений всем подписчикам + команды (/status, /start, /stop).

Демо-режим открытого доступа: любой, кто написал боту, становится подписчиком
(получает уведомления) и может управлять (/start, /stop).
"""
import json
import logging
import socket
import urllib.parse
import urllib.request

from . import config, storage

log = logging.getLogger("notify")


def _pin_telegram_dns():
    """Обход блокировки api.telegram.org (RKN): резолвим только на рабочие IP.

    SNI/Host остаются api.telegram.org (валидный TLS), меняется только адрес
    подключения — как _PinnedIPTransport в крипто-боте. Пусто в env → патч выключен.
    """
    ips = [s.strip() for s in config.TELEGRAM_API_IPS.split(",") if s.strip()]
    if not ips:
        return
    orig = socket.getaddrinfo

    def patched(host, port, *args, **kwargs):
        if host == "api.telegram.org":
            out = []
            for ip in ips:
                out.extend(orig(ip, port, socket.AF_INET, socket.SOCK_STREAM))
            return out
        return orig(host, port, *args, **kwargs)

    socket.getaddrinfo = patched
    log.info("telegram DNS pinned to %s", ips)


_pin_telegram_dns()

_offset = 0  # offset для getUpdates
_TG_FILE = config.DATA_DIR / "telegram.json"


def _api(method: str, params: dict | None = None, timeout: int = 15) -> dict:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(url, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _load() -> dict:
    try:
        with open(_TG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict):
    with open(_TG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def subscribers() -> list[str]:
    data = _load()
    subs = list(data.get("subscribers") or [])
    if data.get("chat_id") and data["chat_id"] not in subs:
        subs.insert(0, data["chat_id"])  # владелец — первым
    return subs


def _subscribe(cid: str) -> bool:
    """Добавляет чат в подписчики. True если добавлен новый."""
    data = _load()
    subs = list(data.get("subscribers") or [])
    if cid in subs:
        return False
    subs.append(cid)
    data["subscribers"] = subs
    if not data.get("chat_id"):
        data["chat_id"] = cid  # первый обратившийся — получатель "статусных" send()
    _save(data)
    log.info("подписчик добавлен: %s (всего %d)", cid, len(subs))
    return True


def chat_id() -> str:
    return _load().get("chat_id", "")


def send(text: str):
    """Публикация в канал (если задан) либо рассылка подписчикам."""
    if not config.TELEGRAM_BOT_TOKEN:
        return
    if config.TELEGRAM_CHANNEL:
        try:
            _api("sendMessage", {"chat_id": config.TELEGRAM_CHANNEL, "text": text})
            return
        except Exception as e:  # noqa: BLE001
            log.warning("channel post failed (%s), fallback подписчикам: %s",
                        config.TELEGRAM_CHANNEL, e)
    subs = subscribers()
    if not subs:
        log.info("[TG skip, подписчиков нет] %s", text)
        return
    for cid in subs:
        try:
            _api("sendMessage", {"chat_id": cid, "text": text})
        except Exception as e:  # noqa: BLE001
            log.warning("telegram send %s failed: %s", cid, e)


def send_to(cid: str, text: str):
    """Ответ в конкретный чат."""
    if not config.TELEGRAM_BOT_TOKEN:
        return
    try:
        _api("sendMessage", {"chat_id": cid, "text": text})
    except Exception as e:  # noqa: BLE001
        log.warning("telegram send_to %s failed: %s", cid, e)


def send_photo(path, caption: str = ""):
    """Фото с подписью в канал (или первому подписчику при отсутствии канала)."""
    if not config.TELEGRAM_BOT_TOKEN:
        return
    import requests
    target = config.TELEGRAM_CHANNEL or (subscribers()[:1] or [None])[0]
    if not target:
        return
    try:
        with open(path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendPhoto",
                data={"chat_id": target, "caption": caption[:1024]},
                files={"photo": f}, timeout=30)
    except Exception as e:  # noqa: BLE001
        log.warning("send_photo failed: %s", e)


def poll_commands(bot):
    """Забирает команды Telegram. Вызывается из главного цикла."""
    global _offset
    if not config.TELEGRAM_BOT_TOKEN:
        return
    try:
        resp = _api("getUpdates", {"offset": _offset, "timeout": 0}, timeout=25)
    except Exception as e:  # noqa: BLE001
        log.warning("getUpdates failed: %s", e)
        return
    for upd in resp.get("result", []):
        _offset = upd["update_id"] + 1
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            continue
        cid = str(msg["chat"]["id"])
        if _subscribe(cid):
            send_to(cid, "✅ Подписка оформлена: будешь получать уведомления о сделках.\n"
                         "Команды: /status /start /stop /help")
        text = (msg.get("text") or "").strip().lower()
        if text.startswith("/start"):
            if bot.state.get("halted"):
                bot.resume()
            else:
                send_to(cid, "✅ Бот уже активен. /status — сводка")
        elif text.startswith("/help"):
            send_to(cid, "🤖 Forex grid bot — ДЕМО XAUUSD (PU Prime, открытый доступ)\n"
                         "/status — состояние\n/start — возобновить после стопа\n/stop — полный стоп\n"
                         "Все, кто пишет боту, получают уведомления о сделках")
        elif text.startswith("/status"):
            try:
                s = bot.status()
                send_to(cid,
                        "📊 Статус\n"
                        f"Equity: {s['account']['equity']:.2f} {s['account']['currency']} "
                        f"(баланс {s['account']['balance']:.2f})\n"
                        f"Реализованный PnL: {s['realized_pnl']:.2f}$ ({s['closed_pairs']} пар)\n"
                        f"Плавающий PnL: {s['floating_pnl']:.2f}$ ({s['positions']} позиций)\n"
                        f"Buy-уровней в сетке: {s['buy_limits']}\n"
                        f"Halted: {s['halted']} {s['halted_reason']}\n"
                        f"Тренд-пауза: {s['trend_paused']}, weekend flat: {s['weekend_flat']}")
            except Exception:  # noqa: BLE001
                st = storage.stats()
                send_to(cid, f"📊 Терминал выключен (бот в стопе). "
                             f"Реализованный PnL: {st['realized_pnl']:+.2f}$ ({st['closed_pairs']} пар)\n"
                             f"Подписчиков: {len(subscribers())}\n▶️ /start — поднять торговлю")
        elif text.startswith("/stop"):
            bot._halt("команда /stop из Telegram")
            bot.shutdown_terminal()
            send_to(cid, "🛑 Полный стоп: ордера отменены, позиции закрыты, терминал выключен.\n"
                         "▶️ /start — поднимет терминал и сетку заново")
        else:
            send_to(cid, "Команды: /status /start /stop /help")
