from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
from urllib.parse import quote, unquote, urlencode

_REQUIRED_PACKAGES = [
    ("aiohttp", "aiohttp"),
    ("aiohttp_socks", "aiohttp_socks"),
    ("requests", "requests"),
    ("pysteamauth", "pysteamauth"),
    ("colorama", "colorama"),
    ("qrcode", "qrcode"),
    ("PIL", "pillow"),
]
_missing_pkgs = []
for _mod, _pkg in _REQUIRED_PACKAGES:
    try:
        __import__(_mod)
    except ImportError:
        _missing_pkgs.append(_pkg)

if _missing_pkgs:
    print(f"[Steam Region Changer] Установка отсутствующих библиотек: {', '.join(_missing_pkgs)}...")
    _installed = False
    for _extra_args in [["--break-system-packages"], []]:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", *_missing_pkgs, "--quiet", "--no-warn-script-location", *_extra_args],
                timeout=180,
            )
            print("[Steam Region Changer] Библиотеки успешно установлены.")
            _installed = True
            break
        except Exception:
            continue
    if not _installed:
        print(f"[Steam Region Changer] Ошибка авто-установки. Установите вручную: pip install {' '.join(_missing_pkgs)}")

import requests
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Mapping, Optional

import aiohttp
from aiohttp import ClientResponse, ClientSession, FormData

import html
try:
    from colorama import Fore, Style
except ImportError:
    class _DummyColor:
        def __getattr__(self, name):
            return ""
    Fore = _DummyColor()
    Style = _DummyColor()
logger = logging.getLogger("FPC.SteamRegionChangerV2")
LP = "[Steam Region Changer v2]"

BASE_DIR = os.getcwd()
STORAGE_DIR = os.path.join(BASE_DIR, "storage", "plugins", "src_plugin_v2")
PLUGIN_LOG_DIR = STORAGE_DIR
PLUGIN_LOG_FILE = os.path.join(PLUGIN_LOG_DIR, "src_plugin_v2.log")
_ANSI_STRIP_RE = re.compile(r"(\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]))")

class _CleanFileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        return _ANSI_STRIP_RE.sub("", s)
try:
    os.makedirs(PLUGIN_LOG_DIR, exist_ok=True)
    if os.path.exists(PLUGIN_LOG_FILE) and os.path.getsize(PLUGIN_LOG_FILE) < 15 * 1024 * 1024:
        try:
            with open(PLUGIN_LOG_FILE, "r", encoding="utf-8", errors="ignore") as _f_clean:
                _content = _f_clean.read()
            if "\x1b" in _content:
                with open(PLUGIN_LOG_FILE, "w", encoding="utf-8") as _f_clean:
                    _f_clean.write(_ANSI_STRIP_RE.sub("", _content))
        except Exception:
            pass
    _fh = logging.FileHandler(PLUGIN_LOG_FILE, encoding="utf-8")
    _fh.setFormatter(_CleanFileFormatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    logger.addHandler(_fh)
except Exception:
    pass
def _mask_card_number(num: str) -> str:
    digits = re.sub(r"\D", "", str(num or ""))
    if len(digits) < 8:
        return "****"
    return f"{digits[:4]} **** **** {digits[-4:]}"
def _mask_proxy(p: str) -> str:
    s = str(p or "")
    s = re.sub(r':([^@:]+)@', ':***@', s)
    if "://" in s:
        return s
    parts = s.split(":")
    if len(parts) == 4 and not parts[0].lower().startswith(("http", "socks")):
        if parts[1].isdigit():
            return f"{parts[0]}:{parts[1]}:{parts[2]}:***"
        elif parts[3].isdigit():
            return f"{parts[0]}:***:{parts[2]}:{parts[3]}"
    return s
def _log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    tag = f"{Fore.CYAN}{LP}{Style.RESET_ALL}"
    ev_str = f"{Fore.YELLOW}event={event}{Style.RESET_ALL}"
    parts = [tag, ev_str]
    for k, v in fields.items():
        if v is None:
            continue
        v_str = str(v).replace('\r', ' ').replace('\n', ' ')
        kl = k.lower()
        if any(token in kl for token in ('password', 'secret', 'token', 'code', 'cvv', 'cvc', 'pan')):
            v_str = '***'
        elif 'card_num' in kl or kl == 'card_number':
            v_str = _mask_card_number(v_str)
        elif k in ('proxy', 'proxy_url'):
            v_str = _mask_proxy(v_str)
        if k in ('status', 'result'):
            if str(v).lower() in ('ok', 'success', 'alive', '200', 'fixed', 'cart_ready'):
                val_colored = f"{Fore.GREEN}{v_str}{Style.RESET_ALL}"
            else:
                val_colored = f"{Fore.RED}{v_str}{Style.RESET_ALL}"
        elif any(w in kl for w in ('err', 'fail', 'reason', 'error')):
            val_colored = f"{Fore.RED}{v_str}{Style.RESET_ALL}"
        elif k in ('ms', 'latency', 'latency_ms', 'ping'):
            val_colored = f"{Fore.MAGENTA}{v_str}{Style.RESET_ALL}"
        else:
            val_colored = f"{Fore.WHITE}{v_str}{Style.RESET_ALL}"
        parts.append(f"{Fore.LIGHTBLUE_EX}{k}={Style.RESET_ALL}{val_colored}")
    logger.log(level, " ".join(parts))
try:
    from tg_bot import CBT as _CBT
except Exception:
    _CBT = None
try:
    from telebot import types as tg_types
except Exception:
    tg_types = None
NAME = "Steam Region Changer v2"
VERSION = "1.0.0"
DESCRIPTION = "Смена региона Steam v2"
CREDITS = "@tinechelovec"
UUID = "9963777d-391b-4718-990e-6f2074f21a70"
SETTINGS_PAGE = True

_CBT_PLUGIN_SETTINGS = getattr(_CBT, 'PLUGIN_SETTINGS', None) if _CBT else None
CBT_SETTINGS = f"{_CBT_PLUGIN_SETTINGS}:{UUID}:0" if _CBT_PLUGIN_SETTINGS is not None else ""
CB_PLUGINS_LIST_OPEN = f"{getattr(_CBT, 'PLUGINS_LIST', '44')}:0" if _CBT else "44:0"

CREATOR_URL = "https://t.me/tinechelovec"
GROUP_URL = "https://t.me/dev_thc_chat"
CHANNEL_URL = "https://t.me/by_thc"
GITHUB_URL = "https://github.com/tinechelovec/FPC-Steam-Region-Changer-v2"
GITHUB_UPDATE_URL = os.getenv(
    "SRC_PLUGIN_V2_UPDATE_URL",
    "https://raw.githubusercontent.com/tinechelovec/FPC-Steam-Region-Changer-v2/main/SRCv2.py",
).strip()
INSTRUCTION_URL = "https://teletype.in/@tinechelovec/Steam-Region-Changer-v2"
POST_SWITCH_GUIDE_URL = "https://teletype.in/@tinechelovec/steam-switch"

REGION_REQUEST_TIMEOUT = float(os.getenv("REGION_REQUEST_TIMEOUT", "30"))
REGION_CONNECT_TIMEOUT = float(os.getenv("REGION_CONNECT_TIMEOUT", "15"))

REGION_WAIT_INTERVAL = 1.5
REGION_WAIT_TRIES = 3

REGION_MAX_WORKERS = 10

GuardProvider = Callable[[str], Awaitable[str]]

QRDisplay = Callable[[str], Awaitable[None]]

URL_SET_COUNTRY = "https://store.steampowered.com/country/setcountry"
URL_SET_COUNTRY_CHK = "https://checkout.steampowered.com/country/setcountry"
URL_ADDFUNDS = "https://store.steampowered.com/steamaccount/addfunds"
URL_STORE = "https://store.steampowered.com/"
URL_ACCOUNT = "https://store.steampowered.com/account/"
URL_USERDATA = "https://store.steampowered.com/dynamicstore/userdata/"
URL_SEARCH_PAGE = "https://store.steampowered.com/search/"
URL_SEARCH_RESULTS = "https://store.steampowered.com/search/results/"
URL_APPDETAILS = "https://store.steampowered.com/api/appdetails"
URL_CART = "https://store.steampowered.com/cart/"
URL_CART_ADD_FORM = "https://store.steampowered.com/cart/addtocart?redirect=1"
URL_CART_API_ADD = "https://api.steampowered.com/IAccountCartService/AddItemsToCart/v1/"
URL_CART_API_GET = "https://api.steampowered.com/IAccountCartService/GetCart/v1/"
URL_CART_API_DELETE = "https://api.steampowered.com/IAccountCartService/DeleteCart/v1/"
URL_ASYNC_CONFIG = "https://store.steampowered.com/pointssummary/ajaxgetasyncconfig"
URL_CHECKOUT_PAGE = "https://checkout.steampowered.com/checkout/?accountcart=1&purchasetype=self&snr=1_8_4__503"
URL_CHECKOUT_INIT = "https://checkout.steampowered.com/checkout/inittransaction/"
URL_CHECKOUT_FINAL_PRICE = "https://checkout.steampowered.com/checkout/getfinalprice/"
URL_CHECKOUT_FINALIZE = "https://checkout.steampowered.com/checkout/finalizetransaction/"
URL_CHECKOUT_STATUS = "https://checkout.steampowered.com/checkout/transactionstatus/"
URL_CHECKOUT_CANCEL = "https://checkout.steampowered.com/checkout/canceltransaction/"

RATE_LIMIT_DELAY = 30

MAX_PROXY_ATTEMPTS_FLOOR = 10
REGION_ATTEMPTS_PER_PROXY = max(1, int(os.getenv("SRC_ATTEMPTS_PER_PROXY", "2")))
REGION_MAX_TOTAL_ATTEMPTS = max(2, int(os.getenv("SRC_MAX_TOTAL_ATTEMPTS", "6")))

_RETRYABLE_HTTP = {503, 502, 504, 429, 520, 521, 522, 523, 524}

_EVENT_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("SRC_EVENT_WORKERS", "3"))),
    thread_name_prefix="SRC-EVENT",
)
_ORDER_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("SRC_ORDER_WORKERS", "4"))),
    thread_name_prefix="SRC-ORDER",
)
_EVENT_JOBS: dict[str, Any] = {}
_ORDER_JOBS: dict[str, Any] = {}
_EVENT_JOBS_LOCK = threading.Lock()
_ORDER_JOBS_LOCK = threading.Lock()
_CHAT_EVENT_LOCKS: dict[str, threading.RLock] = {}
_CHAT_EVENT_LOCKS_LOCK = threading.Lock()
_QR_CANCEL_EVENTS: dict[str, threading.Event] = {}
_QR_CANCEL_EVENTS_LOCK = threading.Lock()

def _get_or_create_qr_cancel_event(order_id: str) -> threading.Event:
    oid = str(order_id).strip().lstrip("#")
    with _QR_CANCEL_EVENTS_LOCK:
        evt = _QR_CANCEL_EVENTS.get(oid)
        if evt is None or evt.is_set():
            evt = threading.Event()
            _QR_CANCEL_EVENTS[oid] = evt
        return evt

def _cancel_qr_worker(order_id: str, reason: str = "") -> None:
    oid = str(order_id).strip().lstrip("#")
    if not oid:
        return
    with _QR_CANCEL_EVENTS_LOCK:
        evt = _QR_CANCEL_EVENTS.get(oid)
        if evt is not None and not evt.is_set():
            _log_event("qr_worker_cancel_requested", order_id=oid, reason=reason)
            evt.set()
    try:
        sess_lock = globals().get("_sessions_lock")
        buyer_sess = globals().get("_buyer_sessions")
        act_sess = globals().get("_active_sessions")
        if sess_lock is not None:
            with sess_lock:
                if isinstance(buyer_sess, dict):
                    for s in buyer_sess.values():
                        if str(s.get("order_id") or "").strip().lstrip("#") == oid:
                            c_evt = s.get("cancel_qr_event")
                            if c_evt and isinstance(c_evt, threading.Event) and not c_evt.is_set():
                                c_evt.set()
                if isinstance(act_sess, list):
                    for s in act_sess:
                        if str(s.get("order_id") or "").strip().lstrip("#") == oid:
                            c_evt = s.get("cancel_qr_event")
                            if c_evt and isinstance(c_evt, threading.Event) and not c_evt.is_set():
                                c_evt.set()
    except Exception:
        pass

def _get_chat_event_lock(key: str) -> threading.RLock:
    key = str(key or "global")
    with _CHAT_EVENT_LOCKS_LOCK:
        lock = _CHAT_EVENT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _CHAT_EVENT_LOCKS[key] = lock
        return lock

def _run_serialized_event(serial_key: str, fn, *args, **kwargs):
    lock = _get_chat_event_lock(serial_key)
    with lock:
        return fn(*args, **kwargs)

def _event_chat_key(event, order=None) -> str:
    chat_obj = getattr(event, "chat", None)
    for obj in (order, getattr(event, "order", None), getattr(event, "message", None), chat_obj, event):
        if obj is None:
            continue
        cid = getattr(obj, "chat_id", None)
        if cid is None and obj is chat_obj:
            cid = getattr(obj, "id", None)
        if cid is not None and str(cid).strip():
            return f"chat:{cid}"
    return "global"

def _message_event_identity(event) -> str:
    msg = getattr(event, "message", None)
    chat = getattr(event, "chat", None)
    mid = getattr(msg, "id", None) or getattr(chat, "node_msg_id", None) or getattr(chat, "user_msg_id", None)
    if mid is not None:
        return str(mid)
    txt = str(
        getattr(msg, "text", "")
        or getattr(msg, "content", "")
        or getattr(chat, "last_message_text", "")
        or getattr(event, "text", "")
        or getattr(event, "last_message_text", "")
        or getattr(event, "content", "")
        or ""
    )
    return str(abs(hash(txt))) if txt else str(id(event))

def _schedule_limited_job(executor, registry: dict[str, Any], registry_lock: threading.Lock, key: str, fn, *args, **kwargs) -> bool:
    with registry_lock:
        old = registry.get(key)
        if old is not None and not old.done():
            return False
        try:
            fut = executor.submit(fn, *args, **kwargs)
        except RuntimeError:
            return False
        registry[key] = fut

    def _cleanup(done_fut):
        with registry_lock:
            if registry.get(key) is done_fut:
                registry.pop(key, None)
        try:
            exc = done_fut.exception()
            if exc is not None:
                logger.error(f"{LP} background job failed key={key}: {exc}", exc_info=(type(exc), exc, exc.__traceback__))
        except Exception:
            pass

    fut.add_done_callback(_cleanup)
    return True

def _schedule_event_job(key: str, fn, *args, **kwargs) -> bool:
    return _schedule_limited_job(_EVENT_EXECUTOR, _EVENT_JOBS, _EVENT_JOBS_LOCK, key, fn, *args, **kwargs)

def _schedule_order_worker(cardinal, sess: dict[str, Any], mode: str = "login") -> bool:
    oid = str(sess.get("order_id") or "noid").strip().lstrip("#")
    key = f"{oid}:{mode}"
    other_key = f"{oid}:qr" if mode == "login" else f"{oid}:login"

    _cancel_qr_worker(oid, reason=f"reschedule_{mode}")

    for target_key in (key, other_key):
        with _ORDER_JOBS_LOCK:
            old = _ORDER_JOBS.get(target_key)
        if old is not None and not old.done():
            logger.info(f"[{NAME}] [#{oid}] Ожидание завершения предыдущего воркера {target_key}...")
            try:
                old.result(timeout=6.0)
            except Exception:
                pass
            with _ORDER_JOBS_LOCK:
                if _ORDER_JOBS.get(target_key) is old and old.done():
                    _ORDER_JOBS.pop(target_key, None)

    with _ORDER_JOBS_LOCK:
        if (key in _ORDER_JOBS and not _ORDER_JOBS[key].done()) or (other_key in _ORDER_JOBS and not _ORDER_JOBS[other_key].done()):
            _log_event("order_job_busy", level=logging.WARNING, order_id=oid, mode=mode, reason="old_worker_still_running")
            return False

    if mode == "qr":
        new_evt = threading.Event()
        with _QR_CANCEL_EVENTS_LOCK:
            _QR_CANCEL_EVENTS[oid] = new_evt
        sess["cancel_qr_event"] = new_evt

    fn = _run_qr_order_process if mode == "qr" else _run_order_process
    scheduled = _schedule_limited_job(_ORDER_EXECUTOR, _ORDER_JOBS, _ORDER_JOBS_LOCK, key, fn, cardinal, sess)
    if not scheduled:
        _log_event("order_job_busy", level=logging.DEBUG, order_id=oid, mode=mode)
    else:
        _log_event("order_job_scheduled", level=logging.DEBUG, order_id=oid, mode=mode)
    return scheduled

def format_eta(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s} сек"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m} мин" + (f" {sec} сек" if sec else "")
    h, m = divmod(m, 60)
    return f"{h} ч" + (f" {m} мин" if m else "")
def _is_network_error(exc: BaseException) -> bool:
    if isinstance(exc, (
        asyncio.TimeoutError,
        aiohttp.ServerConnectionError,
        aiohttp.ClientConnectorError,
        aiohttp.ClientOSError,
        aiohttp.ServerDisconnectedError,
        aiohttp.ClientPayloadError,
        ConnectionResetError,
        OSError,
    )):
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "proxy", "tunnel", "connection", "timeout",
        "reset by peer", "broken pipe", "eof",
    ))
def _is_proxy_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (
        asyncio.TimeoutError,
        aiohttp.ServerTimeoutError,
        aiohttp.ClientConnectorError,
        ConnectionResetError,
    )):
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "connection timeout", "connecttimeout", "timeouterror",
        "clientconnectortimeout", "clientconnectorerror", "proxy timeout",
        "connection reset", "timed out", "reset by peer", "broken pipe"
    ))
async def _net(coro_fn, *args, label: str = "", **kwargs):
    result = await coro_fn(*args, **kwargs)
    if hasattr(result, "status") and result.status in _RETRYABLE_HTTP:
        raise aiohttp.ClientResponseError(
            request_info=result.request_info,
            history=(),
            status=result.status,
            message=f"HTTP {result.status}",
        )
    return result
COUNTRY_NAMES: dict[str, str] = {
    "UA": "🇺🇦 Украина",
    "RU": "🇷🇺 Россия",
    "KZ": "🇰🇿 Казахстан",
    "US": "🇺🇸 США",
    "TR": "🇹🇷 Турция",
    "AR": "🇦🇷 Аргентина",
    "BR": "🇧🇷 Бразилия",
    "IN": "🇮🇳 Индия",
    "CN": "🇨🇳 Китай",
    "ID": "🇮🇩 Индонезия",
    "VN": "🇻🇳 Вьетнам",
    "PH": "🇵🇭 Филиппины",
    "MY": "🇲🇾 Малайзия",
    "TH": "🇹🇭 Таиланд",
    "JP": "🇯🇵 Япония",
    "KR": "🇰🇷 Южная Корея",
    "TW": "🇹🇼 Тайвань",
    "HK": "🇭🇰 Гонконг",
    "SG": "🇸🇬 Сингапур",
    "CA": "🇨🇦 Канада",
    "AU": "🇦🇺 Австралия",
    "NZ": "🇳🇿 Новая Зеландия",
    "GB": "🇬🇧 Великобритания",
    "EU": "🇪🇺 Еврозона",
    "CH": "🇨🇭 Швейцария",
    "PL": "🇵🇱 Польша",
    "NO": "🇳🇴 Норвегия",
    "MX": "🇲🇽 Мексика",
    "CL": "🇨🇱 Чили",
    "CO": "🇨🇴 Колумбия",
    "PE": "🇵🇪 Перу",
    "UY": "🇺🇾 Уругвай",
    "CR": "🇨🇷 Коста-Рика",
    "ZA": "🇿🇦 ЮАР",
    "SA": "🇸🇦 Саудовская Аравия",
    "AE": "🇦🇪 ОАЭ",
    "QA": "🇶🇦 Катар",
    "KW": "🇰🇼 Кувейт",
    "IL": "🇮🇱 Израиль",
    "DE": "🇩🇪 Германия (Еврозона)",
    "FR": "🇫🇷 Франция (Еврозона)",
    "ES": "🇪🇸 Испания (Еврозона)",
    "IT": "🇮🇹 Италия (Еврозона)",
    "NL": "🇳🇱 Нидерланды (Еврозона)",
    "AT": "🇦🇹 Австрия (Еврозона)",
    "BE": "🇧🇪 Бельгия (Еврозона)",
    "FI": "🇫🇮 Финляндия (Еврозона)",
    "IE": "🇮🇪 Ирландия (Еврозона)",
    "PT": "🇵🇹 Португалия (Еврозона)",
    "GR": "🇬🇷 Греция (Еврозона)",
    "SK": "🇸🇰 Словакия (Еврозона)",
    "EE": "🇪🇪 Эстония (Еврозона)",
    "LV": "🇱🇻 Латвия (Еврозона)",
    "LT": "🇱🇹 Литва (Еврозона)",
    "SI": "🇸🇮 Словения (Еврозона)",
    "CY": "🇨🇾 Кипр (Еврозона)",
    "MT": "🇲🇹 Мальта (Еврозона)",
    "LU": "🇱🇺 Люксембург (Еврозона)",
    "HR": "🇭🇷 Хорватия (Еврозона)",
    "SE": "🇸🇪 Швеция",
    "BY": "🇧🇾 Беларусь",
    "AZ": "🇦🇿 Азербайджан",
    "GE": "🇬🇪 Грузия",
    "ANY": "🌐 Любой регион",
}

EUROZONE_COUNTRIES: set[str] = {
    "EU", "DE", "FR", "ES", "IT", "NL", "AT", "BE", "FI", "IE",
    "PT", "GR", "SK", "EE", "LV", "LT", "SI", "CY", "MT", "LU", "HR",
}

REGION_CHEAP_PRICES: dict[str, dict[str, Any]] = {
    "UA": {"name": "🇺🇦 Украина", "steam_cc": "UA", "currency": "UAH", "symbol": "₴", "target_price": 13.0, "target_cents": 1300, "maxprice": 40, "label": "13 ₴"},
    "RU": {"name": "🇷🇺 Россия", "steam_cc": "RU", "currency": "RUB", "symbol": "₽", "target_price": 21.0, "target_cents": 2100, "maxprice": 40, "label": "21 ₽"},
    "KZ": {"name": "🇰🇿 Казахстан", "steam_cc": "KZ", "currency": "KZT", "symbol": "₸", "target_price": 150.0, "target_cents": 15000, "maxprice": 300, "label": "150 ₸"},
    "US": {"name": "🇺🇸 США", "steam_cc": "US", "currency": "USD", "symbol": "$", "target_price": 0.49, "target_cents": 49, "maxprice": 5, "label": "$0,49"},
    "TR": {"name": "🇹🇷 Турция", "steam_cc": "TR", "currency": "USD", "symbol": "$", "target_price": 0.49, "target_cents": 49, "maxprice": 5, "label": "$0,49"},
    "AR": {"name": "🇦🇷 Аргентина", "steam_cc": "AR", "currency": "USD", "symbol": "$", "target_price": 0.49, "target_cents": 49, "maxprice": 5, "label": "$0,49"},
    "BR": {"name": "🇧🇷 Бразилия", "steam_cc": "BR", "currency": "BRL", "symbol": "R$", "target_price": 1.74, "target_cents": 174, "maxprice": 5, "label": "R$ 1,74"},
    "IN": {"name": "🇮🇳 Индия", "steam_cc": "IN", "currency": "INR", "symbol": "₹", "target_price": 26.0, "target_cents": 2600, "maxprice": 60, "label": "₹26"},
    "CN": {"name": "🇨🇳 Китай", "steam_cc": "CN", "currency": "CNY", "symbol": "¥", "target_price": 3.0, "target_cents": 300, "maxprice": 10, "label": "¥3"},
    "ID": {"name": "🇮🇩 Индонезия", "steam_cc": "ID", "currency": "IDR", "symbol": "Rp", "target_price": 4850.0, "target_cents": 485000, "maxprice": 10000, "label": "≈ Rp 4 850"},
    "VN": {"name": "🇻🇳 Вьетнам", "steam_cc": "VN", "currency": "VND", "symbol": "₫", "target_price": 7500.0, "target_cents": 750000, "maxprice": 20000, "label": "7 500 ₫"},
    "PH": {"name": "🇵🇭 Филиппины", "steam_cc": "PH", "currency": "PHP", "symbol": "₱", "target_price": 17.98, "target_cents": 1798, "maxprice": 40, "label": "≈ ₱17,98"},
    "MY": {"name": "🇲🇾 Малайзия", "steam_cc": "MY", "currency": "MYR", "symbol": "RM", "target_price": 1.50, "target_cents": 150, "maxprice": 5, "label": "RM1,50"},
    "TH": {"name": "🇹🇭 Таиланд", "steam_cc": "TH", "currency": "THB", "symbol": "฿", "target_price": 11.50, "target_cents": 1150, "maxprice": 30, "label": "฿11,50"},
    "JP": {"name": "🇯🇵 Япония", "steam_cc": "JP", "currency": "JPY", "symbol": "¥", "target_price": 60.0, "target_cents": 6000, "maxprice": 150, "label": "¥60"},
    "KR": {"name": "🇰🇷 Южная Корея", "steam_cc": "KR", "currency": "KRW", "symbol": "₩", "target_price": 550.0, "target_cents": 55000, "maxprice": 1500, "label": "₩550"},
    "TW": {"name": "🇹🇼 Тайвань", "steam_cc": "TW", "currency": "TWD", "symbol": "NT$", "target_price": 11.0, "target_cents": 1100, "maxprice": 30, "label": "NT$11"},
    "HK": {"name": "🇭🇰 Гонконг", "steam_cc": "HK", "currency": "HKD", "symbol": "HK$", "target_price": 3.50, "target_cents": 350, "maxprice": 10, "label": "HK$3,50"},
    "SG": {"name": "🇸🇬 Сингапур", "steam_cc": "SG", "currency": "SGD", "symbol": "S$", "target_price": 0.55, "target_cents": 55, "maxprice": 5, "label": "S$0,55"},
    "CA": {"name": "🇨🇦 Канада", "steam_cc": "CA", "currency": "CAD", "symbol": "C$", "target_price": 0.64, "target_cents": 64, "maxprice": 5, "label": "C$0,64"},
    "AU": {"name": "🇦🇺 Австралия", "steam_cc": "AU", "currency": "AUD", "symbol": "A$", "target_price": 0.75, "target_cents": 75, "maxprice": 5, "label": "A$0,75"},
    "NZ": {"name": "🇳🇿 Новая Зеландия", "steam_cc": "NZ", "currency": "NZD", "symbol": "NZ$", "target_price": 0.74, "target_cents": 74, "maxprice": 5, "label": "NZ$0,74"},
    "GB": {"name": "🇬🇧 Великобритания", "steam_cc": "GB", "currency": "GBP", "symbol": "£", "target_price": 0.44, "target_cents": 44, "maxprice": 5, "label": "£0,44"},
    "EU": {"name": "🇪🇺 Еврозона", "steam_cc": "DE", "currency": "EUR", "symbol": "€", "target_price": 0.49, "target_cents": 49, "maxprice": 5, "label": "€0,49"},
    "CH": {"name": "🇨🇭 Швейцария", "steam_cc": "CH", "currency": "CHF", "symbol": "CHF", "target_price": 0.54, "target_cents": 54, "maxprice": 5, "label": "CHF 0,54"},
    "PL": {"name": "🇵🇱 Польша", "steam_cc": "PL", "currency": "PLN", "symbol": "zł", "target_price": 2.24, "target_cents": 224, "maxprice": 10, "label": "2,24 zł"},
    "NO": {"name": "🇳🇴 Норвегия", "steam_cc": "NO", "currency": "NOK", "symbol": "kr", "target_price": 5.50, "target_cents": 550, "maxprice": 15, "label": "5,50 kr"},
    "MX": {"name": "🇲🇽 Мексика", "steam_cc": "MX", "currency": "MXN", "symbol": "Mex$", "target_price": 6.69, "target_cents": 669, "maxprice": 20, "label": "Mex$6,69"},
    "CL": {"name": "🇨🇱 Чили", "steam_cc": "CL", "currency": "CLP", "symbol": "CLP$", "target_price": 310.0, "target_cents": 31000, "maxprice": 1000, "label": "CLP$310"},
    "CO": {"name": "🇨🇴 Колумбия", "steam_cc": "CO", "currency": "COP", "symbol": "COL$", "target_price": 1400.0, "target_cents": 140000, "maxprice": 3500, "label": "COL$1 400"},
    "PE": {"name": "🇵🇪 Перу", "steam_cc": "PE", "currency": "PEN", "symbol": "S/", "target_price": 1.25, "target_cents": 125, "maxprice": 5, "label": "S/1,25"},
    "UY": {"name": "🇺🇾 Уругвай", "steam_cc": "UY", "currency": "UYU", "symbol": "$U", "target_price": 16.0, "target_cents": 1600, "maxprice": 40, "label": "$U16"},
    "CR": {"name": "🇨🇷 Коста-Рика", "steam_cc": "CR", "currency": "CRC", "symbol": "₡", "target_price": 250.0, "target_cents": 25000, "maxprice": 600, "label": "₡250"},
    "ZA": {"name": "🇿🇦 ЮАР", "steam_cc": "ZA", "currency": "ZAR", "symbol": "R", "target_price": 5.50, "target_cents": 550, "maxprice": 15, "label": "R5,50"},
    "SA": {"name": "🇸🇦 Саудовская Аравия", "steam_cc": "SA", "currency": "SAR", "symbol": "SR", "target_price": 1.37, "target_cents": 137, "maxprice": 5, "label": "≈ 1,37 SR"},
    "AE": {"name": "🇦🇪 ОАЭ", "steam_cc": "AE", "currency": "AED", "symbol": "AED", "target_price": 1.50, "target_cents": 150, "maxprice": 5, "label": "1,50 AED"},
    "QA": {"name": "🇶🇦 Катар", "steam_cc": "QA", "currency": "QAR", "symbol": "QR", "target_price": 1.39, "target_cents": 139, "maxprice": 5, "label": "≈ 1,39 QR"},
    "KW": {"name": "🇰🇼 Кувейт", "steam_cc": "KW", "currency": "KWD", "symbol": "KD", "target_price": 0.10, "target_cents": 10, "maxprice": 2, "label": "0,10 KD"},
    "IL": {"name": "🇮🇱 Израиль", "steam_cc": "IL", "currency": "ILS", "symbol": "₪", "target_price": 1.75, "target_cents": 175, "maxprice": 5, "label": "₪1,75"},
}

REGION_CODES_LIST: list[str] = list(REGION_CHEAP_PRICES.keys())

REGION_DEFAULT_BILLING: dict[str, dict[str, str]] = {
    "UA": {"first": "Oleksandr", "last": "Kovalenko", "address": "Khreshchatyk St 22", "city": "Kyiv", "state": "", "zip": "01001", "phone": "+380442345678"},
    "RU": {"first": "Alexey", "last": "Smirnov", "address": "Tverskaya St 10", "city": "Moscow", "state": "", "zip": "125009", "phone": "+74951234567"},
    "KZ": {"first": "Nurlan", "last": "Akhmetov", "address": "Abay Ave 52", "city": "Almaty", "state": "", "zip": "050008", "phone": "+77272345678"},
    "US": {"first": "John", "last": "Miller", "address": "350 5th Ave", "city": "New York", "state": "NY", "zip": "10118", "phone": "+12125550199"},
    "TR": {"first": "Mehmet", "last": "Yilmaz", "address": "Istiklal Cad 45", "city": "Istanbul", "state": "", "zip": "34430", "phone": "+902122345678"},
    "AR": {"first": "Lucas", "last": "Gomez", "address": "Av Corrientes 1200", "city": "Buenos Aires", "state": "", "zip": "C1043", "phone": "+541143215678"},
    "BR": {"first": "Rafael", "last": "Silva", "address": "Av Paulista 1000", "city": "Sao Paulo", "state": "SP", "zip": "01310-100", "phone": "+551132145678"},
    "IN": {"first": "Arjun", "last": "Sharma", "address": "MG Road 14", "city": "Bengaluru", "state": "KA", "zip": "560001", "phone": "+918023456789"},
    "CN": {"first": "Wei", "last": "Chen", "address": "Nanjing Rd 100", "city": "Shanghai", "state": "", "zip": "200001", "phone": "+862163215678"},
    "ID": {"first": "Budi", "last": "Santoso", "address": "Jl Sudirman 25", "city": "Jakarta", "state": "", "zip": "10220", "phone": "+62215234567"},
    "VN": {"first": "Minh", "last": "Nguyen", "address": "Le Loi 45", "city": "Ho Chi Minh City", "state": "", "zip": "700000", "phone": "+842838234567"},
    "PH": {"first": "Miguel", "last": "Santos", "address": "Ayala Ave 67", "city": "Makati", "state": "", "zip": "1226", "phone": "+63288123456"},
    "MY": {"first": "Amir", "last": "Rahman", "address": "Jalan Bukit Bintang 30", "city": "Kuala Lumpur", "state": "", "zip": "55100", "phone": "+60321456789"},
    "TH": {"first": "Somchai", "last": "Srisuk", "address": "Sukhumvit Rd 55", "city": "Bangkok", "state": "", "zip": "10110", "phone": "+6622581234"},
    "JP": {"first": "Kenji", "last": "Tanaka", "address": "Marunouchi 1-9-1", "city": "Tokyo", "state": "Tokyo", "zip": "100-0005", "phone": "+81332123456"},
    "KR": {"first": "Minjun", "last": "Kim", "address": "Teheran-ro 152", "city": "Seoul", "state": "", "zip": "06236", "phone": "+8225551234"},
    "TW": {"first": "Chieh", "last": "Lin", "address": "Xinyi Rd Sec 5 7", "city": "Taipei", "state": "", "zip": "110", "phone": "+886281012345"},
    "HK": {"first": "Ka", "last": "Wong", "address": "Nathan Rd 100", "city": "Hong Kong", "state": "", "zip": "999077", "phone": "+85223456789"},
    "SG": {"first": "Wei", "last": "Tan", "address": "Orchard Rd 290", "city": "Singapore", "state": "", "zip": "238859", "phone": "+6562345678"},
    "CA": {"first": "Liam", "last": "Martin", "address": "100 Queen St W", "city": "Toronto", "state": "ON", "zip": "M5H 2N2", "phone": "+14165550199"},
    "AU": {"first": "Jack", "last": "Wilson", "address": "200 George St", "city": "Sydney", "state": "NSW", "zip": "2000", "phone": "+61292345678"},
    "NZ": {"first": "Oliver", "last": "Brown", "address": "150 Queen St", "city": "Auckland", "state": "", "zip": "1010", "phone": "+6493091234"},
    "GB": {"first": "James", "last": "Taylor", "address": "221B Baker St", "city": "London", "state": "", "zip": "NW1 6XE", "phone": "+442079460199"},
    "DE": {"first": "Lukas", "last": "Weber", "address": "Friedrichstr 100", "city": "Berlin", "state": "", "zip": "10117", "phone": "+493023456789"},
    "EU": {"first": "Lukas", "last": "Weber", "address": "Friedrichstr 100", "city": "Berlin", "state": "", "zip": "10117", "phone": "+493023456789"},
    "CH": {"first": "Noah", "last": "Keller", "address": "Bahnhofstrasse 42", "city": "Zurich", "state": "", "zip": "8001", "phone": "+41442112345"},
    "PL": {"first": "Piotr", "last": "Kowalski", "address": "Marszalkowska 55", "city": "Warsaw", "state": "", "zip": "00-676", "phone": "+48221234567"},
    "NO": {"first": "Henrik", "last": "Hansen", "address": "Karl Johans gate 22", "city": "Oslo", "state": "", "zip": "0159", "phone": "+4722123456"},
    "MX": {"first": "Carlos", "last": "Hernandez", "address": "Paseo de la Reforma 222", "city": "Ciudad de Mexico", "state": "CDMX", "zip": "06600", "phone": "+525552345678"},
    "CL": {"first": "Mateo", "last": "Gonzalez", "address": "Av Providencia 1200", "city": "Santiago", "state": "", "zip": "7500000", "phone": "+56223456789"},
    "CO": {"first": "Santiago", "last": "Rodriguez", "address": "Carrera 7 71-21", "city": "Bogota", "state": "", "zip": "110231", "phone": "+5713123456"},
    "PE": {"first": "Diego", "last": "Quispe", "address": "Av Larco 345", "city": "Lima", "state": "", "zip": "15074", "phone": "+5112345678"},
    "UY": {"first": "Mateo", "last": "Rodriguez", "address": "Av 18 de Julio 1234", "city": "Montevideo", "state": "", "zip": "11100", "phone": "+59829012345"},
    "CR": {"first": "Daniel", "last": "Vargas", "address": "Avenida Central 100", "city": "San Jose", "state": "", "zip": "10101", "phone": "+50622212345"},
    "ZA": {"first": "Liam", "last": "Botha", "address": "100 Commissioner St", "city": "Johannesburg", "state": "", "zip": "2001", "phone": "+27112345678"},
    "SA": {"first": "Ahmed", "last": "Al-Saud", "address": "Olaya St 120", "city": "Riyadh", "state": "", "zip": "12214", "phone": "+966112345678"},
    "AE": {"first": "Omar", "last": "Al-Mansoori", "address": "Sheikh Zayed Rd 45", "city": "Dubai", "state": "", "zip": "00000", "phone": "+97142345678"},
    "QA": {"first": "Khalid", "last": "Al-Thani", "address": "Corniche St 10", "city": "Doha", "state": "", "zip": "00000", "phone": "+97444123456"},
    "KW": {"first": "Fahad", "last": "Al-Sabah", "address": "Arabian Gulf St 20", "city": "Kuwait City", "state": "", "zip": "13001", "phone": "+96522412345"},
    "IL": {"first": "David", "last": "Cohen", "address": "Rothschild Blvd 45", "city": "Tel Aviv", "state": "", "zip": "6578403", "phone": "+97235123456"},
}

def _apply_custom_region_price(base: dict[str, Any], cc: str) -> dict[str, Any]:
    res = dict(base)
    try:
        load_cfg = globals().get("_load_config")
        if callable(load_cfg):
            custom_map = (load_cfg().get("custom_region_prices") or {})
            override = custom_map.get(cc) or (custom_map.get("EU") if cc in EUROZONE_COUNTRIES else None)
            if isinstance(override, dict):
                if override.get("target_price") is not None:
                    tp = float(override["target_price"])
                    if tp > 0:
                        res["target_price"] = tp
                        res["target_cents"] = int(round(tp * 100))
                        tp_str = f"{tp:g}".replace(".", ",")
                        res["label"] = f"{tp_str} {res.get('symbol') or res.get('currency', '')}".strip()
                        res["is_custom"] = True
                if override.get("maxprice") is not None:
                    mp = int(override["maxprice"])
                    if mp >= 1:
                        res["maxprice"] = mp
                        res["is_custom"] = True
    except Exception:
        pass
    return res

def get_region_price_info(country_code: str) -> dict[str, Any]:
    cc = str(country_code or "US").strip().upper()
    if cc in REGION_CHEAP_PRICES:
        return _apply_custom_region_price(dict(REGION_CHEAP_PRICES[cc], code=cc), cc)
    if cc in EUROZONE_COUNTRIES:
        base = dict(REGION_CHEAP_PRICES["EU"])
        base["code"] = cc
        base["steam_cc"] = cc if cc != "EU" else "DE"
        base["name"] = COUNTRY_NAMES.get(cc, f"🇪🇺 {cc}")
        return _apply_custom_region_price(base, cc)
    return _apply_custom_region_price({
        "code": cc,
        "name": COUNTRY_NAMES.get(cc, f"🌐 {cc}"),
        "steam_cc": cc if len(cc) == 2 else "US",
        "currency": "USD",
        "symbol": "$",
        "target_price": 0.49,
        "target_cents": 49,
        "maxprice": 5,
        "label": "$0,49",
    }, cc)

def build_steam_cheap_search_url(
    country_code: str,
    *,
    maxprice: int | None = None,
    specials: bool = True,
    count: int = 50,
    start: int = 0,
    for_results_api: bool = True,
) -> str:
    info = get_region_price_info(country_code)
    mp = int(maxprice) if maxprice is not None else int(info["maxprice"])
    params: dict[str, Any] = {
        "sort_by": "Price_ASC",
        "hwtype": "0",
        "maxprice": str(max(1, mp)),
        "ndl": "1",
        "category1": "998",
        "hidef2p": "1",
        "cc": info["steam_cc"],
    }
    if specials:
        params["specials"] = "1"
    if for_results_api:
        params["infinite"] = "1"
        params["start"] = str(max(0, start))
        params["count"] = str(max(1, count))
        return f"{URL_SEARCH_RESULTS}?{urlencode(params)}"
    return f"{URL_SEARCH_PAGE}?{urlencode(params)}"

class RegionResult:
    OK = "OK"
    CART_READY = "CART_READY"
    ALREADY = "ALREADY"
    SENT = "SENT"
    FAIL_WRONG_REGION = "FAIL_WRONG_REGION"
    FAIL_NO_MAFILE = "FAIL_NO_MAFILE"
    FAIL_WRONG_PASS = "FAIL_WRONG_PASS"
    FAIL_WRONG_GUARD = "FAIL_WRONG_GUARD"
    FAIL_RATE_LIMIT = "FAIL_RATE_LIMIT"
    FAIL_SESSION = "FAIL_SESSION"
    FAIL_CHANGE = "FAIL_CHANGE"
    FAIL_VERIFY_REGION = "FAIL_VERIFY_REGION"
    FAIL_NO_CHEAP_GAME = "FAIL_NO_CHEAP_GAME"
    FAIL_CART_ADD = "FAIL_CART_ADD"
    FAIL_NO_CARDS = "FAIL_NO_CARDS"
    FAIL_CARD_DECLINED = "FAIL_CARD_DECLINED"
    FAIL_REGION_COOLDOWN = "FAIL_REGION_COOLDOWN"
    FAIL_CHECKOUT = "FAIL_CHECKOUT"
    FAIL_PROXY = "FAIL_PROXY"
    FAIL_TEMP_ERROR = "FAIL_TEMP_ERROR"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
class FailureReason:
    PROXY_EXHAUSTED = "proxy_exhausted"
    PROXY_TIMEOUT = "proxy_timeout"

    NO_CARDS = "no_cards"
    CARD_DECLINED = "card_declined"
    REGION_COOLDOWN = "region_cooldown"
    CHECKOUT_FAILED = "checkout_failed"

    NO_CHEAP_GAME = "no_cheap_game"
    CART_ADD_FAILED = "cart_add_failed"
    VERIFY_FAILED = "verify_failed"

    WRONG_CREDENTIALS = "wrong_credentials"
    WRONG_GUARD = "wrong_guard"

    STEAM_TEMP_ERROR = "steam_temp_error"
    STEAM_RATE_LIMIT = "steam_rate_limit"

    QR_TIMEOUT = "qr_timeout"
    USER_TIMEOUT = "user_timeout"
    UNKNOWN = "unknown"

@dataclass
class AccountRegionResult:
    login: str
    status: str
    new_region: str | None = None
    error: str | None = None
    failure_reason: str | None = None
    cart_game: dict[str, Any] | None = None
    cart_verified: bool = False
    cart_method: str | None = None
    cart_subtotal: str | None = None
    matched_card: dict[str, Any] | None = None
    card_source: str | None = None
    purchase_completed: bool = False
    region_verified: bool = False
    transid: str | None = None
    checkout_detail: int | None = None
    @property
    def success(self) -> bool:
        return self.status in (RegionResult.OK, RegionResult.CART_READY, RegionResult.ALREADY, RegionResult.SENT)
    @property
    def is_success(self) -> bool:
        return self.success
    @property
    def is_cart_ready(self) -> bool:
        return self.status == RegionResult.CART_READY
    @property
    def critical_wrong_region(self) -> bool:
        return self.status == RegionResult.FAIL_WRONG_REGION
@dataclass
class BatchRegionResult:
    country_code: str
    total: int
    results: list[AccountRegionResult] = field(default_factory=list)
    @property
    def ok(self) -> list[AccountRegionResult]:
        return [r for r in self.results if r.success]
    @property
    def cart_ready(self) -> list[AccountRegionResult]:
        return [r for r in self.results if r.is_cart_ready]
    @property
    def wrong_region(self) -> list[AccountRegionResult]:
        return [r for r in self.results if r.critical_wrong_region]
    @property
    def failed(self) -> list[AccountRegionResult]:
        return [r for r in self.results
                if not r.success and not r.critical_wrong_region
                and r.status not in (RegionResult.SKIPPED, RegionResult.SENT)]
    @property
    def sent(self) -> list[AccountRegionResult]:
        return [r for r in self.results if r.status == RegionResult.SENT]
    @property
    def user_skipped(self) -> list[AccountRegionResult]:
        return [r for r in self.results if r.status == RegionResult.SKIPPED]
def _is_valid_port(p: Any) -> bool:
    try:
        val = int(str(p).strip())
        return 1 <= val <= 65535
    except (ValueError, TypeError):
        return False

def _is_ip_or_host(s: str) -> bool:
    s = str(s or "").strip()
    if not s:
        return False
    if s.lower() in ("localhost", "127.0.0.1"):
        return True
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', s):
        parts = s.split('.')
        return all(0 <= int(x) <= 255 for x in parts)
    if '.' in s and re.match(r'^[a-zA-Z0-9_\-\.]+$', s):
        return True
    if re.match(r'^[a-zA-Z0-9_\-]+$', s):
        return True
    return False

def normalize_proxy_url(p: str) -> str:
    p = (p or "").strip().strip("'\"` \t\r\n")
    if not p:
        return ""
    scheme = "http"
    m_scheme = re.match(r'^(https?|socks5h?|socks4a?)(?::/{1,3}|:)', p, re.IGNORECASE)
    if m_scheme:
        scheme = m_scheme.group(1).lower()
        rest = p[m_scheme.end():]
    elif "://" in p:
        scheme_part, rest = p.split("://", 1)
        if scheme_part.lower() in ("http", "https", "socks5", "socks5h", "socks4", "socks4a"):
            scheme = scheme_part.lower()
        else:
            return ""
    else:
        rest = p

    rest = rest.rstrip("/").strip()
    if not rest:
        return ""

    if "@" in rest:
        left, right = rest.rsplit("@", 1)
        left = left.strip()
        right = right.strip()
        if ":" in right:
            r_parts = right.split(":")
            if len(r_parts) == 2 and _is_ip_or_host(r_parts[0]) and _is_valid_port(r_parts[1]):
                if left:
                    return f"{scheme}://{left}@{r_parts[0]}:{r_parts[1]}"
                return f"{scheme}://{r_parts[0]}:{r_parts[1]}"
        if ":" in left:
            l_parts = left.split(":")
            if len(l_parts) == 2 and _is_ip_or_host(l_parts[0]) and _is_valid_port(l_parts[1]):
                if right:
                    return f"{scheme}://{right}@{l_parts[0]}:{l_parts[1]}"
                return f"{scheme}://{l_parts[0]}:{l_parts[1]}"
        return ""

    parts = rest.split(":")
    if len(parts) == 2:
        host, port = parts[0].strip(), parts[1].strip()
        if _is_ip_or_host(host) and _is_valid_port(port):
            return f"{scheme}://{host}:{port}"
        return ""

    if len(parts) == 4:
        p0, p1, p2, p3 = [x.strip() for x in parts]
        if _is_ip_or_host(p0) and _is_valid_port(p1):
            return f"{scheme}://{p2}:{p3}@{p0}:{p1}"
        elif _is_ip_or_host(p2) and _is_valid_port(p3):
            return f"{scheme}://{p0}:{p1}@{p2}:{p3}"
        elif _is_valid_port(p1) and not _is_valid_port(p3):
            return f"{scheme}://{p2}:{p3}@{p0}:{p1}"
        elif _is_valid_port(p3) and not _is_valid_port(p1):
            return f"{scheme}://{p0}:{p1}@{p2}:{p3}"
        return ""

    if len(parts) > 4:
        p0, p1 = parts[0].strip(), parts[1].strip()
        if _is_ip_or_host(p0) and _is_valid_port(p1):
            user = parts[2].strip()
            pwd = ":".join(parts[3:]).strip()
            return f"{scheme}://{user}:{pwd}@{p0}:{p1}"
        last_host, last_port = parts[-2].strip(), parts[-1].strip()
        if _is_ip_or_host(last_host) and _is_valid_port(last_port):
            user = parts[0].strip()
            pwd = ":".join(parts[1:-2]).strip()
            return f"{scheme}://{user}:{pwd}@{last_host}:{last_port}"

    return ""
DEFAULT_STEAM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

def _make_sticky_proxy_url(proxy_url: str | None) -> str | None:
    if not proxy_url:
        return None
    try:
        m = re.match(r'^([a-zA-Z0-9+.-]+://)([^:@/\s]+):([^@/\s]*)@(.+)$', proxy_url)
        if not m:
            return proxy_url
        scheme, user, pwd, host_part = m.groups()
        u_low = user.lower()
        if "__cr." in u_low and "sessid." not in u_low:
            sid = secrets.token_hex(5)
            sticky_user = f"{user};sessid.{sid};sesstime.10"
            return f"{scheme}{sticky_user}:{pwd}@{host_part}"
    except Exception:
        pass
    return proxy_url

class ProxyRequestStrategy:
    def __init__(self, proxy: str | None = None):
        norm = normalize_proxy_url(proxy) if proxy else None
        self._proxy = _make_sticky_proxy_url(norm) if norm else None
        if norm and self._proxy and self._proxy != norm:
            logger.debug(f"[{NAME}] Включена sticky-сессия прокси для заказа: {_mask_proxy(self._proxy)}")
        self._session: ClientSession | None = None
    def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            cfg_to = REGION_REQUEST_TIMEOUT
            try:
                cfg_to = float(_load_config().get("request_timeout", REGION_REQUEST_TIMEOUT))
            except Exception:
                pass
            connector = None
            if self._proxy and self._proxy.lower().startswith(("socks4://", "socks5://", "socks5h://")):
                try:
                    import aiohttp_socks
                    connector = aiohttp_socks.ProxyConnector.from_url(self._proxy, ssl=False)
                except Exception as _ce:
                    logger.warning(f"[{NAME}] Ошибка SOCKS коннектора: {_ce}")
            if connector is None:
                connector = aiohttp.TCPConnector(ssl=False)
            self._session = ClientSession(
                headers=dict(DEFAULT_STEAM_HEADERS),
                connector=connector,
                timeout=aiohttp.ClientTimeout(
                    total=cfg_to,
                    connect=REGION_CONNECT_TIMEOUT,
                    sock_connect=REGION_CONNECT_TIMEOUT,
                ),
            )
        return self._session
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    async def request(self, url: str, method: str = "GET", **kwargs: Any) -> ClientResponse:
        session = self._get_session()
        is_socks = bool(self._proxy and self._proxy.lower().startswith(("socks4://", "socks5://", "socks5h://")))
        if is_socks:
            kwargs.pop("proxy", None)
            kwargs.pop("proxy_auth", None)
        elif self._proxy and "proxy" not in kwargs:
            kwargs["proxy"] = self._proxy
            if "proxy_auth" not in kwargs:
                try:
                    from yarl import URL as _YURL
                    _u = _YURL(self._proxy)
                    if _u.user and _u.password:
                        kwargs["proxy_auth"] = aiohttp.BasicAuth(_u.user, _u.password)
                except Exception:
                    pass
        req_headers = dict(kwargs.get("headers") or {})
        for hk, hv in DEFAULT_STEAM_HEADERS.items():
            if hk not in req_headers:
                req_headers[hk] = hv
        if "api.steampowered.com" in url:
            if "Origin" not in req_headers:
                req_headers["Origin"] = "https://steamcommunity.com"
            if "Referer" not in req_headers:
                req_headers["Referer"] = "https://steamcommunity.com/login/home/?goto="
        elif "steamcommunity.com" in url:
            if "Referer" not in req_headers:
                req_headers["Referer"] = "https://steamcommunity.com/"
        elif "store.steampowered.com" in url:
            if "Referer" not in req_headers:
                req_headers["Referer"] = "https://store.steampowered.com/"
        kwargs["headers"] = req_headers
        logger.debug(f"[{NAME}] [HTTP {method}] {url} через {_mask_proxy(self._proxy or 'прямое соединение')}")
        response = await session.request(method, url, **kwargs)
        error = response.headers.get("X-eresult")
        if error:
            try:
                from pysteamauth.errors import check_steam_error
                check_steam_error(int(error))
            except (ImportError, ValueError):
                pass
        return response
    def cookies(self, domain: str = "steamcommunity.com") -> Mapping[str, str]:
        session = self._get_session()
        result = {}
        for cookie in session.cookie_jar:
            if cookie["domain"] == domain:
                result[cookie.key] = cookie.value
        return result
    async def text(self, url: str, method: str = "GET", **kwargs: Any) -> str:
        resp = await self.request(url, method, **kwargs)
        return await resp.text()
    async def bytes(self, url: str, method: str = "GET", **kwargs: Any) -> bytes:
        resp = await self.request(url, method, **kwargs)
        return await resp.read()
def parse_store_page(html: str) -> dict:
    country_code = None
    wallet_currency = None
    wallet_fixed = False
    m = re.search(r'data-userinfo="([^"]+)"', html)
    if m:
        try:
            raw = m.group(1).replace("&quot;", '"').replace("&amp;", "&")
            data = json.loads(raw)
            country_code = data.get("country_code")
            wallet_currency = data.get("wallet_currency")
            bal = data.get("wallet_balance", 0)
            if bal and int(bal) > 0:
                wallet_fixed = True
        except Exception:
            pass
    return {
        "country_code": country_code,
        "wallet_fixed": wallet_fixed,
        "wallet_currency": wallet_currency,
    }
def get_shared_secret_from_mafile(mafile_path: str | None) -> str | None:
    if not mafile_path:
        return None
    try:
        with open(mafile_path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("shared_secret") or None
    except Exception:
        return None
def find_shared_secret_in_dir(login: str, mafiles_dir: str) -> str | None:
    p = Path(mafiles_dir)
    if not p.is_dir():
        return None
    login_lower = login.lower()
    for f in p.iterdir():
        if f.suffix.lower() not in (".mafile", ".json"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        acc_name = (data.get("account_name") or "").lower()
        stem = f.stem.lower()
        if acc_name == login_lower or stem == login_lower or \
           (acc_name and (login_lower.startswith(acc_name) or acc_name.startswith(login_lower))):
            return data.get("shared_secret") or None
    return None
async def _safe_is_authorized(self) -> bool:
    try:
        cookies = await self._storage.get(
            login=self._login,
            domain="steamcommunity.com",
        )
        if not cookies:
            return False
        has_sess = bool(cookies.get("sessionid"))
        has_sec = any("steamloginsecure" in str(k).lower() for k in cookies.keys())
        return has_sess and has_sec
    except Exception:
        return False

async def _try_import_pysteamauth():
    try:
        from pysteamauth.auth import Steam
        Steam.is_authorized = _safe_is_authorized
        return Steam
    except ImportError:
        return None
class GuardCodeSkipped(Exception):
    pass
class GuardCodeRejected(Exception):
    pass
class SteamLoginFailed(Exception):
    pass

async def _ensure_session_cookie(request_strategy: ProxyRequestStrategy, domain: str = "steamcommunity.com") -> str:
    cookie = str(request_strategy.cookies(domain).get("sessionid") or "").strip()
    if cookie:
        return cookie
    paths = (
        ("https://steamcommunity.com/login/home/?goto=", "https://steamcommunity.com/")
        if domain == "steamcommunity.com"
        else (f"https://{domain}/",)
    )
    for url in paths:
        try:
            resp = await request_strategy.request(url, method="GET", allow_redirects=False)
            try:
                await resp.read()
            finally:
                resp.release()
        except Exception as exc:
            _log_event("sessionid_probe_failed", level=logging.DEBUG, domain=domain, error=type(exc).__name__)
        cookie = str(request_strategy.cookies(domain).get("sessionid") or "").strip()
        if cookie:
            _log_event("sessionid_ready", level=logging.DEBUG, domain=domain, source="steam")
            return cookie
    from yarl import URL
    cookie = secrets.token_hex(12)
    request_strategy._get_session().cookie_jar.update_cookies(
        {"sessionid": cookie}, response_url=URL(f"https://{domain}/")
    )
    if request_strategy.cookies(domain).get("sessionid") != cookie:
        raise SteamLoginFailed(f"Не удалось сохранить Steam sessionid для {domain}")
    _log_event("sessionid_ready", level=logging.INFO, domain=domain, source="client")
    return cookie
_ManualSteamClass = None

async def _try_import_manual_steam():
    global _ManualSteamClass
    if _ManualSteamClass is not None:
        return _ManualSteamClass
    try:
        from pysteamauth.auth import Steam
        Steam.is_authorized = _safe_is_authorized
        from pysteamauth.pb2.steammessages_auth.steamclient_pb2 import EAuthSessionGuardType
        from urllib3.util import parse_url
    except ImportError:
        return None
    class ManualGuardSteam(Steam):
        _manual_code: str = ""
        _guard_provider: GuardProvider | None = None
        def set_guard_code(self, code: str) -> None:
            self._manual_code = (code or "").strip().upper()
        def set_guard_provider(self, provider: GuardProvider | None) -> None:
            self._guard_provider = provider
        def _encrypt_password(self, keys: Any) -> str:
            import rsa
            publickey_exp = int(keys.publickey_exp, 16)
            publickey_mod = int(keys.publickey_mod, 16)
            public_key = rsa.PublicKey(n=publickey_mod, e=publickey_exp)
            pwd = str(self._password or "")
            try:
                msg_bytes = pwd.encode("utf-8")
            except Exception:
                msg_bytes = pwd.encode("ascii", errors="replace")
            encrypted_password = rsa.encrypt(message=msg_bytes, pub_key=public_key)
            return str(base64.b64encode(encrypted_password), "utf8")
        async def is_authorized(self) -> bool:
            return await _safe_is_authorized(self)
        async def _begin_auth_session(
            self,
            encrypted_password: str,
            rsa_timestamp: int,
        ):
            from pysteamauth.pb2.steammessages_auth.steamclient_pb2 import (
                CAuthentication_BeginAuthSessionViaCredentials_Request,
                CAuthentication_BeginAuthSessionViaCredentials_Response,
                EAuthTokenPlatformType,
            )
            from aiohttp import FormData
            try:
                from pysteamauth.pb2.enums_pb2 import ESessionPersistence
                sess_persist = ESessionPersistence.k_ESessionPersistence_Persistent
            except Exception:
                sess_persist = 1
            message = CAuthentication_BeginAuthSessionViaCredentials_Request(
                account_name=self._login,
                encrypted_password=encrypted_password,
                encryption_timestamp=rsa_timestamp,
                remember_login=True,
                platform_type=EAuthTokenPlatformType.k_EAuthTokenPlatformType_WebBrowser,
                website_id="Community",
                persistence=sess_persist,
                device_friendly_name="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            )
            response = await self._requests.bytes(
                method="POST",
                url="https://api.steampowered.com/IAuthenticationService/BeginAuthSessionViaCredentials/v1",
                data=FormData(
                    fields=[
                        ("input_protobuf_encoded", str(base64.b64encode(message.SerializeToString()), "utf8")),
                    ],
                ),
            )
            return CAuthentication_BeginAuthSessionViaCredentials_Response.FromString(response)
        async def login_to_steam(self) -> None:
            if await self.is_authorized():
                return
            await _ensure_session_cookie(self._requests)

            session = None
            auth_session = None

            for session_attempt in range(1, 3):
                keys = await self._getrsakey()
                encrypted_password = self._encrypt_password(keys)
                auth_session = await self._begin_auth_session(
                    encrypted_password=encrypted_password,
                    rsa_timestamp=keys.timestamp,
                )
                c_types = [getattr(c, "confirmation_type", None) for c in getattr(auth_session, "allowed_confirmations", [])]
                logger.info(
                    f"[{self._login}] Steam auth_session начата (попытка {session_attempt}/2): client_id={getattr(auth_session, 'client_id', '')} "
                    f"interval={getattr(auth_session, 'interval', 5)} confirmations={c_types}"
                )
                if not getattr(auth_session, "client_id", None):
                    raise SteamLoginFailed("Не удалось получить сессию Steam (неверный логин или пароль)")

                push_conf = None
                device_conf = None
                email_conf = None

                for conf in getattr(auth_session, "allowed_confirmations", []):
                    ct = getattr(conf, "confirmation_type", None)
                    if ct in (getattr(EAuthSessionGuardType, 'k_EAuthSessionGuardType_DeviceConfirmation', 4), 4):
                        push_conf = conf
                    elif ct in (getattr(EAuthSessionGuardType, 'k_EAuthSessionGuardType_DeviceCode', 3), 3):
                        device_conf = conf
                    elif ct in (getattr(EAuthSessionGuardType, 'k_EAuthSessionGuardType_EmailCode', 2),
                                getattr(EAuthSessionGuardType, 'k_EAuthSessionGuardType_EmailConfirmation', 5), 2, 5):
                        email_conf = conf

                has_email = email_conf is not None
                has_machine = any(getattr(c, "confirmation_type", None) in (6, getattr(EAuthSessionGuardType, 'k_EAuthSessionGuardType_MachineToken', 6)) for c in getattr(auth_session, "allowed_confirmations", []))
                if has_email or has_machine:
                    try:
                        await self._requests.bytes(
                            method="POST",
                            url="https://login.steampowered.com/jwt/checkdevice",
                            data={
                                "clientid": str(auth_session.client_id),
                                "steamid": str(auth_session.steamid),
                            },
                            headers={
                                "Origin": "https://steamcommunity.com",
                                "Referer": "https://steamcommunity.com",
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                            },
                        )
                        logger.info(f"[{self._login}] checkdevice выполнен: Steam инициировал отправку кода на почту")
                    except Exception as _cd_e:
                        logger.debug(f"[{self._login}] checkdevice: {_cd_e}")

                poll_interval = max(2, int(getattr(auth_session, "interval", 3) or 3))

                if push_conf is not None:
                    logger.info(f"[{self._login}] Выбран способ подтверждения: Steam Mobile Push (тип 4)")
                    if self._guard_provider is not None:
                        try:
                            await self._guard_provider(self._login, guard_type="push", guard_detail="Steam Mobile App")
                        except Exception:
                            pass
                    device_code_type = getattr(EAuthSessionGuardType, 'k_EAuthSessionGuardType_DeviceCode', 3)
                    for _poll_i in range(30):
                        await asyncio.sleep(poll_interval)
                        if self._guard_provider is not None and device_conf is not None:
                            try:
                                push_code = (await self._guard_provider(
                                    self._login,
                                    guard_type="push_code",
                                    guard_detail="Steam Mobile App",
                                ) or "").strip().upper()
                            except Exception:
                                push_code = ""
                            if push_code:
                                logger.info(f"[{self._login}] Получен 5-значный код Steam Guard во время Mobile Push, отправляем в Steam...")
                                try:
                                    await self._update_auth_session(
                                        client_id=auth_session.client_id,
                                        steamid=auth_session.steamid,
                                        code=push_code,
                                        code_type=device_code_type,
                                    )
                                except Exception as _code_err:
                                    err_code = getattr(_code_err, "error_code", None)
                                    err_msg = str(getattr(_code_err, "error_msg", "") or str(_code_err))
                                    logger.warning(f"[{self._login}] Код Steam Guard из Mobile Push отклонён: {err_code} {err_msg}")
                                    if err_code in (65, 88) or "twofactorcodemismatch" in err_msg.lower() or "invalidloginauthcode" in err_msg.lower():
                                        try:
                                            await self._guard_provider(
                                                self._login,
                                                guard_type="push_code_invalid",
                                                guard_detail="Steam Mobile App",
                                            )
                                        except Exception:
                                            pass
                        try:
                            session = await self._poll_auth_session_status(
                                client_id=auth_session.client_id,
                                request_id=auth_session.request_id,
                            )
                        except Exception as _p_err:
                            logger.debug(f"[{self._login}] Poll push status: {_p_err}")
                            session = None
                        if session and session.refresh_token:
                            break
                    if session and session.refresh_token:
                        break
                    else:
                        raise SteamLoginFailed("Требуется подтвердить вход в мобильном приложении Steam (Steam Guard Push)")

                target_conf = device_conf or email_conf
                if target_conf is not None:
                    c_type = getattr(target_conf, "confirmation_type", None)
                    is_email = (target_conf is email_conf)
                    guard_type_str = "email" if is_email else "device"
                    guard_detail_str = getattr(target_conf, "associated_message", "") or ""
                    code_type = getattr(EAuthSessionGuardType, 'k_EAuthSessionGuardType_EmailCode', 2) if is_email else getattr(EAuthSessionGuardType, 'k_EAuthSessionGuardType_DeviceCode', 3)
                    logger.info(f"[{self._login}] Выбран способ подтверждения: {guard_type_str} (тип {c_type})")

                    invalid_code_attempts = 0
                    max_code_attempts = 3
                    stale_session_needed = False

                    while invalid_code_attempts < max_code_attempts:
                        if self._guard_provider is not None:
                            try:
                                code = (await self._guard_provider(
                                    self._login,
                                    guard_type=guard_type_str,
                                    guard_detail=guard_detail_str,
                                    is_retry=(invalid_code_attempts > 0),
                                ) or "").strip().upper()
                            except TypeError:
                                code = (await self._guard_provider(self._login) or "").strip().upper()
                        else:
                            code = self._manual_code

                        if not code:
                            raise GuardCodeSkipped()

                        logger.info(f"[{self._login}] Отправка Guard-кода в Steam (попытка {invalid_code_attempts + 1}/{max_code_attempts})...")
                        from pysteamauth.errors.exceptions import SteamError

                        try:
                            await self._update_auth_session(
                                client_id=auth_session.client_id,
                                steamid=auth_session.steamid,
                                code=code,
                                code_type=code_type,
                            )
                            logger.info(f"[{self._login}] Guard-код отправлен в Steam, начинаем polling...")
                            for _poll_i in range(12):
                                await asyncio.sleep(poll_interval)
                                try:
                                    session = await self._poll_auth_session_status(
                                        client_id=auth_session.client_id,
                                        request_id=auth_session.request_id,
                                    )
                                except Exception as _p_err:
                                    logger.debug(f"[{self._login}] Poll status: {_p_err}")
                                    session = None
                                if session and session.refresh_token:
                                    break
                            if session and session.refresh_token:
                                break
                            else:
                                logger.warning(f"[{self._login}] Refresh token не получен после polling")
                                invalid_code_attempts += 1
                                if invalid_code_attempts >= max_code_attempts:
                                    raise GuardCodeRejected("Guard-код не принят Steam (не получен refresh_token)")
                                continue

                        except SteamError as e:
                            err_code = getattr(e, "error_code", None)
                            err_msg = str(getattr(e, "error_msg", "") or str(e))

                            if err_code == 29 or "duplicaterequest" in err_msg.lower():
                                logger.info(
                                    f"[{self._login}] Steam вернул DuplicateRequest (код 29). "
                                    f"Не считаем ошибкой Guard, опрашиваем текущую auth-session..."
                                )
                                _log_event("steam_guard_duplicate_request", login=self._login, client_id=str(auth_session.client_id))
                                dup_session = None
                                for _dup_i in range(7):
                                    await asyncio.sleep(2.5)
                                    try:
                                        dup_session = await self._poll_auth_session_status(
                                            client_id=auth_session.client_id,
                                            request_id=auth_session.request_id,
                                        )
                                    except Exception as _p_err:
                                        logger.debug(f"[{self._login}] Poll status (DuplicateRequest): {_p_err}")
                                        dup_session = None
                                    if dup_session and dup_session.refresh_token:
                                        logger.info(f"[{self._login}] Успешно получен refresh_token после DuplicateRequest!")
                                        session = dup_session
                                        break

                                if session and session.refresh_token:
                                    break
                                else:

                                    logger.warning(
                                        f"[{self._login}] DuplicateRequest не восстановился через polling. "
                                        f"Сбрасываем проблемную auth-session и создаём новую..."
                                    )
                                    stale_session_needed = True
                                    break

                            elif err_code in (65, 88) or "twofactorcodemismatch" in err_msg.lower() or "invalidloginauthcode" in err_msg.lower():
                                invalid_code_attempts += 1
                                logger.warning(f"[{self._login}] Неверный Guard-код Steam (код ошибки {err_code}, попытка {invalid_code_attempts}/{max_code_attempts})")
                                if invalid_code_attempts >= max_code_attempts:
                                    raise GuardCodeRejected(f"Guard-код не принят Steam (код ошибки {err_code})")
                                continue

                            elif err_code == 84 or "ratelimit" in err_msg.lower():
                                raise SteamLoginFailed("Превышен лимит запросов Steam (RateLimitExceeded)")

                            else:
                                invalid_code_attempts += 1
                                logger.warning(f"[{self._login}] Steam отклонил Guard-код (код ошибки {err_code}): {e}")
                                if invalid_code_attempts >= max_code_attempts:
                                    raise GuardCodeRejected(f"Guard-код не принят Steam (код ошибки {err_code})")
                                continue

                    if session and session.refresh_token:
                        break
                    if stale_session_needed:
                        if session_attempt < 2:
                            logger.info(f"[{self._login}] Повторная инициализация auth_session после DuplicateRequest...")
                            continue
                        else:
                            raise GuardCodeRejected("Steam DuplicateRequest: не удалось восстановить сессию подтверждения")

                else:
                    try:
                        session = await self._poll_auth_session_status(
                            client_id=auth_session.client_id,
                            request_id=auth_session.request_id,
                        )
                    except Exception:
                        pass
                    if session and session.refresh_token:
                        break
                    else:
                        if auth_session.allowed_confirmations:
                            c_names = [str(getattr(c, "confirmation_type", c)) for c in auth_session.allowed_confirmations]
                            raise SteamLoginFailed(f"Требуется подтверждение Steam: {', '.join(c_names)}")
                        raise SteamLoginFailed("Не удалось получить сессию Steam (неверный логин или пароль)")

            if not session or not session.refresh_token:
                raise SteamLoginFailed("Не удалось получить сессию Steam (отсутствует refresh_token)")

            tokens = await self._finalize_login(
                refresh_token=session.refresh_token,
                sessionid=await _ensure_session_cookie(self._requests),
            )
            for token in tokens.transfer_info:
                await self._set_token(
                    url=token.url,
                    nonce=token.params.nonce,
                    auth=token.params.auth,
                    steamid=auth_session.steamid,
                )
            if not self._steamid and tokens.steamID:
                self._steamid = int(tokens.steamID)
            cookies = {"steamcommunity.com": self._requests.cookies("steamcommunity.com")}
            for url in ("https://store.steampowered.com", "https://help.steampowered.com"):
                await self._requests.bytes(url, "GET")
                host = parse_url(url).host
                if host == "store.steampowered.com":
                    await _ensure_session_cookie(self._requests, host)
                cookies.update({host: self._requests.cookies(host)})
            await self._storage.set(login=self._login, cookies=cookies)
    _ManualSteamClass = ManualGuardSteam
    return _ManualSteamClass
class QRLoginTimeout(Exception):
    pass
class QRLoginCancelled(Exception):
    pass
_QRSteamClass = None

async def _try_import_qr_steam():
    global _QRSteamClass
    if _QRSteamClass is not None:
        return _QRSteamClass
    try:
        from aiohttp import FormData
        from pysteamauth.auth import Steam
        from pysteamauth.pb2.steammessages_auth.steamclient_pb2 import (
            CAuthentication_BeginAuthSessionViaQR_Request,
            CAuthentication_BeginAuthSessionViaQR_Response,
            EAuthTokenPlatformType,
        )
        from urllib3.util import parse_url
    except ImportError:
        return None
    class QRSteam(Steam):
        account_name: str = ""
        async def _begin_qr(self):
            message = CAuthentication_BeginAuthSessionViaQR_Request(
                device_friendly_name="Mozilla/5.0 (X11; Linux x86_64; rv:1.9.5.20) "
                                     "Gecko/2812-12-10 04:56:28 Firefox/3.8",
                platform_type=EAuthTokenPlatformType.k_EAuthTokenPlatformType_WebBrowser,
            )
            response = await self._requests.bytes(
                method="POST",
                url="https://api.steampowered.com/IAuthenticationService/BeginAuthSessionViaQR/v1",
                data=FormData(fields=[
                    ("input_protobuf_encoded", str(base64.b64encode(message.SerializeToString()), "utf8")),
                ]),
            )
            return CAuthentication_BeginAuthSessionViaQR_Response.FromString(response)
        async def login_via_qr(
            self,
            display_cb: QRDisplay,
            poll_timeout: float = 180.0,
            confirmed_cb: Optional[Callable[[str], Awaitable[None]]] = None,
            cancel_event: Optional[threading.Event] = None,
            order_id: str = "",
        ) -> str:
            oid_log = order_id or self.account_name or "(qr)"
            if cancel_event and cancel_event.is_set():
                _log_event("qr_worker_cancelled", order_id=oid_log, reason="cancelled_pre_login")
                raise QRLoginCancelled("QR-авторизация отменена")
            await _ensure_session_cookie(self._requests)
            if cancel_event and cancel_event.is_set():
                _log_event("qr_worker_cancelled", order_id=oid_log, reason="cancelled_post_cookie")
                raise QRLoginCancelled("QR-авторизация отменена")
            qr = await self._begin_qr()
            client_id = qr.client_id
            request_id = qr.request_id
            interval = qr.interval or 2.0
            last_url = qr.challenge_url

            if cancel_event and cancel_event.is_set():
                _log_event("qr_worker_cancelled", order_id=oid_log, reason="cancelled_post_begin")
                raise QRLoginCancelled("QR-авторизация отменена")
            await display_cb(last_url)
            deadline = time.monotonic() + poll_timeout
            refresh_token = None
            while time.monotonic() < deadline:
                if cancel_event and cancel_event.is_set():
                    _log_event("qr_worker_cancelled", order_id=oid_log, reason="cancelled_in_poll")
                    raise QRLoginCancelled("QR-авторизация отменена")
                sleep_end = time.monotonic() + max(1.0, interval)
                while time.monotonic() < sleep_end:
                    if cancel_event and cancel_event.is_set():
                        _log_event("qr_worker_cancelled", order_id=oid_log, reason="cancelled_during_sleep")
                        raise QRLoginCancelled("QR-авторизация отменена")
                    await asyncio.sleep(min(0.2, max(0.05, sleep_end - time.monotonic())))
                if cancel_event and cancel_event.is_set():
                    _log_event("qr_worker_cancelled", order_id=oid_log, reason="cancelled_pre_poll_request")
                    raise QRLoginCancelled("QR-авторизация отменена")
                try:
                    session = await self._poll_auth_session_status(
                        client_id=client_id, request_id=request_id,
                    )
                except Exception as poll_err:
                    if cancel_event and cancel_event.is_set():
                        _log_event("qr_worker_cancelled", order_id=oid_log, reason="cancelled_on_poll_err")
                        raise QRLoginCancelled("QR-авторизация отменена")
                    err_code = getattr(poll_err, "error_code", None)
                    err_str = str(poll_err).lower()
                    if (
                        err_code in (9, 42, 2, 16)
                        or "filenotfound" in err_str
                        or "code': 9" in err_str
                        or "code': 42" in err_str
                        or "code': 2" in err_str
                    ):
                        logger.info(f"[QR] Steam сессия QR истекла или завершена (код {err_code or 9})")
                        raise QRLoginTimeout("QR-код не подтверждён (истекло время действия кода)")
                    if _is_network_error(poll_err):
                        continue
                    raise
                if session.new_client_id:
                    client_id = session.new_client_id
                if session.refresh_token:
                    refresh_token = session.refresh_token
                    if session.account_name:
                        self.account_name = session.account_name
                    if confirmed_cb:
                        try:
                            await confirmed_cb(self.account_name)
                        except Exception:
                            pass
                    break
            if cancel_event and cancel_event.is_set():
                _log_event("qr_worker_cancelled", order_id=oid_log, reason="cancelled_post_poll")
                raise QRLoginCancelled("QR-авторизация отменена")
            if not refresh_token:
                raise QRLoginTimeout("QR-код не подтверждён (истекло время ожидания)")
            tokens = await self._finalize_login(
                refresh_token=refresh_token,
                sessionid=await _ensure_session_cookie(self._requests),
            )
            steamid = int(tokens.steamID) if tokens.steamID else 0
            for token in tokens.transfer_info:
                await self._set_token(
                    url=token.url,
                    nonce=token.params.nonce,
                    auth=token.params.auth,
                    steamid=steamid,
                )
            if steamid:
                self._steamid = steamid
            cookies = {"steamcommunity.com": self._requests.cookies("steamcommunity.com")}
            for url in ("https://store.steampowered.com", "https://help.steampowered.com"):
                await self._requests.bytes(url, "GET")
                host = parse_url(url).host
                if host == "store.steampowered.com":
                    await _ensure_session_cookie(self._requests, host)
                cookies.update({host: self._requests.cookies(host)})
            await self._storage.set(login=self._login, cookies=cookies)
            return self.account_name
    _QRSteamClass = QRSteam
    return _QRSteamClass

async def set_country_raw(strategy: ProxyRequestStrategy, cc: str, session_id: str) -> bool:
    hdrs = {"X-Requested-With": "XMLHttpRequest"}
    ok_store = ok_chk = False
    try:
        resp = await strategy.request(
            URL_SET_COUNTRY, method="POST",
            data=aiohttp.FormData(fields=[("sessionid", session_id), ("cc", cc)]),
            headers={**hdrs, "Origin": "https://store.steampowered.com",
                     "Referer": "https://store.steampowered.com/account/"},
        )
        st = getattr(resp, "status", 0)
        ok_store = (200 <= st < 300)
        body = ""
        try:
            if hasattr(resp, "text"):
                body = await resp.text() if asyncio.iscoroutinefunction(resp.text) else resp.text()
            else:
                body = str(resp)
        except Exception:
            body = ""
        body_snip = body.strip().replace("\n", " ").replace("\r", " ")[:120]
        _log_event("setcountry_response", target=cc, endpoint="store", status=st, response=body_snip)
        logger.info(f"setcountry store HTTP {st} response: {body_snip}")
    except Exception as e:
        logger.debug(f"set_country store error: {e}")
        _log_event("setcountry_response", target=cc, endpoint="store", status="error", error=str(e)[:60])
    try:
        resp = await strategy.request(
            URL_SET_COUNTRY_CHK, method="POST",
            data=aiohttp.FormData(fields=[("sessionid", session_id), ("cc", cc)]),
            headers={**hdrs, "Origin": "https://checkout.steampowered.com",
                     "Referer": "https://checkout.steampowered.com/"},
        )
        st = getattr(resp, "status", 0)
        ok_chk = (200 <= st < 300)
        body = ""
        try:
            if hasattr(resp, "text"):
                body = await resp.text() if asyncio.iscoroutinefunction(resp.text) else resp.text()
            else:
                body = str(resp)
        except Exception:
            body = ""
        body_snip = body.strip().replace("\n", " ").replace("\r", " ")[:120]
        _log_event("setcountry_response", target=cc, endpoint="checkout", status=st, response=body_snip)
        logger.info(f"setcountry checkout HTTP {st} response: {body_snip}")
    except Exception as e:
        logger.debug(f"set_country checkout error: {e}")
        _log_event("setcountry_response", target=cc, endpoint="checkout", status="error", error=str(e)[:60])
    return ok_store or ok_chk

async def check_proxy(proxy_url: str, timeout: float = 8.0) -> tuple[bool, str]:
    clean_p = _mask_proxy(proxy_url)
    p_url = normalize_proxy_url(proxy_url)
    if not p_url:
        _log_event("proxy_check", level=logging.WARNING, status="fail", proxy=clean_p, err="invalid_url")
        return False, "пустой/неверный URL"
    t0 = time.monotonic()
    country_code = "??"
    geo_ok = False
    geo_err = ""
    geo_endpoints = [
        ("http://ip-api.com/json/", "countryCode"),
        ("https://ipwho.is/", "country_code"),
        ("https://api.myip.com", "cc"),
    ]
    is_socks = bool(p_url and p_url.lower().startswith(("socks4://", "socks5://", "socks5h://")))
    connector = None
    if is_socks:
        try:
            import aiohttp_socks
            connector = aiohttp_socks.ProxyConnector.from_url(p_url, ssl=False)
        except Exception:
            pass
    if connector is None:
        connector = aiohttp.TCPConnector(ssl=False)
    client_timeout = aiohttp.ClientTimeout(total=timeout, connect=max(3.0, timeout / 2))
    proxy_arg = None if is_socks else p_url
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=client_timeout) as session:
            for geo_url, key in geo_endpoints:
                try:
                    async with session.get(geo_url, proxy=proxy_arg) as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            if isinstance(data, dict):
                                val = data.get(key)
                                if val:
                                    country_code = str(val).upper()
                                    geo_ok = True
                                    break
                        else:
                            geo_err = f"HTTP {resp.status}"
                except asyncio.TimeoutError:
                    geo_err = "таймаут"
                except Exception as e:
                    geo_err = str(e)[:60]
            try:
                async with session.get(
                    "https://store.steampowered.com/",
                    proxy=proxy_arg,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                ) as resp:
                    elapsed = time.monotonic() - t0
                    ms = int(elapsed * 1000)
                    if resp.status in (200, 301, 302):
                        _log_event("proxy_check", status="ok", proxy=clean_p, country=country_code, steam="OK", http=resp.status, ms=ms)
                        return True, country_code
                    else:
                        _log_event("proxy_check", level=logging.WARNING, status="fail", proxy=clean_p, err=f"Steam HTTP {resp.status}", ms=ms)
                        return False, f"Steam HTTP {resp.status}"
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - t0
                _log_event("proxy_check", level=logging.WARNING, status="fail", proxy=clean_p, err="SteamTimeout", ms=int(elapsed * 1000))
                return False, "таймаут Steam"
            except Exception as e:
                elapsed = time.monotonic() - t0
                err_msg = _mask_proxy(str(e))[:80]
                _log_event("proxy_check", level=logging.WARNING, status="fail", proxy=clean_p, err=err_msg, ms=int(elapsed * 1000))
                return False, f"Steam: {err_msg}"
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        _log_event("proxy_check", level=logging.WARNING, status="fail", proxy=clean_p, err="Timeout", ms=int(elapsed * 1000))
        return False, "таймаут"
    except Exception as e:
        elapsed = time.monotonic() - t0
        err_msg = _mask_proxy(str(e))[:80]
        _log_event("proxy_check", level=logging.WARNING, status="fail", proxy=clean_p, err=err_msg, ms=int(elapsed * 1000))
        return False, err_msg
async def check_proxies_for_country(
    proxies: list[str],
    expected_cc: str,
    max_check: int = 10,
) -> tuple[list[str], list[str]]:
    check_list = proxies[:max_check] if max_check > 0 else proxies
    _log_event("country_check_start", country=expected_cc, count=len(check_list), total=len(proxies))
    good, bad = [], []
    tasks = [check_proxy(p) for p in check_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for proxy, result in zip(check_list, results):
        clean_p = _mask_proxy(proxy)
        if isinstance(result, tuple) and result[0]:
            cc = result[1]
            if cc == expected_cc.upper() or expected_cc == "ANY":
                good.append(proxy)
                _log_event("country_proxy_result", status="ok", proxy=clean_p, country=cc)
            else:
                bad.append(proxy)
                _log_event("country_proxy_result", level=logging.WARNING, status="fail", proxy=clean_p, reason=f"WrongCountry ({cc} != {expected_cc})")
        else:
            bad.append(proxy)
            err_str = result[1] if isinstance(result, tuple) else str(result)[:60]
            _log_event("country_proxy_result", level=logging.WARNING, status="fail", proxy=clean_p, reason=err_str)
    _log_event("country_check_summary", country=expected_cc, good=len(good), bad=len(bad))
    return good, bad
class ProxyPool:
    def __init__(self, proxies: list[str]):
        self._initial = [p for p in (proxies or []) if p]
        self._proxies = list(self._initial)
        self._failures: dict[str, int] = {}
        self._idx = 0
    def __len__(self) -> int:
        return len(self._proxies)
    @property
    def total_configured(self) -> int:
        return len(self._initial)
    def get_next(self) -> str | None:
        if not self._proxies:
            return None
        p = self._proxies[self._idx % len(self._proxies)]
        self._idx += 1
        return p
    def ban(self, proxy: str | None) -> None:
        if not proxy:
            return
        self._failures[proxy] = self._failures.get(proxy, 0) + 1
        threshold = 2
        if self._failures[proxy] >= threshold:
            try:
                if proxy in self._proxies:
                    self._proxies.remove(proxy)
                    if self._proxies:
                        self._idx %= len(self._proxies)
                    else:
                        self._idx = 0
                    logger.warning(f"Прокси временно исключён из пула ({self._failures[proxy]} ошибок): {_mask_proxy(proxy)}")
            except ValueError:
                pass
def extract_steam_country(html_or_json: str) -> str | None:
    if not html_or_json:
        return None
    s = str(html_or_json).strip()
    if s.startswith("{") and s.endswith("}"):
        try:
            d = json.loads(s)
            c = d.get("rgCountryCode") or d.get("country") or d.get("country_code")
            if c and len(str(c)) == 2:
                return str(c).upper()
        except Exception:
            pass
    m = re.search(r'data-userinfo="([^"]+)"', s)
    if m:
        try:
            raw = html.unescape(m.group(1))
            d = json.loads(raw)
            c = d.get("country_code") or d.get("country") or d.get("rgCountryCode")
            if c and len(str(c)) == 2:
                return str(c).upper()
        except Exception:
            pass
    s_clean = html.unescape(s)
    patterns = [
        r'["\']country_code["\']\s*:\s*["\']([A-Za-z]{2})["\']',
        r'g_strCountryCode\s*=\s*["\']([A-Za-z]{2})["\']',
        r'rgCountryCode\s*=\s*["\']([A-Za-z]{2})["\']',
        r'["\']rgCountryCode["\']\s*:\s*["\']([A-Za-z]{2})["\']',
        r'["\']country["\']\s*:\s*["\']([A-Za-z]{2})["\']',
        r'data-country=["\']([A-Za-z]{2})["\']',
    ]
    for p in patterns:
        m2 = re.search(p, s_clean, re.IGNORECASE)
        if m2:
            return m2.group(1).upper()
    return None

async def _fetch_steam_page_country(steam, base_url: str, login: str) -> str | None:
    cache_bust = f"_src_verify={int(time.time())}_{secrets.token_hex(4)}"
    sep = "&" if "?" in base_url else "?"
    url = f"{base_url}{sep}{cache_bust}"
    headers = {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    try:
        if hasattr(steam, "request") and callable(steam.request):
            raw = await _net(steam.request, url, label=login, headers=headers)
        elif hasattr(steam, "text") and callable(steam.text):
            raw = await _net(steam.text, url, label=login, headers=headers)
        elif hasattr(steam, "get") and callable(steam.get):
            raw = await _net(steam.get, url, label=login, headers=headers)
        else:
            return None

        if hasattr(raw, "text"):
            page_text = await raw.text() if asyncio.iscoroutinefunction(raw.text) else raw.text()
        else:
            page_text = str(raw)

        return extract_steam_country(page_text)
    except Exception as e:
        logger.debug(f"[{login}] verify_page error {base_url}: {e}")
        return None

async def verify_region_change(steam, login: str, target_cc: str) -> tuple[bool, dict[str, Any]]:
    target = (target_cc or "").upper()
    _log_event("region_verify_start", login=login, target=target)
    check_results = []
    for check_num in (1, 2):
        await asyncio.sleep(1.5)
        account_cc = await _fetch_steam_page_country(steam, URL_ACCOUNT, login)
        addfunds_cc = await _fetch_steam_page_country(steam, URL_ADDFUNDS, login)
        userdata_cc = await _fetch_steam_page_country(steam, "https://store.steampowered.com/dynamicstore/userdata/", login)

        is_ok = bool(account_cc and account_cc == target and addfunds_cc and addfunds_cc == target)
        if userdata_cc and userdata_cc != target:
            is_ok = False

        check_info = {
            "check": check_num,
            "account": account_cc,
            "addfunds": addfunds_cc,
            "userdata": userdata_cc,
            "match": is_ok,
        }
        check_results.append(check_info)

        _log_event(
            "region_verify_check",
            check=check_num,
            account=account_cc,
            addfunds=addfunds_cc,
            status="ok" if is_ok else "fail",
            login=login,
            target=target,
            userdata=userdata_cc,
        )

        if not is_ok:
            _log_event(
                "region_verify_failed",
                login=login,
                target=target,
                reason="mismatch",
                check=check_num,
                account=account_cc,
                addfunds=addfunds_cc,
                userdata=userdata_cc,
            )
            return False, {"target": target, "checks": check_results, "reason": f"check_{check_num}_mismatch"}

    _log_event(
        "region_verify_success",
        login=login,
        target=target,
        account=check_results[1]["account"],
        addfunds=check_results[1]["addfunds"],
        userdata=check_results[1]["userdata"],
    )
    return True, {"target": target, "checks": check_results}

_COMMERCIAL_SUB_MARKERS = (
    "commercial license", "pc cafe", "cyber cafe", "site license",
    "коммерческая лицензия", "soundtrack", "саундтрек",
)

def parse_steam_search_html(results_html: str, country_code: str = "US") -> list[dict[str, Any]]:
    info = get_region_price_info(country_code)
    items: list[dict[str, Any]] = []
    if not results_html:
        return items
    rows = re.findall(
        r'<a\b[^>]*class="[^"]*search_result_row[^"]*"[^>]*>.*?</a>',
        results_html,
        re.DOTALL | re.IGNORECASE,
    )
    seen_ids: set[tuple[int | None, int | None, int | None]] = set()
    for row in rows:
        m_href = re.search(r'href="([^"]+)"', row, re.IGNORECASE)
        href = html.unescape(m_href.group(1)).split("?")[0] if m_href else ""

        appid: int | None = None
        m_app = re.search(r'data-ds-appid="([^"]+)"', row, re.IGNORECASE)
        if m_app:
            raw_app = m_app.group(1).strip()
            if "," not in raw_app and raw_app.isdigit():
                appid = int(raw_app)
            elif "," in raw_app:
                first_part = raw_app.split(",", 1)[0].strip()
                if first_part.isdigit():
                    appid = int(first_part)

        packageid: int | None = None
        m_pkg = re.search(r'data-ds-packageid="(\d+)"', row, re.IGNORECASE)
        if m_pkg:
            packageid = int(m_pkg.group(1))

        bundleid: int | None = None
        m_bnd = re.search(r'data-ds-bundleid="(\d+)"', row, re.IGNORECASE)
        if m_bnd:
            bundleid = int(m_bnd.group(1))

        if appid is None and packageid is None and bundleid is None:
            continue

        m_pf = re.search(r'data-price-final="(\d+)"', row, re.IGNORECASE)
        if not m_pf:
            continue
        price_cents = int(m_pf.group(1))
        if price_cents <= 0:
            continue

        m_title = re.search(r'<span[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</span>', row, re.DOTALL | re.IGNORECASE)
        title = html.unescape(re.sub(r'<[^>]+>', '', m_title.group(1)).strip()) if m_title else f"App {appid or packageid or bundleid}"

        m_disc_price = re.search(r'class="[^"]*discount_final_price[^"]*"[^>]*>([^<]+)<', row, re.IGNORECASE)
        m_plain_price = re.search(r'class="[^"]*search_price\b[^"]*"[^>]*>(.*?)</div>', row, re.DOTALL | re.IGNORECASE)
        if m_disc_price:
            price_formatted = html.unescape(m_disc_price.group(1).strip())
        elif m_plain_price:
            price_formatted = html.unescape(re.sub(r'<[^>]+>', ' ', m_plain_price.group(1)).strip())
            price_formatted = re.sub(r'\s+', ' ', price_formatted)
        else:
            price_formatted = f"{price_cents / 100.0:.2f} {info['currency']}"

        m_orig = re.search(r'class="[^"]*discount_original_price[^"]*"[^>]*>([^<]+)<', row, re.IGNORECASE)
        original_formatted = html.unescape(m_orig.group(1).strip()) if m_orig else ""

        m_disc = re.search(r'data-discount="(\d+)"', row, re.IGNORECASE)
        discount_pct = int(m_disc.group(1)) if m_disc else 0

        dedup = (appid, packageid, bundleid)
        if dedup in seen_ids:
            continue
        seen_ids.add(dedup)

        items.append({
            "appid": appid,
            "packageid": packageid,
            "bundleid": bundleid,
            "title": title,
            "price_cents": price_cents,
            "price_unit": price_cents / 100.0,
            "price_formatted": price_formatted,
            "original_price_formatted": original_formatted,
            "discount_pct": discount_pct,
            "currency": info["currency"],
            "cc": info["steam_cc"],
            "url": href or (f"https://store.steampowered.com/app/{appid}/" if appid else URL_STORE),
        })
    items.sort(key=lambda x: (x["price_cents"], 0 if x["appid"] and not x["bundleid"] else 1))
    return items

async def fetch_account_userdata(steam, login: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {
        "owned_apps": set(),
        "owned_packages": set(),
        "country_code": None,
    }
    try:
        cache_bust = f"_src_ud={int(time.time())}_{secrets.token_hex(3)}"
        raw = await _net(steam.request, f"{URL_USERDATA}?{cache_bust}", label=login)
        text = await raw.text() if hasattr(raw, "text") and asyncio.iscoroutinefunction(raw.text) else (raw.text() if hasattr(raw, "text") else str(raw))
        data = json.loads(text)
        if isinstance(data, dict):
            apps = data.get("rgOwnedApps") or []
            pkgs = data.get("rgOwnedPackages") or []
            out["owned_apps"] = {int(x) for x in apps if str(x).isdigit()}
            out["owned_packages"] = {int(x) for x in pkgs if str(x).isdigit()}
            cc = data.get("rgCountryCode") or data.get("country_code")
            if cc and len(str(cc)) == 2:
                out["country_code"] = str(cc).upper()
            _log_event("steam_userdata_fetched", login=login, country=out["country_code"], owned_apps=len(out["owned_apps"]), owned_pkgs=len(out["owned_packages"]))
    except Exception as e:
        logger.debug(f"[{login}] fetch_account_userdata error: {e}")
    return out

async def resolve_game_packageid(
    strategy: ProxyRequestStrategy,
    appid: int,
    steam_cc: str,
    expected_price_cents: int | None = None,
) -> dict[str, Any] | None:
    cc = (steam_cc or "US").upper()
    try:
        url = f"{URL_APPDETAILS}?appids={int(appid)}&cc={cc}&filters=packages,package_groups,price_overview,is_free"
        resp = await strategy.request(url, method="GET")
        if resp.status == 200:
            payload = await resp.json(content_type=None)
            entry = (payload or {}).get(str(appid)) or {}
            if entry.get("success") and isinstance(entry.get("data"), dict):
                data = entry["data"]
                if data.get("is_free"):
                    return None
                po = data.get("price_overview") or {}
                currency = str(po.get("currency") or "")
                candidates: list[dict[str, Any]] = []
                for pg in (data.get("package_groups") or []):
                    for sub in (pg.get("subs") or []):
                        pkg_id = sub.get("packageid")
                        if not pkg_id:
                            continue
                        if sub.get("is_free_license"):
                            continue
                        sub_price = int(sub.get("price_in_cents_with_discount") or 0)
                        if sub_price <= 0:
                            continue
                        opt_text = str(sub.get("option_text") or "")
                        opt_low = opt_text.lower()
                        if any(m in opt_low for m in _COMMERCIAL_SUB_MARKERS):
                            continue
                        candidates.append({
                            "packageid": int(pkg_id),
                            "price_cents": sub_price,
                            "currency": currency,
                            "option_text": re.sub(r'<[^>]+>', '', opt_text).strip(),
                        })
                if candidates:
                    candidates.sort(key=lambda x: (
                        abs(x["price_cents"] - expected_price_cents) if expected_price_cents else x["price_cents"],
                        x["price_cents"],
                    ))
                    best = candidates[0]
                    if expected_price_cents and best["price_cents"] > max(expected_price_cents * 3, expected_price_cents + 500):
                        candidates.sort(key=lambda x: x["price_cents"])
                        best = candidates[0]
                    return best
                pkgs = data.get("packages") or []
                if pkgs and int(po.get("final") or 0) > 0:
                    return {
                        "packageid": int(pkgs[0]),
                        "price_cents": int(po.get("final") or 0),
                        "currency": currency,
                        "option_text": "",
                    }
    except Exception as e:
        logger.debug(f"[{NAME}] appdetails resolve error appid={appid} cc={cc}: {e}")

    try:
        app_url = f"https://store.steampowered.com/app/{int(appid)}/?cc={cc}"
        resp = await strategy.request(
            app_url,
            method="GET",
            headers={"Cookie": "birthtime=568022401; wants_mature_content=1; lastagecheckage=1-0-1988"},
        )
        if resp.status == 200:
            page_html = await resp.text()
            blocks = re.findall(
                r'<div[^>]*class="[^"]*game_area_purchase_game\b[^"]*"[^>]*>(.*?)</div>\s*</div>',
                page_html,
                re.DOTALL | re.IGNORECASE,
            )
            found_subs: list[tuple[int, int]] = []
            for blk in blocks:
                blk_low = blk.lower()
                if any(m in blk_low for m in _COMMERCIAL_SUB_MARKERS):
                    continue
                if "/freelicense/" in blk_low:
                    continue
                m_sub = re.search(r'<form[^>]*name="add_to_cart_(\d+)"', blk, re.IGNORECASE)
                if not m_sub:
                    m_sub = re.search(r'name="subid"\s+value="(\d+)"', blk, re.IGNORECASE)
                if not m_sub:
                    continue
                subid = int(m_sub.group(1))
                m_p = re.search(r'data-price-final="(\d+)"', blk, re.IGNORECASE)
                p_cents = int(m_p.group(1)) if m_p else (expected_price_cents or 0)
                if p_cents > 0 or expected_price_cents:
                    found_subs.append((p_cents or 99999999, subid))
            if found_subs:
                found_subs.sort(key=lambda x: x[0])
                return {
                    "packageid": found_subs[0][1],
                    "price_cents": found_subs[0][0] if found_subs[0][0] < 99999999 else (expected_price_cents or 0),
                    "currency": "",
                    "option_text": "",
                }
            m_any = re.search(r'name="subid"\s+value="(\d+)"', page_html, re.IGNORECASE)
            if m_any:
                return {
                    "packageid": int(m_any.group(1)),
                    "price_cents": expected_price_cents or 0,
                    "currency": "",
                    "option_text": "",
                }
    except Exception as e:
        logger.debug(f"[{NAME}] app page resolve error appid={appid} cc={cc}: {e}")
    return None

async def search_cheap_games(
    strategy: ProxyRequestStrategy | None,
    country_code: str,
    *,
    limit: int = 10,
    owned_apps: set[int] | None = None,
    owned_packages: set[int] | None = None,
    specials_only: bool | None = None,
    max_price_multiplier: float | None = None,
    custom_maxprice: int | None = None,
    resolve_packages: bool = False,
) -> list[dict[str, Any]]:
    cfg = _load_config()
    info = get_region_price_info(country_code)
    use_specials = bool(cfg.get("search_specials_only", True)) if specials_only is None else bool(specials_only)
    mult = float(cfg.get("search_max_price_multiplier", 2.0)) if max_price_multiplier is None else float(max_price_multiplier)
    mult = max(1.0, mult)

    owned_apps_set = owned_apps or set()
    owned_pkgs_set = owned_packages or set()

    own_strategy = strategy is None
    st = strategy if strategy is not None else ProxyRequestStrategy(None)
    try:
        modes_to_try = [use_specials]
        if use_specials:
            modes_to_try.append(False)

        raw_items: list[dict[str, Any]] = []
        used_url = ""
        for sp in modes_to_try:
            mp = custom_maxprice if custom_maxprice is not None else int(max(1, round(info["maxprice"] * max(1.0, mult / 2.0))))
            search_url = build_steam_cheap_search_url(
                country_code,
                maxprice=mp,
                specials=sp,
                count=50,
                start=0,
                for_results_api=True,
            )
            used_url = search_url
            _log_event("cheap_game_search", country=info["code"], steam_cc=info["steam_cc"], maxprice=mp, specials=sp)
            try:
                resp = await st.request(
                    search_url,
                    method="GET",
                    headers={
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": build_steam_cheap_search_url(country_code, maxprice=mp, specials=sp, for_results_api=False),
                    },
                )
                if resp.status != 200:
                    logger.warning(f"[{NAME}] Steam search HTTP {resp.status} для {info['code']}")
                    continue
                data = await resp.json(content_type=None)
                results_html = (data or {}).get("results_html") or ""
                parsed = parse_steam_search_html(results_html, country_code)
                filtered = [
                    it for it in parsed
                    if (it["appid"] is None or it["appid"] not in owned_apps_set)
                    and (it["packageid"] is None or it["packageid"] not in owned_pkgs_set)
                ]
                if filtered:
                    raw_items = filtered
                    break
            except Exception as e:
                logger.warning(f"[{NAME}] Ошибка поиска дешёвых игр ({info['code']}, specials={sp}): {e}")
                continue

        if not raw_items:
            _log_event("cheap_game_search_empty", level=logging.WARNING, country=info["code"], url=used_url[:100])
            return []

        target_cents = int(info["target_cents"])
        hard_limit_cents = int(round(target_cents * mult))
        preferred = [it for it in raw_items if it["price_cents"] <= hard_limit_cents]
        selected = preferred if preferred else raw_items
        selected = selected[:max(1, limit)]

        if resolve_packages:
            resolved: list[dict[str, Any]] = []
            for it in selected:
                item = dict(it)
                if item.get("packageid") is None and item.get("appid"):
                    pkg_info = await resolve_game_packageid(
                        st,
                        int(item["appid"]),
                        info["steam_cc"],
                        expected_price_cents=item.get("price_cents"),
                    )
                    if pkg_info and pkg_info.get("packageid"):
                        if int(pkg_info["packageid"]) in owned_pkgs_set:
                            continue
                        item["packageid"] = int(pkg_info["packageid"])
                        if pkg_info.get("price_cents"):
                            item["resolved_price_cents"] = int(pkg_info["price_cents"])
                        if pkg_info.get("currency"):
                            item["currency"] = pkg_info["currency"]
                resolved.append(item)
            selected = resolved

        _log_event(
            "cheap_game_search_ok",
            country=info["code"],
            found=len(selected),
            cheapest=selected[0]["title"][:35] if selected else "",
            price=selected[0]["price_formatted"] if selected else "",
        )
        return selected
    finally:
        if own_strategy:
            await st.close()

async def _extract_steam_access_token(strategy: ProxyRequestStrategy, steam=None) -> str | None:
    try:
        sess = strategy._get_session()
        for cookie in sess.cookie_jar:
            if str(cookie.key).lower() == "steamloginsecure":
                raw_val = unquote(str(cookie.value or ""))
                if "||" in raw_val:
                    tok = raw_val.split("||", 1)[1].strip()
                    if tok and len(tok) > 20:
                        return tok
    except Exception:
        pass

    for dom in ("store.steampowered.com", "steamcommunity.com"):
        try:
            ck = strategy.cookies(dom)
            for k, v in ck.items():
                if str(k).lower() == "steamloginsecure":
                    raw_val = unquote(str(v or ""))
                    if "||" in raw_val:
                        tok = raw_val.split("||", 1)[1].strip()
                        if tok and len(tok) > 20:
                            return tok
        except Exception:
            pass

    try:
        resp = await strategy.request(URL_ASYNC_CONFIG, method="GET")
        if resp.status == 200:
            data = await resp.json(content_type=None)
            tok = ((data or {}).get("data") or {}).get("webapi_token")
            if tok and len(str(tok)) > 20:
                return str(tok).strip()
    except Exception:
        pass

    try:
        resp = await strategy.request(URL_CART, method="GET")
        if resp.status == 200:
            txt = await resp.text()
            for pat in (
                r'data-loyalty_webapi_token="(?:&quot;|")?([A-Za-z0-9_\-\.]{30,})(?:&quot;|")?"',
                r'"webapi_token"\s*:\s*"([A-Za-z0-9_\-\.]{30,})"',
            ):
                m = re.search(pat, txt)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass

    return None

async def _clear_steam_cart(strategy: ProxyRequestStrategy, access_token: str | None, login: str = "") -> bool:
    if not access_token:
        return False
    try:
        resp = await strategy.request(
            f"{URL_CART_API_DELETE}?access_token={access_token}",
            method="POST",
            data=aiohttp.FormData(fields=[("input_json", "{}")]),
            headers={
                "Origin": "https://store.steampowered.com",
                "Referer": URL_CART,
            },
        )
        ok = (200 <= resp.status < 300)
        _log_event("steam_cart_clear", login=login, status=resp.status)
        return ok
    except Exception as e:
        logger.debug(f"[{login}] clear_cart error: {e}")
        return False

async def _verify_steam_cart(
    strategy: ProxyRequestStrategy,
    steam,
    packageid: int | None,
    bundleid: int | None,
    access_token: str | None,
    login: str = "",
) -> tuple[bool, dict[str, Any]]:
    meta: dict[str, Any] = {"items_count": 0, "subtotal": ""}
    if access_token:
        try:
            resp = await strategy.request(
                f"{URL_CART_API_GET}?access_token={access_token}",
                method="GET",
                headers={
                    "Origin": "https://store.steampowered.com",
                    "Referer": URL_CART,
                },
            )
            if resp.status == 200:
                data = await resp.json(content_type=None)
                cart = ((data or {}).get("response") or {}).get("cart") or {}
                line_items = cart.get("line_items") or []
                meta["items_count"] = len(line_items)
                subtotal_obj = cart.get("subtotal") or {}
                if isinstance(subtotal_obj, dict):
                    meta["subtotal"] = str(subtotal_obj.get("formatted_amount") or "")
                for li in line_items:
                    if packageid and int(li.get("packageid") or 0) == int(packageid):
                        return True, meta
                    if bundleid and int(li.get("bundleid") or 0) == int(bundleid):
                        return True, meta
                if line_items:
                    return True, meta
        except Exception as e:
            logger.debug(f"[{login}] GetCart API verify error: {e}")

    try:
        resp = await strategy.request(URL_CART, method="GET")
        if resp.status == 200:
            txt = await resp.text()
            if packageid and str(packageid) in txt:
                meta["items_count"] = max(1, meta["items_count"])
                return True, meta
            if bundleid and str(bundleid) in txt:
                meta["items_count"] = max(1, meta["items_count"])
                return True, meta
    except Exception as e:
        logger.debug(f"[{login}] cart HTML verify error: {e}")
    return False, meta

async def add_game_to_cart(
    strategy: ProxyRequestStrategy,
    steam,
    session_id: str,
    country_code: str,
    game: dict[str, Any],
    login: str = "",
    clear_cart_first: bool = True,
) -> dict[str, Any]:
    info = get_region_price_info(country_code)
    steam_cc = info["steam_cc"]
    appid = game.get("appid")
    packageid = game.get("packageid")
    bundleid = game.get("bundleid")

    if not packageid and not bundleid and appid:
        pkg_info = await resolve_game_packageid(
            strategy,
            int(appid),
            steam_cc,
            expected_price_cents=game.get("price_cents"),
        )
        if pkg_info and pkg_info.get("packageid"):
            packageid = int(pkg_info["packageid"])
            game = dict(game, packageid=packageid)
            if pkg_info.get("currency"):
                game["currency"] = pkg_info["currency"]

    if not packageid and not bundleid:
        return {
            "ok": False,
            "error": f"Не удалось определить packageid (subid) для игры {game.get('title', appid)}",
            "game": game,
        }

    access_token = await _extract_steam_access_token(strategy, steam)
    if clear_cart_first and access_token:
        await _clear_steam_cart(strategy, access_token, login=login)

    added_ok = False
    method_used = ""
    last_err = ""

    if access_token:
        try:
            item_payload: dict[str, Any] = {
                "flags": {"is_gift": False, "is_private": False},
            }
            if packageid:
                item_payload["packageid"] = int(packageid)
            else:
                item_payload["bundleid"] = int(bundleid)
            input_obj = {
                "user_country": steam_cc,
                "items": [item_payload],
            }
            resp = await strategy.request(
                f"{URL_CART_API_ADD}?access_token={access_token}",
                method="POST",
                data=aiohttp.FormData(fields=[("input_json", json.dumps(input_obj))]),
                headers={
                    "Origin": "https://store.steampowered.com",
                    "Referer": game.get("url") or URL_CART,
                },
            )
            eresult = str(resp.headers.get("X-eresult") or "").strip()
            body_text = await resp.text()
            _log_event(
                "steam_cart_api_add",
                login=login,
                status=resp.status,
                eresult=eresult or "none",
                packageid=packageid,
                bundleid=bundleid,
                cc=steam_cc,
            )
            if 200 <= resp.status < 300 and (eresult in ("", "1")):
                try:
                    j = json.loads(body_text) if body_text else {}
                    resp_obj = (j or {}).get("response") or {}
                    lids = resp_obj.get("line_item_ids") or []
                    cart_items = ((resp_obj.get("cart") or {}).get("line_items") or [])
                    if lids or cart_items:
                        added_ok = True
                        method_used = "IAccountCartService"
                    else:
                        last_err = f"IAccountCartService вернул пустую корзину ({body_text[:100]})"
                except Exception:
                    added_ok = True
                    method_used = "IAccountCartService"
            else:
                last_err = f"IAccountCartService HTTP {resp.status} eresult={eresult}"
        except Exception as e:
            last_err = f"IAccountCartService: {str(e)[:100]}"
            logger.debug(f"[{login}] {last_err}")

    if not added_ok:
        try:
            store_sid = await _ensure_session_cookie(strategy, "store.steampowered.com")
            sid_to_use = store_sid or session_id
            fields = [
                ("snr", "1_5_9__403"),
                ("originating_snr", ""),
                ("action", "add_to_cart"),
                ("sessionid", sid_to_use),
            ]
            if packageid:
                fields.append(("subid", str(int(packageid))))
            else:
                fields.append(("bundleid", str(int(bundleid))))
            resp = await strategy.request(
                URL_CART_ADD_FORM,
                method="POST",
                data=aiohttp.FormData(fields=fields),
                headers={
                    "Origin": "https://store.steampowered.com",
                    "Referer": game.get("url") or URL_STORE,
                },
                allow_redirects=True,
            )
            st_code = getattr(resp, "status", 0)
            body_text = await resp.text()
            _log_event("steam_cart_form_add", login=login, status=st_code, packageid=packageid, bundleid=bundleid)
            if 200 <= st_code < 400:
                added_ok = True
                method_used = "store_form"
            else:
                last_err = f"store_form HTTP {st_code}"
        except Exception as e:
            last_err = f"store_form: {str(e)[:100]}"
            logger.warning(f"[{login}] Ошибка добавления в корзину (form): {last_err}")

    if not added_ok:
        return {
            "ok": False,
            "error": last_err or "Не удалось добавить игру в корзину Steam",
            "game": game,
        }

    try:
        store_sid = await _ensure_session_cookie(strategy, "store.steampowered.com")
        await set_country_raw(strategy, steam_cc, store_sid or session_id)
    except Exception as e:
        logger.debug(f"[{login}] post-cart setcountry error: {e}")

    verified, cart_meta = await _verify_steam_cart(
        strategy, steam, packageid, bundleid, access_token, login=login
    )
    _log_event(
        "steam_cart_ready",
        login=login,
        status="ok" if verified else "unverified",
        method=method_used,
        appid=appid,
        packageid=packageid,
        title=str(game.get("title", ""))[:40],
        price=game.get("price_formatted", ""),
        subtotal=cart_meta.get("subtotal", ""),
        cc=steam_cc,
    )
    return {
        "ok": True,
        "verified": verified,
        "method": method_used,
        "subtotal": cart_meta.get("subtotal") or game.get("price_formatted", ""),
        "items_count": cart_meta.get("items_count", 1),
        "game": dict(game, packageid=packageid, bundleid=bundleid),
    }

def _detect_card_payment_method(card_number: str) -> str:
    digits = re.sub(r"\D", "", str(card_number or ""))
    if not digits:
        return "visa"
    if digits.startswith("4"):
        return "visa"
    p4 = int(digits[:4]) if len(digits) >= 4 else 0
    p2 = int(digits[:2]) if len(digits) >= 2 else 0
    if 2200 <= p4 <= 2204:
        return "mir"
    if (51 <= p2 <= 55) or (2221 <= p4 <= 2720):
        return "mastercard"
    if p2 in (34, 37):
        return "amex"
    if 3528 <= p4 <= 3589:
        return "jcb"
    if p4 == 6011 or p2 == 65 or (644 <= (int(digits[:3]) if len(digits) >= 3 else 0) <= 649):
        return "discover"
    return "visa"

def _build_billing_fields(card: dict[str, Any], steam_cc: str) -> dict[str, str]:
    cc = (steam_cc or "US").strip().upper()
    if cc == "EU":
        cc = "DE"
    defaults = REGION_DEFAULT_BILLING.get(cc) or REGION_DEFAULT_BILLING["US"]
    holder = str(card.get("holder") or "").strip()
    if holder:
        parts = holder.split(None, 1)
        first_name = parts[0][:40]
        last_name = (parts[1] if len(parts) > 1 else defaults.get("last", "Smith"))[:40]
    else:
        first_name = defaults.get("first", "Alex")
        last_name = defaults.get("last", "Smith")
    return {
        "FirstName": first_name,
        "LastName": last_name,
        "Address": str(card.get("address") or "").strip() or defaults["address"],
        "AddressTwo": str(card.get("address_two") or "").strip(),
        "Country": cc,
        "City": str(card.get("city") or "").strip() or defaults["city"],
        "State": str(card.get("state") or "").strip() or defaults["state"],
        "PostalCode": str(card.get("zip") or "").strip() or defaults["zip"],
        "Phone": str(card.get("phone") or "").strip() or defaults["phone"],
    }

def _decode_checkout_error(detail: int | None, specific_text: str = "") -> tuple[str, str]:
    txt = str(specific_text or "").strip()
    txt_low = txt.lower()
    if any(kw in txt_low for kw in (
        "3 months", "90 days", "recently changed", "country recently",
        "3 месяца", "90 дней", "недавно изменяли", "недавно сменили",
    )):
        return "region_cooldown", txt or "Регион аккаунта менялся менее 3 месяцев назад (ограничение Steam)"
    if detail in (9, 24, 47):
        labels = {
            9: "Игра уже есть на аккаунте",
            24: "Для покупки требуется базовая игра",
            47: "Дубликат товара в корзине",
        }
        return "already_owned", txt or labels.get(detail, f"Неподходящий товар (detail={detail})")
    if detail == 23:
        return "country_mismatch", txt or "Страна магазина не совпадает со страной биллинга (detail=23)"
    if detail in (1, 2, 6, 33, 55, 82, 83) or any(kw in txt_low for kw in (
        "declined", "insufficient", "invalid card", "invalid credit", "cvv", "cvc",
        "expired", "rejected", "not authorized", "отклонен", "недостаточно средств", "неверный номер карты",
    )):
        labels = {
            1: "Ошибка проверки платёжного адреса карты (AVS)",
            2: "Недостаточно средств на карте",
            6: "Способ оплаты не поддерживается для этого региона",
            33: "Срок действия карты истёк",
            55: "Неверный номер карты или данные карты",
            82: "Платёж отклонён банком-эмитентом",
            83: "Карта отклонена системой безопасности Steam",
        }
        return "card_declined", txt or labels.get(detail or 0, f"Карта отклонена Steam (detail={detail})")
    if detail == 11:
        return "checkout_failed", txt or "Платёж отклонён антифродом Steam (FraudCheckFailed, detail=11)"
    if detail == 79:
        return "card_declined", txt or "Карта или платёж отклонены провайдером (detail=79)"
    if detail in (44, 46):
        return "checkout_failed", txt or "Покупки на аккаунте ограничены или заблокированы Steam"
    if detail == 53:
        return "checkout_failed", txt or "Слишком много попыток оплаты на аккаунте (Steam rate limit)"
    if detail in (8, 45, 52):
        return "checkout_failed", txt or f"На аккаунте есть незавершённая транзакция Steam (detail={detail})"
    return "checkout_failed", txt or f"Ошибка оплаты Steam (detail={detail})"

async def _sync_checkout_cookies(strategy: ProxyRequestStrategy) -> None:
    try:
        from yarl import URL as _YarlURL
        session = strategy._get_session()
        merged: dict[str, str] = {}
        for cookie in session.cookie_jar:
            dom = str(cookie.get("domain") or "").lstrip(".").lower()
            if dom.endswith("steampowered.com") and cookie.value:
                merged[str(cookie.key)] = str(cookie.value)
        for dom in ("steamcommunity.com", "checkout.steampowered.com", "store.steampowered.com"):
            for k, v in strategy.cookies(dom).items():
                if v:
                    merged[str(k)] = str(v)
        if "timezoneOffset" not in merged:
            merged["timezoneOffset"] = "7200,0"
        if merged:
            session.cookie_jar.update_cookies(merged, response_url=_YarlURL("https://checkout.steampowered.com/"))
    except Exception as e:
        logger.debug(f"[{NAME}] _sync_checkout_cookies warning: {e}")
    try:
        await _ensure_session_cookie(strategy, "checkout.steampowered.com")
    except Exception:
        pass

async def _cancel_steam_transaction(strategy: ProxyRequestStrategy, transid: str, checkout_sid: str) -> None:
    if not transid or str(transid) in ("-1", "0"):
        return
    try:
        await strategy.request(
            URL_CHECKOUT_CANCEL,
            method="POST",
            data=aiohttp.FormData(fields=[("transid", str(transid)), ("sessionid", str(checkout_sid or ""))]),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://checkout.steampowered.com",
                "Referer": URL_CHECKOUT_PAGE,
            },
        )
    except Exception:
        pass

async def _steam_checkout_pay_with_card(
    strategy: ProxyRequestStrategy,
    steam,
    session_id: str,
    country_code: str,
    card: dict[str, Any],
    login: str = "",
    order_id: str = "",
    cancel_event: Any = None,
) -> dict[str, Any]:
    info = get_region_price_info(country_code)
    steam_cc = info["steam_cc"]
    masked_card = _mask_card_number(card.get("number", ""))
    payment_method = str(card.get("payment_method") or "").strip().lower() or _detect_card_payment_method(card.get("number", ""))
    effective_card = dict(card)
    if bool(card.get("auto_billing", False)) or not str(card.get("address") or "").strip():
        try:
            gen_addr = await asyncio.to_thread(fetch_random_billing_address, steam_cc)
            if isinstance(gen_addr, dict) and gen_addr.get("address"):
                effective_card["holder"] = gen_addr.get("holder") or effective_card.get("holder", "")
                effective_card["address"] = gen_addr.get("address") or effective_card.get("address", "")
                effective_card["address_two"] = gen_addr.get("address_two", "")
                effective_card["city"] = gen_addr.get("city") or effective_card.get("city", "")
                effective_card["state"] = gen_addr.get("state") or effective_card.get("state", "")
                effective_card["zip"] = gen_addr.get("zip") or effective_card.get("zip", "")
                effective_card["phone"] = gen_addr.get("phone") or effective_card.get("phone", "")
                _log_event(
                    "checkout_billing_generated",
                    login=login,
                    order_id=order_id,
                    country=steam_cc,
                    source=gen_addr.get("source", ""),
                    city=effective_card.get("city", ""),
                    state=effective_card.get("state", ""),
                    zip=effective_card.get("zip", ""),
                )
        except Exception as e:
            logger.debug(f"[{login}] dynamic billing generation error: {e}")
    billing = _build_billing_fields(effective_card, steam_cc)

    await _sync_checkout_cookies(strategy)

    checkout_sid = session_id
    shopping_cart_gid = "-1"
    use_account_cart = "1"
    has_billing_states = steam_cc in ("US", "CA", "AU", "BR", "IN", "JP", "MX")
    try:
        resp_page = await strategy.request(
            URL_CHECKOUT_PAGE,
            method="GET",
            headers={
                "Referer": URL_CART,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        page_html = await resp_page.text()
        final_url = str(getattr(resp_page, "url", "") or "")
        if "/login" in final_url.lower():
            return {"ok": False, "category": "checkout_failed", "detail": None, "error": "Сессия checkout.steampowered.com не авторизована"}
        m_sid = re.search(r'g_sessionID\s*=\s*["\']([^"\']+)["\']', page_html)
        if m_sid:
            checkout_sid = m_sid.group(1).strip()
        else:
            chk_cookies = strategy.cookies("checkout.steampowered.com")
            if chk_cookies.get("sessionid"):
                checkout_sid = str(chk_cookies["sessionid"])
        m_gid = re.search(r'id=["\']shopping_cart_gid["\'][^>]*value=["\']([^"\']+)["\']', page_html, re.IGNORECASE)
        if not m_gid:
            m_gid = re.search(r'name=["\']shopping_cart_gid["\'][^>]*value=["\']([^"\']+)["\']', page_html, re.IGNORECASE)
        if m_gid:
            shopping_cart_gid = m_gid.group(1).strip() or "-1"
        if re.search(r'g_bUseAccountCart\s*=\s*false', page_html, re.IGNORECASE) and shopping_cart_gid != "-1":
            use_account_cart = "0"
        m_st_flag = re.search(r'g_bHasBillingStates\s*=\s*(true|false)', page_html, re.IGNORECASE)
        if m_st_flag:
            has_billing_states = (m_st_flag.group(1).lower() == "true")
    except Exception as e:
        _log_event("checkout_page_error", level=logging.WARNING, login=login, order_id=order_id, err=str(e)[:120])
        return {"ok": False, "category": "checkout_failed", "detail": None, "error": f"Не удалось открыть страницу оплаты Steam: {str(e)[:120]}"}

    card_pan = re.sub(r"\D", "", str(card.get("number", "")))
    card_exp_y = re.sub(r"\D", "", str(card.get("exp_year", "")))
    if len(card_exp_y) == 2:
        card_exp_y = "20" + card_exp_y
    card_exp_m = re.sub(r"\D", "", str(card.get("exp_month", ""))).zfill(2)
    effective_state = billing["State"] if has_billing_states else ""

    def _build_init_fields() -> list[tuple[str, str]]:
        return [
            ("gidShoppingCart", str(shopping_cart_gid)),
            ("gidReplayOfTransID", "-1"),
            ("bUseAccountCart", str(use_account_cart)),
            ("PaymentMethod", payment_method),
            ("abortPendingTransactions", "1"),
            ("bHasCardInfo", "1"),
            ("CardNumber", card_pan),
            ("CardExpirationYear", card_exp_y),
            ("CardExpirationMonth", card_exp_m),
            ("FirstName", billing["FirstName"]),
            ("LastName", billing["LastName"]),
            ("Address", billing["Address"]),
            ("AddressTwo", billing["AddressTwo"]),
            ("Country", billing["Country"]),
            ("City", billing["City"]),
            ("State", effective_state),
            ("PostalCode", billing["PostalCode"]),
            ("Phone", billing["Phone"]),
            ("ShippingFirstName", ""),
            ("ShippingLastName", ""),
            ("ShippingAddress", ""),
            ("ShippingAddressTwo", ""),
            ("ShippingCountry", billing["Country"]),
            ("ShippingCity", ""),
            ("ShippingState", ""),
            ("ShippingPostalCode", ""),
            ("ShippingPhone", ""),
            ("bIsGift", "0"),
            ("GifteeAccountID", "0"),
            ("GifteeEmail", ""),
            ("GifteeName", ""),
            ("GiftMessage", ""),
            ("Sentiment", ""),
            ("Signature", ""),
            ("ScheduledSendOnDate", "0"),
            ("BankAccount", ""),
            ("BankCode", ""),
            ("BankIBAN", ""),
            ("BankBIC", ""),
            ("TPBankID", ""),
            ("BankAccountID", ""),
            ("bSaveBillingAddress", "0"),
            ("bUseRemainingSteamAccount", "0"),
            ("gidPaymentID", ""),
            ("bPreAuthOnly", "0"),
            ("sessionid", str(checkout_sid or "")),
        ]

    ajax_headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://checkout.steampowered.com",
        "Referer": URL_CHECKOUT_PAGE,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    init_data: dict[str, Any] = {}
    for init_try in (1, 2):
        _log_event(
            "checkout_init_attempt",
            login=login,
            order_id=order_id,
            country=steam_cc,
            method=payment_method,
            card=masked_card,
            attempt=init_try,
        )
        try:
            resp_init = await strategy.request(
                URL_CHECKOUT_INIT,
                method="POST",
                data=aiohttp.FormData(fields=_build_init_fields()),
                headers=ajax_headers,
            )
            init_data = await resp_init.json(content_type=None)
            if not isinstance(init_data, dict):
                init_data = {}
        except Exception as e:
            _log_event("checkout_init_exception", level=logging.WARNING, login=login, order_id=order_id, err=str(e)[:120])
            return {"ok": False, "category": "checkout_failed", "detail": None, "error": f"Ошибка inittransaction: {str(e)[:120]}"}

        init_success = int(init_data.get("success") or 0)
        init_detail = init_data.get("purchaseresultdetail")
        try:
            init_detail_int = int(init_detail) if init_detail is not None else None
        except (TypeError, ValueError):
            init_detail_int = None
        specific_err = str(init_data.get("specificerrortext") or init_data.get("strErrorMessage") or "").strip()
        transid = str(init_data.get("transid") or "-1")

        _log_event(
            "checkout_init_result",
            login=login,
            order_id=order_id,
            success=init_success,
            detail=init_detail_int,
            transid=transid,
            card=masked_card,
            err=specific_err[:120],
        )

        if init_success == 1 and transid not in ("", "-1", "0"):
            break

        if init_detail_int == 23 and init_try == 1:
            try:
                await set_country_raw(strategy, steam_cc, checkout_sid or session_id)
                await asyncio.sleep(1.0)
            except Exception:
                pass
            continue

        cat, human_err = _decode_checkout_error(init_detail_int, specific_err)
        return {
            "ok": False,
            "category": cat,
            "detail": init_detail_int,
            "transid": transid if transid not in ("", "-1", "0") else None,
            "error": human_err,
        }
    else:
        return {"ok": False, "category": "checkout_failed", "detail": 23, "error": "Страна магазина не совпадает со страной биллинга"}

    transid = str(init_data.get("transid"))
    final_total = ""
    try:
        fp_params = urllib.parse.urlencode({
            "count": "1",
            "transid": transid,
            "purchasetype": "self",
            "microtxnid": "-1",
            "cart": str(shopping_cart_gid),
            "gidReplayOfTransID": "-1",
        })
        resp_fp = await strategy.request(
            f"{URL_CHECKOUT_FINAL_PRICE}?{fp_params}",
            method="GET",
            headers=ajax_headers,
        )
        fp_data = await resp_fp.json(content_type=None)
        if isinstance(fp_data, dict):
            final_total = str(fp_data.get("formattedTotal") or fp_data.get("formattedProviderTotal") or "").strip()
            fp_succ = int(fp_data.get("success") or 1)
            if fp_succ != 1:
                fp_det = fp_data.get("purchaseresultdetail")
                try:
                    fp_det_int = int(fp_det) if fp_det is not None else None
                except (TypeError, ValueError):
                    fp_det_int = None
                fp_err = str(fp_data.get("specificerrortext") or fp_data.get("strErrorMessage") or "").strip()
                await _cancel_steam_transaction(strategy, transid, checkout_sid)
                cat, human_err = _decode_checkout_error(fp_det_int, fp_err)
                return {"ok": False, "category": cat, "detail": fp_det_int, "transid": transid, "error": human_err}
    except Exception as e:
        logger.debug(f"[{login}] getfinalprice warning: {e}")

    browser_info = json.dumps({
        "language": "ru-RU",
        "javaEnabled": "false",
        "colorDepth": 24,
        "screenHeight": 1080,
        "screenWidth": 1920,
    })

    try:
        resp_fin = await strategy.request(
            URL_CHECKOUT_FINALIZE,
            method="POST",
            data=aiohttp.FormData(fields=[
                ("transid", transid),
                ("CardCVV2", str(card.get("cvv", ""))),
                ("browserInfo", browser_info),
            ]),
            headers=ajax_headers,
        )
        fin_data = await resp_fin.json(content_type=None)
        if not isinstance(fin_data, dict):
            fin_data = {}
    except Exception as e:
        _log_event("checkout_finalize_exception", level=logging.WARNING, login=login, order_id=order_id, transid=transid, err=str(e)[:120])
        await _cancel_steam_transaction(strategy, transid, checkout_sid)
        return {"ok": False, "category": "checkout_failed", "detail": None, "transid": transid, "error": f"Ошибка finalizetransaction: {str(e)[:120]}"}

    fin_succ = int(fin_data.get("success") or 0)
    fin_det = fin_data.get("purchaseresultdetail")
    try:
        fin_det_int = int(fin_det) if fin_det is not None else None
    except (TypeError, ValueError):
        fin_det_int = None
    fin_err = str(fin_data.get("specificerrortext") or fin_data.get("strErrorMessage") or "").strip()

    _log_event(
        "checkout_finalize_result",
        login=login,
        order_id=order_id,
        transid=transid,
        success=fin_succ,
        detail=fin_det_int,
        card=masked_card,
        err=fin_err[:120],
    )

    if fin_succ == 1 or (fin_succ == 22 and fin_det_int == 29):
        return {"ok": True, "transid": transid, "total": final_total}

    if fin_succ == 22 and fin_det_int == 86:
        await _cancel_steam_transaction(strategy, transid, checkout_sid)
        return {
            "ok": False,
            "category": "card_declined",
            "detail": 86,
            "transid": transid,
            "error": "Карта требует доп. подтверждения банка (отклонена)",
        }

    if fin_succ == 22:
        for poll_i in range(1, 13):
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                await _cancel_steam_transaction(strategy, transid, checkout_sid)
                return {"ok": False, "category": "checkout_failed", "detail": fin_det_int, "transid": transid, "error": "Оплата отменена"}
            await asyncio.sleep(3.0)
            try:
                resp_st = await strategy.request(
                    f"{URL_CHECKOUT_STATUS}?count={poll_i}&transid={urllib.parse.quote(transid)}",
                    method="GET",
                    headers=ajax_headers,
                )
                st_data = await resp_st.json(content_type=None)
                if not isinstance(st_data, dict):
                    continue
                st_succ = int(st_data.get("success") or 0)
                st_det = st_data.get("purchaseresultdetail")
                try:
                    st_det_int = int(st_det) if st_det is not None else None
                except (TypeError, ValueError):
                    st_det_int = None
                if st_succ == 1 or (st_succ == 22 and st_det_int == 29):
                    _log_event("checkout_status_confirmed", login=login, order_id=order_id, transid=transid, poll=poll_i)
                    return {"ok": True, "transid": transid, "total": final_total}
                if st_succ == 22 and st_det_int == 86:
                    await _cancel_steam_transaction(strategy, transid, checkout_sid)
                    return {
                        "ok": False,
                        "category": "card_declined",
                        "detail": 86,
                        "transid": transid,
                        "error": "Карта требует доп. подтверждения банка (отклонена)",
                    }
                if st_succ != 22:
                    st_err = str(st_data.get("specificerrortext") or st_data.get("strErrorMessage") or "").strip()
                    rcpt = st_data.get("purchasereceipt") if isinstance(st_data.get("purchasereceipt"), dict) else {}
                    auth_det = st_data.get("authenticationdetails")
                    rcpt_info = {
                        k: rcpt.get(k)
                        for k in ("resultdetail", "purchasestatus", "paymentmethod", "errorheadline", "errordetail", "providererrordetail")
                        if rcpt.get(k) not in (None, "")
                    }
                    _log_event(
                        "checkout_status_result",
                        level=logging.WARNING,
                        login=login,
                        order_id=order_id,
                        transid=transid,
                        poll=poll_i,
                        success=st_succ,
                        detail=st_det_int,
                        err=st_err[:160],
                        receipt=json.dumps(rcpt_info, ensure_ascii=False)[:220] if rcpt_info else "",
                        auth=str(auth_det)[:160] if auth_det else "",
                    )
                    cat, human_err = _decode_checkout_error(st_det_int, st_err)
                    return {"ok": False, "category": cat, "detail": st_det_int, "transid": transid, "error": human_err}
            except Exception:
                continue
        await _cancel_steam_transaction(strategy, transid, checkout_sid)
        return {
            "ok": False,
            "category": "checkout_failed",
            "detail": fin_det_int,
            "transid": transid,
            "error": "Транзакция Steam не завершилась в отведённое время",
        }

    await _cancel_steam_transaction(strategy, transid, checkout_sid)
    cat, human_err = _decode_checkout_error(fin_det_int, fin_err)
    return {
        "ok": False,
        "category": cat,
        "detail": fin_det_int,
        "transid": transid,
        "error": human_err,
    }

async def _region_change_core(
    steam,
    strategy,
    session_id,
    login: str,
    country_code: str,
    max_setcountry_attempts: int = 1,
    lot_cfg: dict[str, Any] | None = None,
    order_id: str = "",
    cancel_event: Any = None,
    on_checkout_start_cb: Optional[Callable[[dict[str, Any]], Any]] = None,
) -> AccountRegionResult | None:
    info = get_region_price_info(country_code)
    target_cc = info["code"]
    steam_cc = info["steam_cc"]

    userdata = await fetch_account_userdata(steam, login=login)
    owned_apps: set[int] = userdata.get("owned_apps") or set()
    owned_packages: set[int] = userdata.get("owned_packages") or set()

    try:
        account_cc = await _fetch_steam_page_country(steam, URL_ACCOUNT, login)
    except Exception:
        account_cc = None

    if account_cc and account_cc.upper() == steam_cc:
        logger.info(f"[{login}] На аккаунте уже установлен целевой регион {steam_cc}")
        return AccountRegionResult(login, RegionResult.ALREADY, new_region=target_cc)

    logger.info(f"[{login}] Выполняем setcountry для региона {steam_cc} перед поиском и добавлением игры в корзину...")
    try:
        await set_country_raw(strategy, steam_cc, session_id)
    except Exception as e:
        logger.debug(f"[{login}] pre-search setcountry error: {e}")

    games = await search_cheap_games(
        strategy,
        target_cc,
        limit=12,
        owned_apps=owned_apps,
        owned_packages=owned_packages,
        resolve_packages=False,
    )
    if not games:
        err_msg = f"Не удалось найти дешёвую игру для региона {_country_display(target_cc)} ({info['label']})"
        logger.warning(f"[{login}] {err_msg}")
        return AccountRegionResult(
            login,
            RegionResult.FAIL_NO_CHEAP_GAME,
            error=err_msg,
            failure_reason=FailureReason.NO_CHEAP_GAME,
        )

    cfg = _load_config()
    clear_cart = bool(cfg.get("clear_cart_before_add", True))
    auto_checkout = bool(cfg.get("auto_checkout_card", True))
    lot_id_str = str(lot_cfg.get("id")) if isinstance(lot_cfg, dict) and lot_cfg.get("id") else None
    checkout_notified = False
    last_cart_err = ""
    max_candidates = min(5, len(games))
    for idx, candidate in enumerate(games[:max_candidates], 1):
        logger.info(
            f"[{login}] Пробуем добавить игру в корзину ({idx}/{max_candidates}): "
            f"{candidate.get('title')} (appid={candidate.get('appid')}, цена={candidate.get('price_formatted')})"
        )
        add_res = await add_game_to_cart(
            strategy=strategy,
            steam=steam,
            session_id=session_id,
            country_code=target_cc,
            game=candidate,
            login=login,
            clear_cart_first=clear_cart,
        )
        if not add_res.get("ok"):
            last_cart_err = str(add_res.get("error") or "ошибка добавления")
            logger.warning(f"[{login}] Не удалось добавить {candidate.get('title')} в корзину: {last_cart_err}")
            continue

        final_game = add_res.get("game") or candidate
        cart_subtotal = str(add_res.get("subtotal") or final_game.get("price_formatted") or "")
        cart_verified = bool(add_res.get("verified"))
        cart_method = str(add_res.get("method") or "")

        if not auto_checkout:
            matched_card, card_source = _select_card_for_order(lot_cfg, target_cc)
            return AccountRegionResult(
                login=login,
                status=RegionResult.CART_READY,
                new_region=target_cc,
                cart_game=final_game,
                cart_verified=cart_verified,
                cart_method=cart_method,
                cart_subtotal=cart_subtotal,
                matched_card=matched_card,
                card_source=card_source,
            )

        candidate_cards = _get_candidate_cards_for_order(lot_cfg, target_cc)
        if not candidate_cards:
            return AccountRegionResult(
                login=login,
                status=RegionResult.FAIL_NO_CARDS,
                new_region=target_cc,
                cart_game=final_game,
                cart_verified=cart_verified,
                cart_method=cart_method,
                cart_subtotal=cart_subtotal,
                error=f"В базе нет активной карты для региона {_country_display(target_cc)} ({target_cc})",
                failure_reason=FailureReason.NO_CARDS,
            )

        try_next_game = False
        last_pay_err = ""
        last_pay_cat = "checkout_failed"
        last_pay_detail: int | None = None
        last_pay_transid: str | None = None
        last_pay_card: dict[str, Any] | None = None
        last_pay_src: str | None = None

        auto_switch = bool(cfg.get("auto_switch_card", True))
        cards_to_try = candidate_cards if auto_switch else candidate_cards[:1]
        if on_checkout_start_cb is not None and not checkout_notified:
            checkout_notified = True
            try:
                cb_ret = on_checkout_start_cb(final_game)
                if asyncio.iscoroutine(cb_ret):
                    await cb_ret
            except Exception as e:
                logger.debug(f"[{login}] on_checkout_start_cb error: {e}")
        for card_idx, (card, card_src) in enumerate(cards_to_try, 1):
            last_pay_card = card
            last_pay_src = card_src
            logger.info(
                f"[{login}] Автооплата Steam картой ({card_idx}/{len(cards_to_try)}): "
                f"{_mask_card_number(card.get('number', ''))} ({card_src}) для {target_cc}"
            )
            pay_res = await _steam_checkout_pay_with_card(
                strategy=strategy,
                steam=steam,
                session_id=session_id,
                country_code=target_cc,
                card=card,
                login=login,
                order_id=order_id,
                cancel_event=cancel_event,
            )
            if pay_res.get("ok"):
                _increment_card_usage(card.get("id", ""), card_src, lot_id_str)
                verified_reg = False
                try:
                    verified_reg, _ = await verify_region_change(steam, login, steam_cc)
                except Exception as ve:
                    logger.warning(f"[{login}] verify_region_change error after checkout: {ve}")
                paid_total = str(pay_res.get("total") or cart_subtotal)
                return AccountRegionResult(
                    login=login,
                    status=RegionResult.OK,
                    new_region=target_cc,
                    cart_game=final_game,
                    cart_verified=True,
                    cart_method=cart_method,
                    cart_subtotal=paid_total,
                    matched_card=card,
                    card_source=card_src,
                    purchase_completed=True,
                    region_verified=verified_reg,
                    transid=str(pay_res.get("transid") or ""),
                )

            last_pay_cat = str(pay_res.get("category") or "checkout_failed")
            last_pay_err = str(pay_res.get("error") or "Ошибка оплаты картой")
            last_pay_detail = pay_res.get("detail")
            last_pay_transid = pay_res.get("transid")

            if last_pay_cat == "already_owned":
                logger.info(f"[{login}] Товар не подошёл для checkout ({last_pay_err}), пробуем следующую игру...")
                try_next_game = True
                break
            if last_pay_cat == "region_cooldown":
                return AccountRegionResult(
                    login=login,
                    status=RegionResult.FAIL_REGION_COOLDOWN,
                    new_region=target_cc,
                    cart_game=final_game,
                    cart_verified=cart_verified,
                    cart_subtotal=cart_subtotal,
                    matched_card=card,
                    card_source=card_src,
                    error=last_pay_err,
                    failure_reason=FailureReason.REGION_COOLDOWN,
                    transid=last_pay_transid,
                    checkout_detail=last_pay_detail,
                )
            if last_pay_cat == "card_declined":
                _mark_card_failed(card.get("id", ""), card_src, lot_id_str, last_pay_err)
                logger.warning(f"[{login}] Карта {_mask_card_number(card.get('number', ''))} отклонена: {last_pay_err}")
                if auto_switch and card_idx < len(cards_to_try):
                    logger.info(f"[{login}] Автосмена карты: пробуем следующую карту из базы...")
                    continue
                break
            if auto_switch and card_idx < len(cards_to_try):
                _mark_card_failed(card.get("id", ""), card_src, lot_id_str, last_pay_err)
                logger.warning(f"[{login}] Проблема оплаты картой ({last_pay_err}), автосмена на следующую карту...")
                continue
            break

        if try_next_game:
            last_cart_err = last_pay_err
            continue

        if last_pay_cat == "card_declined":
            return AccountRegionResult(
                login=login,
                status=RegionResult.FAIL_CARD_DECLINED,
                new_region=target_cc,
                cart_game=final_game,
                cart_verified=cart_verified,
                cart_subtotal=cart_subtotal,
                matched_card=last_pay_card,
                card_source=last_pay_src,
                error=last_pay_err,
                failure_reason=FailureReason.CARD_DECLINED,
                transid=last_pay_transid,
                checkout_detail=last_pay_detail,
            )

        return AccountRegionResult(
            login=login,
            status=RegionResult.FAIL_CHECKOUT,
            new_region=target_cc,
            cart_game=final_game,
            cart_verified=cart_verified,
            cart_subtotal=cart_subtotal,
            matched_card=last_pay_card,
            card_source=last_pay_src,
            error=last_pay_err,
            failure_reason=FailureReason.CHECKOUT_FAILED,
            transid=last_pay_transid,
            checkout_detail=last_pay_detail,
        )

    err_msg = f"Не удалось добавить дешёвую игру в корзину Steam для региона {target_cc}: {last_cart_err}"
    logger.warning(f"[{login}] {err_msg}")
    return AccountRegionResult(
        login,
        RegionResult.FAIL_CART_ADD,
        error=err_msg,
        failure_reason=FailureReason.CART_ADD_FAILED,
    )
async def process_one_account(
    login: str,
    password: str,
    shared_secret: str | None,
    proxy_pool: "ProxyPool",
    country_code: str,
    semaphore: asyncio.Semaphore,
    guard_provider: GuardProvider | None = None,
    max_attempts_override: int | None = None,
    cancel_event: asyncio.Event | None = None,
    lot_cfg: dict[str, Any] | None = None,
    order_id: str = "",
    on_checkout_start_cb: Optional[Callable[[dict[str, Any]], Any]] = None,
) -> AccountRegionResult:
    async with semaphore:
        if cancel_event is not None and cancel_event.is_set():
            return AccountRegionResult(login, RegionResult.SKIPPED, error="отменено пользователем")
        manual_mode = guard_provider is not None
        if manual_mode:
            Steam = await _try_import_manual_steam()
        else:
            Steam = await _try_import_pysteamauth()
        if Steam is None:
            return AccountRegionResult(
                login=login,
                status=RegionResult.ERROR,
                error="pysteamauth не установлен. pip install pysteamauth",
            )
        if max_attempts_override is not None:
            max_attempts = max(1, max_attempts_override)
        else:
            proxy_count = max(1, proxy_pool.total_configured)
            max_attempts = min(REGION_MAX_TOTAL_ATTEMPTS, max(2, proxy_count * REGION_ATTEMPTS_PER_PROXY))
        last_status = RegionResult.FAIL_CHANGE
        last_error: str | None = None
        guard_rejections = 0
        guard_reject_limit = 3
        reuse_proxy_on_guard = False
        proxy = None
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            if attempt > 1:
                if last_status == RegionResult.FAIL_RATE_LIMIT:
                    rl_delay = RATE_LIMIT_DELAY
                    try:
                        rl_delay = int(_load_config().get("rate_limit_delay", RATE_LIMIT_DELAY))
                    except Exception:
                        pass
                    logger.info(
                        f"[{login}] rate-limit Steam — пауза {rl_delay}с "
                        f"(попытка {attempt}/{max_attempts})"
                    )
                    await asyncio.sleep(rl_delay)
                elif reuse_proxy_on_guard and proxy:
                    logger.info(f"[{login}] повторная попытка ввода Guard на том же прокси (попытка {attempt}/{max_attempts})")
                else:
                    logger.info(f"[{login}] следующий прокси (попытка {attempt}/{max_attempts})")
            if not (reuse_proxy_on_guard and proxy):
                proxy = proxy_pool.get_next()
            reuse_proxy_on_guard = False
            if proxy is None:
                _log_event("account_proxy_exhausted", level=logging.WARNING, login=login, reason="pool_empty")
                last_status, last_error = RegionResult.FAIL_SESSION, "Все прокси недоступны (исключены из пула)"
                break
            masked_px = _mask_proxy(proxy)
            _log_event("account_attempt", login=login, attempt=attempt, max=max_attempts, proxy=masked_px)
            strategy = ProxyRequestStrategy(proxy)
            try:
                if manual_mode:
                    steam = Steam(login=login, password=password, request_strategy=strategy)
                    steam.set_guard_provider(guard_provider)
                else:
                    steam = Steam(
                        login=login,
                        password=password,
                        shared_secret=shared_secret,
                        request_strategy=strategy,
                    )
                try:
                    await _net(steam.login_to_steam, label=login)
                except GuardCodeSkipped:
                    return AccountRegionResult(login, RegionResult.SKIPPED, error="пропущено пользователем")
                except GuardCodeRejected as e:
                    guard_rejections += 1
                    last_status, last_error = RegionResult.FAIL_WRONG_GUARD, str(e)
                    _log_event("account_guard_rejected", level=logging.WARNING, login=login, count=guard_rejections, limit=guard_reject_limit)
                    if guard_rejections >= guard_reject_limit:
                        return AccountRegionResult(
                            login, RegionResult.FAIL_WRONG_GUARD,
                            error="Неверный/просроченный Guard-код (исчерпаны попытки ввода)",
                            failure_reason=FailureReason.WRONG_GUARD,
                        )
                    reuse_proxy_on_guard = True
                    continue
                except Exception as e:
                    msg = str(e)
                    if "shared_secret is not specified" in msg:
                        return AccountRegionResult(login, RegionResult.FAIL_NO_MAFILE, error=msg)
                    if isinstance(e, SteamLoginFailed) and "неверный логин или пароль" in msg.lower():
                        return AccountRegionResult(login, RegionResult.FAIL_WRONG_PASS, error="Неверный логин или пароль Steam", failure_reason=FailureReason.WRONG_CREDENTIALS)
                    m = re.search(r"'code':\s*(\d+)", msg)
                    code_num = int(m.group(1)) if m else None
                    if getattr(e, "error_code", None) is not None:
                        try:
                            code_num = int(getattr(e, "error_code"))
                        except (ValueError, TypeError):
                            pass
                    if code_num == 5 or "InvalidPassword" in msg or getattr(e, "error_code", None) == 5:
                        return AccountRegionResult(login, RegionResult.FAIL_WRONG_PASS, error="Неверный логин или пароль Steam", failure_reason=FailureReason.WRONG_CREDENTIALS)
                    if code_num in (65, 88) or getattr(e, "error_code", None) in (65, 88):
                        guard_rejections += 1
                        last_status, last_error = RegionResult.FAIL_WRONG_GUARD, "Неверный Guard-код"
                        _log_event("account_guard_rejected", level=logging.WARNING, login=login, count=guard_rejections, limit=guard_reject_limit)
                        if guard_rejections >= guard_reject_limit:
                            return AccountRegionResult(
                                login, RegionResult.FAIL_WRONG_GUARD,
                                error="Неверный/просроченный Guard-код (исчерпаны попытки ввода)",
                                failure_reason=FailureReason.WRONG_GUARD,
                            )
                        reuse_proxy_on_guard = True
                        continue
                    status = getattr(e, "status", None)
                    err_low = msg.lower()
                    if status == 429 or code_num == 84 or getattr(e, "error_code", None) == 84 or any(kw in err_low for kw in ("429", "ratelimit", "rate limit")):
                        last_status, last_error = RegionResult.FAIL_RATE_LIMIT, msg
                        _log_event("account_rate_limit", level=logging.WARNING, login=login, code=84, attempt=attempt)
                        await asyncio.sleep(3.0)
                        continue
                    if (status in (500, 502, 503, 504)) or any(kw in err_low for kw in ("500", "502", "503", "504", "temporary unavailable", "server disconnected", "service unavailable")):
                        last_status, last_error = RegionResult.FAIL_TEMP_ERROR, msg
                        _log_event("steam_temp_error", level=logging.WARNING, login=login, err=msg[:80])
                        await asyncio.sleep(3.0)
                        continue
                    if code_num in (15, 16):
                        proxy_pool.ban(proxy)
                        _log_event("account_proxy_error", level=logging.WARNING, login=login, proxy=masked_px, err=f"Steam AccessDenied (code {code_num})")
                        last_status, last_error = RegionResult.FAIL_SESSION, msg
                        continue
                    if _is_proxy_timeout(e):
                        proxy_pool.ban(proxy)
                        _log_event("proxy_timeout", level=logging.WARNING, login=login, proxy=masked_px, attempt=attempt, err=msg[:80], reason=FailureReason.PROXY_TIMEOUT)
                        last_status, last_error = RegionResult.FAIL_SESSION, msg
                        await asyncio.sleep(2.0)
                        continue
                    if _is_network_error(e):
                        proxy_pool.ban(proxy)
                        _log_event("account_proxy_error", level=logging.WARNING, login=login, proxy=masked_px, err=msg[:80])
                        last_status, last_error = RegionResult.FAIL_SESSION, msg
                        await asyncio.sleep(2.0)
                        continue
                    _log_event("account_steam_error", level=logging.WARNING, login=login, err=msg[:80])
                    await asyncio.sleep(1.0)
                    last_status, last_error = RegionResult.FAIL_SESSION, msg
                    continue
                if not await steam.is_authorized():
                    _log_event("account_not_authorized", level=logging.WARNING, login=login, attempt=attempt)
                    last_status = RegionResult.FAIL_SESSION
                    await asyncio.sleep(1.0)
                    continue
                try:
                    session_id = await _net(steam.sessionid, "store.steampowered.com", label=login)
                except Exception:
                    try:
                        session_id = await _net(steam.sessionid, "steamcommunity.com", label=login)
                    except Exception as e:
                        proxy_pool.ban(proxy)
                        _log_event("account_session_error", level=logging.WARNING, login=login, proxy=masked_px, err=str(e)[:80])
                        await asyncio.sleep(2.0)
                        continue
                result = await _region_change_core(
                    steam, strategy, session_id, login, country_code,
                    max_setcountry_attempts=1, lot_cfg=lot_cfg,
                    order_id=order_id, cancel_event=cancel_event,
                    on_checkout_start_cb=on_checkout_start_cb,
                )
                if result is None:
                    continue
                if result.status in (RegionResult.OK, RegionResult.CART_READY, RegionResult.ALREADY):
                    return result
                if result.status in (RegionResult.FAIL_CHANGE, RegionResult.FAIL_VERIFY_REGION):
                    last_status = result.status
                    last_error = result.error
                    _log_event("account_region_attempt_failed", level=logging.WARNING, login=login, status=result.status, error=result.error)
                    continue
                return result
            except Exception as e:
                last_error = str(e)
                status = getattr(e, "status", None)
                code_num = getattr(e, "error_code", None)
                err_low = str(e).lower()
                if status == 429 or code_num == 84 or any(kw in err_low for kw in ("429", "ratelimit", "rate limit")):
                    last_status, last_error = RegionResult.FAIL_RATE_LIMIT, str(e)
                    _log_event("account_rate_limit", level=logging.WARNING, login=login, code=84, attempt=attempt)
                    await asyncio.sleep(3.0)
                    continue
                elif (status in (500, 502, 503, 504)) or any(kw in err_low for kw in ("500", "502", "503", "504", "temporary unavailable", "server disconnected", "service unavailable")):
                    last_status, last_error = RegionResult.FAIL_TEMP_ERROR, str(e)
                    _log_event("steam_temp_error", level=logging.WARNING, login=login, err=str(e)[:80])
                    await asyncio.sleep(3.0)
                    continue
                elif _is_proxy_timeout(e):
                    proxy_pool.ban(proxy)
                    _log_event("proxy_timeout", level=logging.WARNING, login=login, proxy=masked_px, attempt=attempt, err=str(e)[:80], reason=FailureReason.PROXY_TIMEOUT)
                    last_status = RegionResult.FAIL_SESSION
                    await asyncio.sleep(2.0)
                elif _is_network_error(e):
                    proxy_pool.ban(proxy)
                    _log_event("account_net_error", level=logging.WARNING, login=login, proxy=masked_px, err=str(e)[:80])
                    last_status = RegionResult.FAIL_SESSION
                    await asyncio.sleep(2.0)
                else:
                    _log_event("account_error", level=logging.WARNING, login=login, err=str(e)[:80])
                    last_status = RegionResult.FAIL_SESSION
                    await asyncio.sleep(1.0)
                continue
            finally:
                await strategy.close()
        _log_event("account_change_failed", level=logging.WARNING, login=login, attempts=max_attempts, status=str(last_status))
        fail_rsn = None
        if proxy_pool is not None and len(proxy_pool) == 0:
            fail_rsn = FailureReason.PROXY_EXHAUSTED
        elif last_status == RegionResult.FAIL_RATE_LIMIT:
            fail_rsn = FailureReason.STEAM_RATE_LIMIT
        elif last_status == RegionResult.FAIL_TEMP_ERROR:
            fail_rsn = FailureReason.STEAM_TEMP_ERROR
        elif last_status == RegionResult.FAIL_VERIFY_REGION:
            fail_rsn = FailureReason.VERIFY_FAILED
        elif last_status == RegionResult.FAIL_WRONG_GUARD:
            fail_rsn = FailureReason.WRONG_GUARD
        elif last_status == RegionResult.FAIL_WRONG_PASS:
            fail_rsn = FailureReason.WRONG_CREDENTIALS
        elif last_status == RegionResult.FAIL_NO_CHEAP_GAME:
            fail_rsn = FailureReason.NO_CHEAP_GAME
        elif last_status == RegionResult.FAIL_CART_ADD:
            fail_rsn = FailureReason.CART_ADD_FAILED
        elif last_status == RegionResult.FAIL_NO_CARDS:
            fail_rsn = FailureReason.NO_CARDS
        elif last_status == RegionResult.FAIL_CARD_DECLINED:
            fail_rsn = FailureReason.CARD_DECLINED
        elif last_status == RegionResult.FAIL_REGION_COOLDOWN:
            fail_rsn = FailureReason.REGION_COOLDOWN
        elif last_status == RegionResult.FAIL_CHECKOUT:
            fail_rsn = FailureReason.CHECKOUT_FAILED
        return AccountRegionResult(
            login, last_status,
            error=last_error or f"Не удалось выполнить смену региона после {max_attempts} попыток",
            failure_reason=fail_rsn,
        )
async def process_one_account_qr(
    proxy_pool: "ProxyPool",
    country_code: str,
    display_cb: QRDisplay,
    poll_timeout: float = 180.0,
    begin_retries: int = 5,
    confirmed_cb: Optional[Callable[[str], Awaitable[None]]] = None,
    cancel_event: Optional[threading.Event] = None,
    order_id: str = "",
    lot_cfg: dict[str, Any] | None = None,
    on_checkout_start_cb: Optional[Callable[[dict[str, Any]], Any]] = None,
) -> AccountRegionResult:
    QRSteam = await _try_import_qr_steam()
    if QRSteam is None:
        return AccountRegionResult(order_id or "(qr)", RegionResult.ERROR,
                                   error="pysteamauth/QR недоступен. pip install pysteamauth")
    strategy = None
    steam = None
    account = None
    last_err: str | None = None
    tries = min(begin_retries, max(2, max(1, proxy_pool.total_configured) * 2))
    for _ in range(tries):
        if cancel_event and cancel_event.is_set():
            return AccountRegionResult(order_id or "(qr)", RegionResult.SKIPPED, error="QR отменён")
        proxy = proxy_pool.get_next()
        if proxy is None:
            return AccountRegionResult(order_id or "(qr)", RegionResult.FAIL_SESSION,
                                       error=last_err or "Все прокси недоступны (исключены из пула)",
                                       failure_reason=FailureReason.PROXY_EXHAUSTED)
        strategy = ProxyRequestStrategy(proxy)
        steam = QRSteam(login="qr_session", password="", request_strategy=strategy)
        try:
            account = await steam.login_via_qr(
                display_cb,
                poll_timeout=poll_timeout,
                confirmed_cb=confirmed_cb,
                cancel_event=cancel_event,
                order_id=order_id,
            )
            if cancel_event and cancel_event.is_set():
                await strategy.close()
                return AccountRegionResult(order_id or "(qr)", RegionResult.SKIPPED, error="QR отменён")
            break
        except asyncio.CancelledError:
            await strategy.close()
            raise
        except QRLoginCancelled as e:
            await strategy.close()
            return AccountRegionResult(order_id or "(qr)", RegionResult.SKIPPED, error=str(e), failure_reason=FailureReason.QR_TIMEOUT)
        except QRLoginTimeout as e:
            await strategy.close()
            return AccountRegionResult(order_id or "(qr)", RegionResult.SKIPPED, error=str(e), failure_reason=FailureReason.QR_TIMEOUT)
        except Exception as e:
            last_err = str(e)
            if strategy:
                await strategy.close()
                strategy = None
            err_code = getattr(e, "error_code", None)
            status = getattr(e, "status", None)
            err_str = str(e).lower()
            if (
                err_code in (9, 42, 2, 16)
                or "filenotfound" in err_str
                or "code': 9" in err_str
                or "code': 42" in err_str
                or "code': 2" in err_str
            ):
                return AccountRegionResult(order_id or "(qr)", RegionResult.SKIPPED, error="QR-код не подтверждён (истекло время действия кода)", failure_reason=FailureReason.QR_TIMEOUT)

            if status == 429 or err_code == 84 or any(kw in err_str for kw in ("429", "ratelimit", "rate limit")):
                _log_event("qr_rate_limit", level=logging.WARNING, order_id=order_id, err=str(e)[:80])
                return AccountRegionResult(order_id or "(qr)", RegionResult.FAIL_RATE_LIMIT, error=str(e), failure_reason=FailureReason.STEAM_RATE_LIMIT)

            if (status in (500, 502, 503, 504)) or any(kw in err_str for kw in ("500", "502", "503", "504", "temporary unavailable", "server disconnected", "service unavailable")):
                _log_event("qr_steam_temp_error", level=logging.WARNING, order_id=order_id, err=str(e)[:80])
                return AccountRegionResult(order_id or "(qr)", RegionResult.FAIL_TEMP_ERROR, error=str(e), failure_reason=FailureReason.STEAM_TEMP_ERROR)

            if _is_proxy_timeout(e):
                proxy_pool.ban(proxy)
                _log_event("proxy_timeout", level=logging.WARNING, order_id=order_id, proxy=_mask_proxy(proxy), err=str(e)[:80], reason=FailureReason.PROXY_TIMEOUT)
                await asyncio.sleep(2.0)
                continue

            if _is_network_error(e):
                proxy_pool.ban(proxy)
                logger.warning(f"[QR] ошибка прокси при начале сессии: {str(e)[:80]}")
                await asyncio.sleep(2.0)
                continue

            return AccountRegionResult(order_id or "(qr)", RegionResult.ERROR, error=str(e))
    if cancel_event and cancel_event.is_set():
        if strategy:
            await strategy.close()
        return AccountRegionResult(order_id or "(qr)", RegionResult.SKIPPED, error="QR отменён")
    if strategy is None:
        return AccountRegionResult(order_id or "(qr)", RegionResult.FAIL_SESSION,
                                   error=last_err or "нет рабочего прокси для QR",
                                   failure_reason=FailureReason.PROXY_EXHAUSTED)
    login = account or order_id or "(qr)"
    try:
        if cancel_event and cancel_event.is_set():
            return AccountRegionResult(login, RegionResult.SKIPPED, error="QR отменён")
        if not await steam.is_authorized():
            return AccountRegionResult(login, RegionResult.FAIL_SESSION, error="сессия не авторизована")
        try:
            session_id = await _net(steam.sessionid, "store.steampowered.com", label=login)
        except Exception:
            session_id = await _net(steam.sessionid, "steamcommunity.com", label=login)
        result = await _region_change_core(
            steam, strategy, session_id, login, country_code,
            max_setcountry_attempts=2, lot_cfg=lot_cfg,
            order_id=order_id, cancel_event=cancel_event,
            on_checkout_start_cb=on_checkout_start_cb,
        )
        if result is None:
            return AccountRegionResult(login, RegionResult.FAIL_CHANGE,
                                       error="не удалось сменить регион — повторите вход по QR")
        return result
    except Exception as e:
        logger.warning(f"[{login}] ошибка подготовки корзины после QR-входа: {e}")
        status = getattr(e, "status", None)
        err_str = str(e).lower()
        if status == 429 or any(kw in err_str for kw in ("429", "ratelimit", "rate limit")):
            return AccountRegionResult(login, RegionResult.FAIL_RATE_LIMIT, error=str(e), failure_reason=FailureReason.STEAM_RATE_LIMIT)
        if (status in (500, 502, 503, 504)) or any(kw in err_str for kw in ("500", "502", "503", "504", "temporary unavailable", "server disconnected", "service unavailable")):
            return AccountRegionResult(login, RegionResult.FAIL_TEMP_ERROR, error=str(e), failure_reason=FailureReason.STEAM_TEMP_ERROR)
        return AccountRegionResult(login, RegionResult.ERROR, error=str(e))
    finally:
        await strategy.close()
async def change_region_batch(
    accounts: list[dict],
    proxies: list[str],
    country_code: str,
    max_workers: int = 3,
    progress_cb = None,
    guard_provider: GuardProvider | None = None,
    max_attempts_override: int | None = None,
    cancel_event: asyncio.Event | None = None,
    lot_cfg: dict[str, Any] | None = None,
) -> BatchRegionResult:
    manual_mode = guard_provider is not None
    workers = 1 if manual_mode else max(1, max_workers)
    semaphore = asyncio.Semaphore(workers)
    batch = BatchRegionResult(country_code=country_code, total=len(accounts))
    proxy_pool = ProxyPool(proxies)
    async def _worker(acc: dict) -> AccountRegionResult:
        return await process_one_account(
            login = acc["login"],
            password = acc["password"],
            shared_secret = acc.get("shared_secret"),
            proxy_pool = proxy_pool,
            country_code = country_code,
            semaphore = semaphore,
            guard_provider = guard_provider,
            max_attempts_override = max_attempts_override,
            cancel_event = cancel_event,
            lot_cfg = lot_cfg,
        )
    if manual_mode:
        done = 0
        try:
            for acc in accounts:
                if cancel_event is not None and cancel_event.is_set():
                    batch.results.append(AccountRegionResult(
                        acc["login"], RegionResult.SKIPPED, error="отменено пользователем"))
                    done += 1
                    if progress_cb:
                        try:
                            await progress_cb(done, len(accounts), batch.results[-1])
                        except Exception:
                            pass
                    continue
                result = await _worker(acc)
                batch.results.append(result)
                done += 1
                logger.info(
                    f"[{result.login}] итог: {result.status}"
                    + (f" region={result.new_region}" if result.new_region else "")
                    + (f" error={result.error}" if result.error else "")
                )
                if progress_cb:
                    try:
                        await progress_cb(done, len(accounts), result)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            logger.info("change_region_batch (manual) отменён — частичный результат")
        return batch
    tasks = [asyncio.ensure_future(_worker(acc)) for acc in accounts]
    done = 0
    try:
        for coro in asyncio.as_completed(tasks):
            result = await coro
            batch.results.append(result)
            done += 1
            logger.info(
                f"[{result.login}] итог: {result.status}"
                + (f" region={result.new_region}" if result.new_region else "")
                + (f" error={result.error}" if result.error else "")
            )
            if progress_cb:
                try:
                    await progress_cb(done, len(accounts), result)
                except Exception:
                    pass
    except asyncio.CancelledError:
        for t in tasks:
            if not t.done():
                t.cancel()
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        batch.results = [r for r in gathered if isinstance(r, AccountRegionResult)]
        logger.info("change_region_batch отменён — частичный результат")
    return batch
def parse_proxies(text: str) -> list[str]:
    proxies: list[str] = []
    lines = (text or "").splitlines()
    for raw_line in lines:
        line = raw_line.strip().strip("'\"` \t")
        if not line or line.startswith("#"):
            continue

        norm = normalize_proxy_url(line)
        if norm:
            if norm not in proxies:
                proxies.append(norm)
            continue

        chunks = line.split()
        if len(chunks) > 1:
            for chunk in chunks:
                chunk = chunk.strip().strip("'\"` \t,;")
                if not chunk or chunk.startswith("#"):
                    continue
                cnorm = normalize_proxy_url(chunk)
                if cnorm and cnorm not in proxies:
                    proxies.append(cnorm)
        else:

            if "," in line:
                for sub in line.split(","):
                    sub = sub.strip().strip("'\"` \t")
                    if not sub or sub.startswith("#"):
                        continue
                    snorm = normalize_proxy_url(sub)
                    if snorm and snorm not in proxies:
                        proxies.append(snorm)
    logger.info(f"[{NAME}] Распознано {len(proxies)} прокси из входного текста")
    return proxies
def normalize_card_entry(raw: dict[str, Any], default_country: str = "ANY") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    number = re.sub(r"\D", "", str(raw.get("number") or raw.get("pan") or ""))
    if not (13 <= len(number) <= 19):
        return None
    exp_month = re.sub(r"\D", "", str(raw.get("exp_month") or raw.get("month") or ""))
    exp_year = re.sub(r"\D", "", str(raw.get("exp_year") or raw.get("year") or ""))
    if (not exp_month or not exp_year) and raw.get("expiry"):
        m_exp = re.match(r"^\s*(\d{1,2})\s*[/\-:.]\s*(\d{2,4})\s*$", str(raw.get("expiry")))
        if m_exp:
            exp_month, exp_year = m_exp.group(1), m_exp.group(2)
    if not exp_month or not exp_year:
        return None
    try:
        mm_int = int(exp_month)
        yy_int = int(exp_year)
    except ValueError:
        return None
    if not (1 <= mm_int <= 12):
        return None
    if yy_int < 100:
        yy_int += 2000
    if not (2024 <= yy_int <= 2099):
        return None
    cvv = re.sub(r"\D", "", str(raw.get("cvv") or raw.get("cvc") or ""))
    if not (3 <= len(cvv) <= 4):
        return None
    country = re.sub(r"[^A-Za-z]", "", str(raw.get("country") or default_country or "ANY")).upper()
    if country != "ANY" and len(country) != 2:
        country = default_country.upper() if default_country else "ANY"
    cid = str(raw.get("id") or "").strip() or f"card_{secrets.token_hex(4)}"
    try:
        used_count = max(0, int(raw.get("used_count", 0)))
    except Exception:
        used_count = 0
    try:
        max_uses = max(0, int(raw.get("max_uses", 0)))
    except Exception:
        max_uses = 0
    try:
        fail_count = max(0, int(raw.get("fail_count", 0)))
    except Exception:
        fail_count = 0
    pm_raw = str(raw.get("payment_method") or "").strip().lower()
    if pm_raw not in ("visa", "mastercard", "amex", "jcb", "discover", "mir"):
        pm_raw = _detect_card_payment_method(number)
    lim_act = str(raw.get("limit_action") or "").strip().lower()
    if lim_act not in ("none", "deactivate", "delete"):
        lim_act = "deactivate" if max_uses > 0 else "none"
    if lim_act == "none":
        max_uses = 0
    auto_billing = bool(raw.get("auto_billing", False))
    return {
        "id": cid,
        "country": country,
        "payment_method": pm_raw,
        "number": number,
        "exp_month": f"{mm_int:02d}",
        "exp_year": str(yy_int),
        "cvv": cvv,
        "holder": str(raw.get("holder") or "").strip(),
        "address": str(raw.get("address") or "").strip(),
        "address_two": str(raw.get("address_two") or "").strip(),
        "city": str(raw.get("city") or "").strip(),
        "state": str(raw.get("state") or "").strip(),
        "zip": str(raw.get("zip") or "").strip(),
        "phone": str(raw.get("phone") or "").strip(),
        "auto_billing": auto_billing,
        "label": str(raw.get("label") or "").strip(),
        "enabled": bool(raw.get("enabled", True)),
        "used_count": used_count,
        "max_uses": max_uses,
        "limit_action": lim_act,
        "fail_count": fail_count,
        "last_error": str(raw.get("last_error") or "").strip(),
        "created_at": float(raw.get("created_at") or time.time()),
    }

ADDRESS_SITE_SLUGS: dict[str, tuple[str, str]] = {
    "UA": ("ukraine-address", "ukraine"),
    "RU": ("russia-address", "russia"),
    "KZ": ("kazakhstan-address", "kazakhstan"),
    "US": ("america-address", "united-states"),
    "TR": ("turkey-address", "turkey"),
    "AR": ("argentina-address", "argentina"),
    "BR": ("brazil-address", "brazil"),
    "IN": ("india-address", "india"),
    "CN": ("china-address", "china"),
    "ID": ("indonesia-address", "indonesia"),
    "VN": ("vietnam-address", "vietnam"),
    "PH": ("philippines-address", "philippines"),
    "MY": ("malaysia-address", "malaysia"),
    "TH": ("thailand-address", "thailand"),
    "JP": ("japan-address", "japan"),
    "KR": ("korea-address", "south-korea"),
    "TW": ("taiwan-address", "taiwan"),
    "HK": ("hong-kong-address", "hong-kong"),
    "SG": ("singapore-address", "singapore"),
    "CA": ("canada-address", "canada"),
    "AU": ("australia-address", "australia"),
    "NZ": ("new-zealand-address", "new-zealand"),
    "GB": ("united-kingdom-address", "united-kingdom"),
    "EU": ("germany-address", "germany"),
    "DE": ("germany-address", "germany"),
    "FR": ("france-address", "france"),
    "ES": ("spain-address", "spain"),
    "IT": ("italy-address", "italy"),
    "NL": ("netherlands-address", "netherlands"),
    "CH": ("switzerland-address", "switzerland"),
    "PL": ("poland-address", "poland"),
    "NO": ("norway-address", "norway"),
    "MX": ("mexico-address", "mexico"),
    "CL": ("chile-address", "chile"),
    "CO": ("colombia-address", "colombia"),
    "PE": ("peru-address", "peru"),
    "UY": ("uruguay-address", "uruguay"),
    "CR": ("costa-rica-address", "costa-rica"),
    "ZA": ("south-africa-address", "south-africa"),
    "SA": ("saudi-arabia-address", "saudi-arabia"),
    "AE": ("united-arab-emirates-address", "united-arab-emirates"),
    "QA": ("qatar-address", "qatar"),
    "KW": ("kuwait-address", "kuwait"),
    "IL": ("israel-address", "israel"),
}

_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "ё": "e",
    "є": "ye", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "yi", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

def _translit_to_latin(text: str) -> str:
    s = str(text or "").replace("\xa0", " ").strip()
    if not s:
        return ""
    out: list[str] = []
    for ch in s:
        low = ch.lower()
        if low in _CYR_TO_LAT:
            rep = _CYR_TO_LAT[low]
            out.append(rep.capitalize() if ch.isupper() else rep)
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()

_SANCTIONED_BILLING_TOKENS: tuple[str, ...] = (
    "crimea", "krym", "sevastopol", "simferopol", "yalta", "kerch",
    "feodosiya", "feodosia", "evpatoriya", "yevpatoriya", "alushta",
    "donetsk", "lugansk", "luhansk", "mariupol", "makeyevka", "makiivka",
    "gorlovka", "horlivka", "yenakiieve", "kherson", "melitopol",
    "berdyansk", "enerhodar", "zaporozh", "zaporizh", "kramatorsk",
    "0692", "0652", "0642", "0622",
)

_SAFE_LOCAL_BILLING_POOLS: dict[str, list[dict[str, str]]] = {
    "UA": [
        {"first": "Oleksandr", "last": "Kovalenko", "address": "Khreshchatyk St 22", "city": "Kyiv", "state": "Kyiv", "zip": "01001", "phone": "+380672345678"},
        {"first": "Dmytro", "last": "Shevchenko", "address": "Svobody Ave 18", "city": "Lviv", "state": "Lvivska oblast", "zip": "79000", "phone": "+380503456789"},
        {"first": "Andrii", "last": "Bondarenko", "address": "Deribasivska St 14", "city": "Odesa", "state": "Odeska oblast", "zip": "65000", "phone": "+380934567890"},
        {"first": "Maksym", "last": "Tkachenko", "address": "Dmytra Yavornytskoho Ave 54", "city": "Dnipro", "state": "Dnipropetrovska oblast", "zip": "49000", "phone": "+380685678901"},
        {"first": "Ihor", "last": "Melnyk", "address": "Soborna St 42", "city": "Vinnytsia", "state": "Vinnytska oblast", "zip": "21000", "phone": "+380666789012"},
        {"first": "Serhii", "last": "Kravchenko", "address": "Sobornosti St 31", "city": "Poltava", "state": "Poltavska oblast", "zip": "36000", "phone": "+380977890123"},
    ],
    "KZ": [
        {"first": "Nurlan", "last": "Akhmetov", "address": "Abay Ave 52", "city": "Almaty", "state": "Almaty", "zip": "050008", "phone": "+77012345678"},
        {"first": "Arman", "last": "Suleimenov", "address": "Mangilik El Ave 28", "city": "Astana", "state": "Astana", "zip": "010000", "phone": "+77023456789"},
        {"first": "Timur", "last": "Kassymov", "address": "Tauke Khan Ave 41", "city": "Shymkent", "state": "Shymkent", "zip": "160000", "phone": "+77054567890"},
        {"first": "Daniyar", "last": "Nurgaliev", "address": "Bukhar Zhyrau Ave 64", "city": "Karaganda", "state": "Karaganda", "zip": "100000", "phone": "+77775678901"},
    ],
}

def _is_sanctioned_billing(raw_d: dict[str, str]) -> bool:
    blob = " ".join(
        _translit_to_latin(str(raw_d.get(k) or ""))
        for k in ("address", "address_two", "city", "state", "zip", "phone")
    ).lower()
    return any(tok in blob for tok in _SANCTIONED_BILLING_TOKENS)

def fetch_random_billing_address(country_code: str, timeout: float = 10.0) -> dict[str, str]:
    cc = str(country_code or "US").strip().upper()
    if cc == "ANY" or len(cc) != 2:
        cc = "US"
    lookup_cc = "DE" if cc == "EU" else cc
    pool = _SAFE_LOCAL_BILLING_POOLS.get(lookup_cc)
    defaults = random.choice(pool) if pool else (REGION_DEFAULT_BILLING.get(lookup_cc) or REGION_DEFAULT_BILLING["US"])
    ar_slug, ddt_slug = ADDRESS_SITE_SLUGS.get(cc, ("america-address", "united-states"))
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }

    def _clean(val: str) -> str:
        s = re.sub(r"\s+", " ", html.unescape(str(val or "")).replace("\xa0", " ")).strip()
        if s.lower() in ("n/a", "na", "none", "null", "-", "—"):
            return ""
        return s

    def _finalize_result(raw_d: dict[str, str], source: str, source_url: str) -> dict[str, str]:
        full_name = _translit_to_latin(_clean(raw_d.get("holder", "")))
        name_parts = [p for p in re.split(r"[\s,]+", full_name) if p and len(p) >= 3 and p.replace("-", "").isalpha()]
        if len(name_parts) >= 2:
            first_n, last_n = name_parts[0][:30], name_parts[1][:30]
        elif len(name_parts) == 1:
            first_n, last_n = name_parts[0][:30], defaults.get("last", "Smith")
        else:
            first_n, last_n = defaults.get("first", "Alex"), defaults.get("last", "Smith")
        addr = _translit_to_latin(_clean(raw_d.get("address", "")))
        if len(addr) < 3:
            addr = defaults["address"]
        addr2 = _translit_to_latin(_clean(raw_d.get("address_two", "")))
        city = _translit_to_latin(_clean(raw_d.get("city", "")))
        if "/" in city:
            city = city.split("/", 1)[0].strip()
        if len(city) < 2:
            city = defaults["city"]
        state = _translit_to_latin(_clean(raw_d.get("state", ""))) or defaults.get("state", "")
        zip_c = _clean(raw_d.get("zip", ""))
        if not zip_c:
            m_zip = re.search(r",\s*(\d{5,6}(?:-\d{3,4})?)\b", addr)
            zip_c = m_zip.group(1) if m_zip else defaults["zip"]
        phone_raw = _clean(raw_d.get("phone", "")) or defaults["phone"]
        phone_digits = re.sub(r"[^\d+]", "", phone_raw)
        if phone_digits.startswith("+3800") and len(phone_digits) > 13:
            phone_digits = "+380" + phone_digits[5:]
        if phone_digits and not phone_digits.startswith("+"):
            phone_digits = "+" + phone_digits
        phone = phone_digits if 8 <= len(phone_digits) <= 15 else defaults["phone"]
        return {
            "first": first_n,
            "last": last_n,
            "holder": f"{first_n} {last_n}".strip(),
            "address": addr[:120],
            "address_two": addr2[:80],
            "city": city[:50],
            "state": state[:40],
            "zip": zip_c[:20],
            "phone": phone[:25],
            "country": lookup_cc,
            "source": source,
            "source_url": source_url,
        }

    ar_url = f"https://www.addressrussia.com/{ar_slug}/"
    for _ar_try in range(2):
        try:
            resp = requests.get(ar_url, headers=hdrs, timeout=timeout)
            if resp.status_code == 200 and resp.text:
                pairs = {
                    _clean(k).lower(): _clean(v)
                    for k, v in re.findall(r"<dd>\s*<label>([^<]+)</label>\s*<span><b>([^<]*)</b></span>", resp.text, re.DOTALL | re.I)
                }
                street = pairs.get("translated street") or pairs.get("street") or pairs.get("address") or ""
                city = pairs.get("city") or pairs.get("town") or ""
                state = pairs.get("trans oblast") or pairs.get("state") or pairs.get("oblast") or pairs.get("province") or ""
                zip_code = pairs.get("zip code") or pairs.get("zip") or pairs.get("post code") or pairs.get("postal code") or pairs.get("postcode") or ""
                phone = pairs.get("phone number") or pairs.get("telephone") or pairs.get("phone") or ""
                full_name = pairs.get("full name") or pairs.get("name") or ""
                cand = {"holder": full_name, "address": street, "city": city, "state": state, "zip": zip_code, "phone": phone}
                if (street or city) and not _is_sanctioned_billing(cand):
                    return _finalize_result(cand, source="addressrussia.com", source_url=ar_url)
        except Exception as e:
            logger.debug(f"[{NAME}] addressrussia fetch error ({cc}): {e}")
            break

    ddt_url = f"https://dummydatatools.com/tools/random-address/{ddt_slug}/"
    try:
        resp = requests.get(ddt_url, headers=hdrs, timeout=timeout)
        if resp.status_code == 200 and resp.text:
            rows_ddt = {
                _clean(k).lower(): _clean(v)
                for k, v in re.findall(r'<td[^>]*class="df-t-field"[^>]*>([^<]+)</td>\s*<td[^>]*class="df-t-value"[^>]*>([^<]+)</td>', resp.text, re.DOTALL | re.I)
                if _clean(v)
            }
            cand = {
                "holder": rows_ddt.get("random name", ""),
                "address": rows_ddt.get("street", ""),
                "city": rows_ddt.get("city", ""),
                "state": rows_ddt.get("state/region", ""),
                "zip": rows_ddt.get("zip/postal code", ""),
                "phone": rows_ddt.get("phone number", ""),
            }
            if (cand["address"] or cand["city"]) and not _is_sanctioned_billing(cand):
                return _finalize_result(cand, source="dummydatatools.com", source_url=ddt_url)
    except Exception as e:
        logger.debug(f"[{NAME}] dummydatatools fetch error ({cc}): {e}")

    ra_url = f"https://www.randaddress.com/ru/genaddress/{lookup_cc.lower()}-address/"
    try:
        resp = requests.get(ra_url, headers=hdrs, timeout=timeout)
        if resp.status_code == 200 and resp.text:
            m_json = re.search(r'\{[^{}]*"street"\s*:\s*"([^"]+)"[^{}]*"city"\s*:\s*"([^"]+)"[^{}]*\}', resp.text, re.I)
            if m_json:
                cand = {"address": m_json.group(1), "city": m_json.group(2)}
                if not _is_sanctioned_billing(cand):
                    return _finalize_result(cand, source="randaddress.com", source_url=ra_url)
    except Exception as e:
        logger.debug(f"[{NAME}] randaddress fetch error ({cc}): {e}")

    base_addr = defaults["address"]
    rand_num = random.randint(10, 290)
    if re.search(r"\d+", base_addr):
        gen_addr = re.sub(r"\d+", str(rand_num), base_addr, count=1)
    else:
        gen_addr = f"{base_addr} {rand_num}"
    base_phone = defaults["phone"]
    gen_phone = base_phone[:-4] + f"{random.randint(1000, 9999)}" if len(base_phone) >= 8 else base_phone
    return _finalize_result(
        {
            "holder": f"{defaults.get('first', 'Alex')} {defaults.get('last', 'Smith')}",
            "address": gen_addr,
            "city": defaults["city"],
            "state": defaults.get("state", ""),
            "zip": defaults["zip"],
            "phone": gen_phone,
        },
        source="локальный генератор",
        source_url=ra_url,
    )

def parse_cards_text(text: str, default_country: str = "ANY") -> tuple[list[dict[str, Any]], list[str]]:
    raw_text = (text or "").strip()
    if not raw_text:
        return [], []
    if raw_text.startswith("[") or raw_text.startswith("{"):
        try:
            parsed_json = json.loads(raw_text)
            items_json = parsed_json if isinstance(parsed_json, list) else [parsed_json]
            valid_j: list[dict[str, Any]] = []
            bad_j: list[str] = []
            for idx, obj in enumerate(items_json, 1):
                entry = normalize_card_entry(obj, default_country=default_country)
                if entry:
                    valid_j.append(entry)
                else:
                    bad_j.append(f"JSON #{idx}")
            return valid_j, bad_j
        except Exception:
            pass

    valid: list[dict[str, Any]] = []
    invalid: list[str] = []
    def_cc = (default_country or "ANY").upper()

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        country = def_cc
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
        elif ";" in line:
            parts = [p.strip() for p in line.split(";")]
        elif ":" in line:
            parts = [p.strip() for p in line.split(":")]
        elif "," in line:
            parts = [p.strip() for p in line.split(",")]
        else:
            parts = line.split()

        if len(parts) >= 4 and re.fullmatch(r"[A-Za-z]{2,3}", parts[0]):
            first_up = parts[0].upper()
            if first_up == "ANY" or len(first_up) == 2:
                country = first_up
                parts = parts[1:]

        if len(parts) >= 3:
            num_raw = parts[0]
            rest = parts[1:]
            if len(re.sub(r"\D", "", num_raw)) < 13 and len(parts) >= 6:
                joined4 = "".join(parts[:4])
                if 13 <= len(re.sub(r"\D", "", joined4)) <= 19:
                    num_raw = joined4
                    rest = parts[4:]
            num = re.sub(r"\D", "", num_raw)
            mm = ""
            yy = ""
            cvv = ""
            extra_idx = 0
            if len(rest) >= 2 and re.match(r"^\d{1,2}\s*[/\-]\s*\d{2,4}$", rest[0]):
                m_e = re.match(r"^(\d{1,2})\s*[/\-]\s*(\d{2,4})$", rest[0])
                mm, yy = m_e.group(1), m_e.group(2)
                cvv = rest[1]
                extra_idx = 2
            elif len(rest) >= 3 and re.fullmatch(r"\d{1,2}", rest[0]) and re.fullmatch(r"\d{2,4}", rest[1]):
                mm, yy, cvv = rest[0], rest[1], rest[2]
                extra_idx = 3
            else:
                invalid.append(line[:40])
                continue

            extras = rest[extra_idx:]
            if extras and re.fullmatch(r"[A-Za-z]{2,3}", extras[0]) and (extras[0].upper() == "ANY" or len(extras[0]) == 2):
                country = extras[0].upper()
                extras = extras[1:]

            holder = extras[0] if len(extras) > 0 else ""
            address = extras[1] if len(extras) > 1 else ""
            city = extras[2] if len(extras) > 2 else ""
            zip_code = extras[3] if len(extras) > 3 else ""
            phone = extras[4] if len(extras) > 4 else ""
            label = extras[5] if len(extras) > 5 else ""

            entry = normalize_card_entry({
                "country": country,
                "number": num,
                "exp_month": mm,
                "exp_year": yy,
                "cvv": cvv,
                "holder": holder,
                "address": address,
                "city": city,
                "zip": zip_code,
                "phone": phone,
                "label": label,
            }, default_country=def_cc)
            if entry:
                valid.append(entry)
            else:
                invalid.append(line[:40])
        else:
            invalid.append(line[:40])

    return valid, invalid

def format_card_export_line(card: dict[str, Any]) -> str:
    mm = card.get("exp_month", "01")
    yy = str(card.get("exp_year", "2028"))[-2:]
    fields = [
        card.get("country", "ANY"),
        card.get("number", ""),
        f"{mm}/{yy}",
        card.get("cvv", ""),
    ]
    opt = [
        card.get("holder", ""),
        card.get("address", ""),
        card.get("city", ""),
        card.get("zip", ""),
        card.get("phone", ""),
        card.get("label", ""),
    ]
    while opt and not opt[-1]:
        opt.pop()
    return "|".join(fields + opt)

def _format_card_short(card: dict[str, Any]) -> str:
    st_ico = "🟢" if card.get("enabled", True) else "🔴"
    masked = _mask_card_number(card.get("number", ""))
    exp = f"{card.get('exp_month', '??')}/{str(card.get('exp_year', '??'))[-2:]}"
    cc = card.get("country", "ANY")
    cc_disp = _country_display(cc)
    lbl = f" · {html.escape(card['label'])}" if card.get("label") else ""
    return f"{st_ico} <code>{masked}</code> ({exp}) · {cc_disp}{lbl}"

cardinal_instance = None
bot_instance = None
admin_chat_id = None

BASE_DIR = os.getcwd()
STORAGE_DIR = os.path.join(BASE_DIR, "storage", "plugins", "src_plugin_v2")
CONFIG_FILE = os.path.join(STORAGE_DIR, "settings.json")
PROXIES_FILE = os.path.join(STORAGE_DIR, "proxies.txt")
PROXIES_BY_COUNTRY_FILE = os.path.join(STORAGE_DIR, "proxies_by_country.json")
CARDS_FILE = os.path.join(STORAGE_DIR, "cards.json")
MAFILES_DIR = os.path.join(STORAGE_DIR, "mafiles")
STATS_FILE = os.path.join(STORAGE_DIR, "stats.json")

_config_lock = threading.Lock()
_waiting: dict[int, dict[str, Any]] = {}

DEFAULT_BUYER_MESSAGES: dict[str, str] = {
    "welcome_menu": (
        "🎉 Оплата прошла успешно, спасибо за доверие!\n\n"
        "Для быстрой и безопасной смены региона выберите способ авторизации:\n"
        "1️⃣ По Логину и Паролю — напишите цифру 1\n"
        "2️⃣ По QR-коду (без ввода данных) — напишите цифру 2\n\n"
        "ℹ️ Если что-то пошло не так — напишите «заново» или «-», чтобы начать сначала."
    ),
    "welcome": (
        "🔹 Шаг 1 из 2: Логин аккаунта\n\n"
        "Пожалуйста, отправьте логин от вашего аккаунта Steam в этот чат:\n"
        "(Или отправьте сразу «логин:пароль»)"
    ),
    "ask_password": (
        "✅ Логин принят: {login}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔹 Шаг 2 из 2: Пароль аккаунта\n\n"
        "Теперь отправьте пароль от вашего аккаунта Steam в этот чат:\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    ),
    "qr_ready_prompt": (
        "Вы выбрали авторизацию по QR-коду. 📷\n\n"
        "⚠️ Напишите + в чат, когда будете готовы открыть мобильное приложение Steam (Steam Guard → Сканер) и отсканировать код."
    ),
    "welcome_qr": (
        "🛑 Отсканируйте и подтвердите вход в мобильном приложении Steam 🛑\n\n"
        "🟢 Вхожу с веб-браузера\n"
        "🟢 Локация — {country_name}\n\n"
        "🔗 Прямая ссылка для входа:\n{challenge_url}"
    ),
    "qr_timeout": (
        "⏳ QR-код истёк. Напишите 2 (или +) для нового QR-кода или 1 для входа по логину.\n\n"
        "ℹ️ Если что-то пошло не так — напишите «заново» или «-», чтобы начать сначала."
    ),
    "qr_confirmed": (
        "✅ Вход подтверждён. Начинаю смену..."
    ),
    "checkout_started": (
        "⏳ Провожу транзакцию..."
    ),
    "data_received": (
        "✅ Данные получены!\n\n"
        "⏳ Начинаем авторизацию в Steam и смену региона на {country_name}...\n"
        "Пожалуйста, оставайтесь на связи. При необходимости бот запросит код Steam Guard."
    ),
    "guard_request": (
        "🔐 Требуется код Steam Guard!\n\n"
        "{guard_destination}\n\n"
        "Пожалуйста, отправьте 5-значный код сообщением в этот чат:"
    ),
    "guard_retry": (
        "⚠️ Введённый проверочный код не подошёл или истёк срок его действия!\n"
        "(Срок действия кода Steam составляет около 2 минут).\n\n"
        "🔄 Запрошен новый код для входа в Steam.\n"
        "{guard_destination}\n\n"
        "Пожалуйста, отправьте новый 5-значный проверочный код сообщением в этот чат:"
    ),
    "guard_push": (
        "📱 Требуется подтверждение входа в Steam!\n\n"
        "1️⃣ Зайдите в мобильное приложение Steam и откройте раздел «Steam Guard».\n"
        "2️⃣ Вам будет предложен выбор для подтверждения входа:\n"
        "• Сначала выберите: «Браузер»\n"
        "• Затем выберите гео-метку со страной: {country_name}\n\n"
        "💬 Либо вы можете просто отправить 5-значный проверочный код Steam Guard из мобильного приложения в этот чат!"
    ),
    "guard_received": (
        "✅ Код Steam Guard принят!\n\n"
        "⏳ Продолжаем вход в аккаунт и смену региона на {country_name}..."
    ),
    "cart_ready": (
        "✅ Вход в аккаунт Steam ({login}) выполнен!\n\n"
        "🎮 Дешёвая игра «{game_title}» ({game_price}) добавлена в корзину для региона {country_name}.\n"
        "⏳ Продавец уведомлён и скоро завершит смену региона."
    ),
    "already_region": (
        "ℹ️ На вашем аккаунте Steam ({login}) уже установлен регион {country_name}.\n\n"
        "Пожалуйста, свяжитесь с продавцом в этом чате."
    ),
    "success": (
        "🎉 Регион был успешно изменен на вашем аккаунте!\n"
        "⚠️Рекомендую использовать VPN нового региона первые 3 дня, чтобы уменьшить риск блокировки аккаунта.\n"
        "Обязательно ознакомьтесь с инструкцией по мерам предосторожности после смены региона:\n"
        "🔗 https://teletype.in/@tinechelovec/steam-switch\n\n"
        "🎮 Куплена игра: {game_title}\n\n"
        "Последние 4 цифры карты, которая использовалась для покупки - ✏️ {card_brand} *{card_last4}\n"
        "Они могут в дальнейшем понадобиться для поддержки Steam, если вы утеряете доступ к своему аккаунту📍\n\n"
        "Спасибо за покупку, не забудьте подтвердить заказ в разделе \"Покупки\" и оставить отзыв❤️\n\n"
        "Желаем Вам хорошего дня!✈️"
    ),
    "no_proxies_refund": (
        "⚠️ Временная недоступность прокси\n\n"
        "К сожалению, для целевого региона {country_name} временно закончились рабочие прокси в пуле.\n\n"
        "💸 Оформлен автоматический возврат средств за ваш заказ #{order_id}.\n"
        "Приносим извинения за неудобства!"
    ),
    "no_proxies_no_refund": (
        "⚠️ Временная недоступность прокси\n\n"
        "К сожалению, для целевого региона {country_name} временно закончились рабочие прокси в пуле.\n\n"
        "Пожалуйста, ожидайте ответа продавца в этом чате."
    ),
    "region_cooldown_refund": (
        "⚠️ Ограничение Steam (3 месяца)\n\n"
        "На вашем аккаунте Steam ({login}) регион уже менялся менее 3 месяцев назад — Steam временно не позволяет сменить его повторно.\n\n"
        "💸 Оформлен автоматический возврат средств за ваш заказ #{order_id}."
    ),
    "region_cooldown_no_refund": (
        "⚠️ Ограничение Steam (3 месяца)\n\n"
        "На вашем аккаунте Steam ({login}) регион уже менялся менее 3 месяцев назад — Steam временно не позволяет сменить его повторно.\n\n"
        "Пожалуйста, ожидайте ответа продавца в этом чате."
    ),
    "error_refund": (
        "❌ Не удалось выполнить операцию Steam ({reason})\n\n"
        "💸 Мы оформили автоматический возврат средств за ваш заказ #{order_id}.\n"
        "Деньги уже возвращены на ваш баланс FunPay."
    ),
    "error_no_refund": (
        "❌ Не удалось выполнить операцию Steam ({reason})\n\n"
        "Пожалуйста, ожидайте ответа продавца в этом чате."
    ),
    "reminder_login": (
        "👋 Здравствуйте, {buyer_username}!\n\n"
        "Напоминаем: для выполнения заказа #{order_id} по смене региона Steam на {country_name} отправьте логин и пароль от аккаунта Steam в этот чат.\n\n"
        "💬 Можно отправить сразу в формате: логин:пароль"
    ),
    "reminder_password": (
        "🔑 Напоминание по заказу #{order_id}!\n\n"
        "Мы всё ещё ожидаем пароль от вашего аккаунта Steam ({login}).\n"
        "Пожалуйста, отправьте пароль в этот чат для продолжения смены региона на {country_name}."
    ),
    "reminder_guard": (
        "🔐 Напоминание по заказу #{order_id}!\n\n"
        "Для завершения авторизации в Steam ({login}) требуется проверочный код Steam Guard.\n"
        "Пожалуйста, проверьте мобильное приложение Steam или почту и отправьте проверочный код в этот чат."
    ),
    "welcome_qr_choice": (
        "🎉 Оплата прошла успешно, спасибо за доверие!\n\n"
        "Для быстрой и безопасной смены региона выберите способ авторизации:\n"
        "1️⃣ По Логину и Паролю — напишите цифру 1\n"
        "2️⃣ По QR-коду (без ввода данных) — напишите цифру 2\n\n"
        "ℹ️ Если что-то пошло не так — напишите «заново» или «-», чтобы начать сначала."
    ),
    "welcome_qr_only": (
        "Вы выбрали авторизацию по QR-коду. 📷\n\n"
        "⚠️ Напишите + в чат, когда будете готовы открыть мобильное приложение Steam (Steam Guard → Сканер) и отсканировать код."
    ),
}

_LEGACY_BUYER_MESSAGE_DEFAULTS: dict[str, str] = {
    "guard_push": (
        "📱 Требуется подтверждение входа!\n\n"
        "Пожалуйста, откройте мобильное приложение Steam на телефоне и подтвердите попытку входа (нажмите «Подтвердить» в уведомлении Steam Guard).\n\n"
        "⏳ Бот ожидает подтверждения входа..."
    ),
    "reminder_login": (
        "👋 Здравствуйте, {buyer_username}!\n\n"
        "Напоминаем: для выполнения заказа #{order_id} по смене региона Steam на {country_name}, пожалуйста, выберите способ авторизации:\n"
        "1 — по логину и паролю\n"
        "2 — по QR-коду (или напишите «+» когда будете готовы)"
    ),
    "qr_confirmed": (
        "✅ Вход по QR подтверждён!\n\n"
        "⏳ Продолжаем вход в аккаунт и смену региона на {country_name}..."
    ),
    "success": (
        "🎉 Регион Steam успешно изменён!\n\n"
        "• Новый регион: {country_name}\n"
        "• Аккаунт: {login}\n\n"
        "Спасибо за покупку! Пожалуйста, подтвердите выполнение заказа на FunPay и оставьте отзыв ⭐⭐⭐⭐⭐"
    ),
}

BUYER_MESSAGE_LABELS: dict[str, str] = {
    "welcome_menu": "🎉 Выбор способа входа (1 или 2)",
    "welcome": "🔹 Шаг 1: Запрос логина (или по цифре 1)",
    "ask_password": "🔑 Шаг 2: Запрос пароля",
    "qr_ready_prompt": "📷 Готовность к QR (запрос +)",
    "welcome_qr": "🛑 Инструкция к QR-коду (с фото)",
    "qr_timeout": "⏳ Тайм-аут QR-кода",
    "qr_confirmed": "✅ Вход подтверждён (старт смены)",
    "checkout_started": "⏳ Проведение транзакции",
    "data_received": "⏳ Данные приняты (старт смены)",
    "guard_request": "🔐 Запрос Steam Guard",
    "guard_retry": "⚠️ Повторный запрос Guard",
    "guard_push": "📱 Запрос подтверждения Push",
    "guard_received": "✅ Код Guard принят",
    "cart_ready": "🛒 Игра добавлена в корзину",
    "already_region": "ℹ️ Регион уже установлен",
    "success": "🎉 Успешная смена региона",
    "reminder_login": "🔔 Напоминание при молчании",
    "reminder_password": "🔑 Напоминание пароля",
    "reminder_guard": "🔐 Напоминание кода Guard",
    "no_proxies_refund": "⚠️ Закончились прокси (автовозврат)",
    "no_proxies_no_refund": "⚠️ Закончились прокси (без автовозврата)",
    "region_cooldown_refund": "⏳ КД региона 3 мес. (автовозврат)",
    "region_cooldown_no_refund": "⏳ КД региона 3 мес. (без автовозврата)",
    "error_refund": "❌ Ошибка операции (автовозврат)",
    "error_no_refund": "❌ Ошибка операции (без автовозврата)",
}

BUYER_MESSAGE_HINTS: dict[str, str] = {
    "welcome_menu": "Переменные: <code>{buyer_username}</code>, <code>{country_name}</code>, <code>{order_id}</code>",
    "welcome": "Переменные: <code>{buyer_username}</code>, <code>{country_name}</code>, <code>{order_id}</code>",
    "qr_ready_prompt": "Переменные: <code>{buyer_username}</code>, <code>{country_name}</code>, <code>{order_id}</code>",
    "welcome_qr_choice": "Переменные: <code>{buyer_username}</code>, <code>{country_name}</code>, <code>{order_id}</code>",
    "welcome_qr_only": "Переменные: <code>{buyer_username}</code>, <code>{country_name}</code>, <code>{order_id}</code>",
    "welcome_qr": "Переменные: <code>{country_name}</code>, <code>{order_id}</code>, <code>{challenge_url}</code>",
    "qr_timeout": "Переменные: <code>{buyer_username}</code>, <code>{country_name}</code>, <code>{order_id}</code>, <code>{login}</code>",
    "qr_confirmed": "Переменные: <code>{country_name}</code>, <code>{order_id}</code>, <code>{login}</code>",
    "checkout_started": "Переменные: <code>{country_name}</code>, <code>{order_id}</code>, <code>{login}</code>, <code>{game_title}</code>, <code>{game_price}</code>",
    "ask_password": "Переменные: <code>{login}</code>, <code>{country_name}</code>, <code>{order_id}</code>",
    "data_received": "Переменные: <code>{login}</code>, <code>{country_name}</code>, <code>{order_id}</code>",
    "guard_request": "Переменные: <code>{login}</code>, <code>{order_id}</code>, <code>{guard_destination}</code>",
    "guard_retry": "Переменные: <code>{login}</code>, <code>{order_id}</code>, <code>{guard_destination}</code>",
    "guard_push": "Переменные: <code>{login}</code>, <code>{order_id}</code>, <code>{country_name}</code>",
    "guard_received": "Переменные: <code>{country_name}</code>, <code>{login}</code>, <code>{order_id}</code>",
    "cart_ready": "Переменные: <code>{country_name}</code>, <code>{login}</code>, <code>{order_id}</code>, <code>{game_title}</code>, <code>{game_price}</code>",
    "already_region": "Переменные: <code>{country_name}</code>, <code>{login}</code>, <code>{order_id}</code>",
    "success": "Переменные: <code>{country_name}</code>, <code>{login}</code>, <code>{order_id}</code>, <code>{game_title}</code>, <code>{game_price}</code>, <code>{card_brand}</code>, <code>{card_last4}</code>, <code>{guide_url}</code>",
    "reminder_login": "Переменные: <code>{buyer_username}</code>, <code>{country_name}</code>, <code>{order_id}</code>",
    "reminder_password": "Переменные: <code>{login}</code>, <code>{country_name}</code>, <code>{order_id}</code>",
    "reminder_guard": "Переменные: <code>{login}</code>, <code>{country_name}</code>, <code>{order_id}</code>",
    "no_proxies_refund": "Переменные: <code>{order_id}</code>, <code>{country_name}</code>",
    "no_proxies_no_refund": "Переменные: <code>{order_id}</code>, <code>{country_name}</code>",
    "region_cooldown_refund": "Переменные: <code>{order_id}</code>, <code>{login}</code>, <code>{country_name}</code>",
    "region_cooldown_no_refund": "Переменные: <code>{order_id}</code>, <code>{login}</code>, <code>{country_name}</code>",
    "error_refund": "Переменные: <code>{order_id}</code>, <code>{reason}</code>",
    "error_no_refund": "Переменные: <code>{order_id}</code>, <code>{reason}</code>",
}

def _get_buyer_messages() -> dict[str, str]:
    cfg = _load_config()
    saved = cfg.get("buyer_messages")
    merged = dict(DEFAULT_BUYER_MESSAGES)
    if isinstance(saved, dict):
        for k, v in saved.items():
            if k in DEFAULT_BUYER_MESSAGES and v:
                saved_value = str(v)
                if saved_value == _LEGACY_BUYER_MESSAGE_DEFAULTS.get(k):
                    continue
                if "{guard_destination}" in DEFAULT_BUYER_MESSAGES[k] and "{guard_destination}" not in saved_value:
                    continue
                merged[k] = saved_value
    return merged
def _render_buyer_msg(key: str, **kwargs) -> str:
    msgs = _get_buyer_messages()
    tmpl = msgs.get(key, DEFAULT_BUYER_MESSAGES.get(key, ""))
    defaults = {
        "buyer_username": "покупатель",
        "login": "аккаунт",
        "country_name": "выбранную страну",
        "order_id": "",
        "reason": "ошибка операции",
        "guard_destination": "На ваш мобильный Guard или почту отправлен проверочный код.",
        "challenge_url": "",
        "game_title": "игра",
        "game_price": "",
        "game_url": "",
        "card_brand": "Карта",
        "card_last4": "****",
        "guide_url": POST_SWITCH_GUIDE_URL,
    }
    merged_kwargs = {**defaults, **kwargs}
    try:
        return tmpl.format(**merged_kwargs)
    except Exception:
        res = tmpl
        for k, v in merged_kwargs.items():
            res = res.replace(f"{{{k}}}", str(v))
        return res
DEFAULT_REFUND_REASONS: dict[str, bool] = {
    FailureReason.PROXY_EXHAUSTED: True,
    FailureReason.NO_CARDS: True,
    FailureReason.REGION_COOLDOWN: True,
    FailureReason.CARD_DECLINED: True,
    FailureReason.NO_CHEAP_GAME: False,
    FailureReason.CART_ADD_FAILED: False,
    FailureReason.CHECKOUT_FAILED: True,
    FailureReason.VERIFY_FAILED: False,
    FailureReason.WRONG_CREDENTIALS: False,
    FailureReason.WRONG_GUARD: False,
    FailureReason.STEAM_TEMP_ERROR: False,
    FailureReason.STEAM_RATE_LIMIT: False,
    FailureReason.QR_TIMEOUT: False,
    FailureReason.USER_TIMEOUT: False,
    FailureReason.UNKNOWN: True,
}

REFUND_REASON_LABELS: dict[str, str] = {
    FailureReason.PROXY_EXHAUSTED: "🌐 Нет рабочих прокси",
    FailureReason.NO_CARDS: "💳 Нет активной карты для региона",
    FailureReason.REGION_COOLDOWN: "⏳ КД региона Steam (3 месяца)",
    FailureReason.CARD_DECLINED: "🚫 Карта отклонена банком / Steam",
    FailureReason.NO_CHEAP_GAME: "💰 Не найдена дешёвая игра",
    FailureReason.CART_ADD_FAILED: "🛒 Ошибка добавления в корзину",
    FailureReason.CHECKOUT_FAILED: "🧾 Ошибка оплаты на checkout Steam",
    FailureReason.VERIFY_FAILED: "🔍 Регион не подтверждён после оплаты",
    FailureReason.WRONG_CREDENTIALS: "🔑 Неверный логин или пароль",
    FailureReason.WRONG_GUARD: "🛡 Неверный / просроченный Guard-код",
    FailureReason.STEAM_TEMP_ERROR: "☁️ Временная ошибка Steam (5xx)",
    FailureReason.STEAM_RATE_LIMIT: "🚦 Лимит запросов Steam (429)",
    FailureReason.QR_TIMEOUT: "📷 Таймаут входа по QR-коду",
    FailureReason.USER_TIMEOUT: "⏱ Покупатель не отвечает",
    FailureReason.UNKNOWN: "❓ Непредвиденная ошибка (карта / прокси)",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "plugin_enabled": True,
    "instruction_read": False,
    "qr_login_mode": "choice",
    "login_by_qr": True,
    "auto_checkout_card": True,
    "auto_switch_card": True,
    "disable_failed_card": True,
    "disable_used_card": False,
    "auto_refund_enabled": True,
    "refund_reasons": dict(DEFAULT_REFUND_REASONS),
    "auto_deactivate_lots": True,
    "buyer_inactivity_reminders": True,
    "notify_tg_orders": True,
    "notify_tg_success": True,
    "notify_tg_errors": True,
    "notify_buyer_on_cart_ready": True,
    "clear_cart_before_add": True,
    "search_specials_only": True,
    "search_max_price_multiplier": 2.0,
    "custom_region_prices": {},
    "max_workers": 5,
    "request_timeout": 30.0,
    "connect_timeout": 15.0,
    "rate_limit_delay": 30,
    "lots": {},
    "buyer_messages": dict(DEFAULT_BUYER_MESSAGES),
}

def should_refund(reason: str, order_id: str = "") -> bool:
    if reason == FailureReason.PROXY_TIMEOUT:
        return should_refund(FailureReason.PROXY_EXHAUSTED, order_id=order_id)
    cfg = _load_config()
    master_enabled = bool(cfg.get("auto_refund_enabled", True))
    if not master_enabled:
        _log_event("refund_skipped", order_id=order_id, reason=reason, enabled=False, master_disabled=True)
        return False
    refund_reasons = cfg.get("refund_reasons")
    if not isinstance(refund_reasons, dict):
        refund_reasons = DEFAULT_REFUND_REASONS
    reason_enabled = bool(refund_reasons.get(reason, DEFAULT_REFUND_REASONS.get(reason, False)))
    _log_event("refund_decision", order_id=order_id, reason=reason, enabled=reason_enabled)
    if not reason_enabled:
        _log_event("refund_skipped", order_id=order_id, reason=reason, enabled=False)
    return reason_enabled

def _get_qr_mode(cfg: dict[str, Any]) -> str:
    m = cfg.get("qr_login_mode")
    if m in ("choice", "qr_only", "off"):
        return m
    if cfg.get("login_by_qr") is False:
        return "off"
    return "choice"

def _qr_mode_label(mode: str) -> str:
    if mode == "choice":
        return "🔄 QR или данные"
    elif mode == "qr_only":
        return "📱 Только QR-код"
    else:
        return "🔴 Только данные (логин/пароль)"

def _get_lots_dict(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = cfg.get("lots")
    res: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                raw_cards = v.get("cards") or []
                norm_cards = [c for c in (normalize_card_entry(x, default_country=str(v.get("country", "KZ")).upper()) for x in raw_cards) if c]
                res[str(k)] = {
                    "id": str(v.get("id", k)),
                    "title": str(v.get("title", f"Лот {k}")),
                    "enabled": bool(v.get("enabled", True)),
                    "country": str(v.get("country", "KZ")).upper(),
                    "proxies": list(v.get("proxies") or []),
                    "cards": norm_cards,
                }
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                lid = str(item.get("id", ""))
                if lid:
                    raw_cards = item.get("cards") or []
                    norm_cards = [c for c in (normalize_card_entry(x, default_country=str(item.get("country", "KZ")).upper()) for x in raw_cards) if c]
                    res[lid] = {
                        "id": lid,
                        "title": str(item.get("title", f"Лот {lid}")),
                        "enabled": bool(item.get("enabled", True)),
                        "country": str(item.get("country", "KZ")).upper(),
                        "proxies": list(item.get("proxies") or []),
                        "cards": norm_cards,
                    }
            elif isinstance(item, (int, str)):
                lid = str(item)
                res[lid] = {
                    "id": lid,
                    "title": f"Лот {lid}",
                    "enabled": True,
                    "country": "KZ",
                    "proxies": [],
                    "cards": [],
                }
    return res
DEFAULT_STATS: dict[str, Any] = {
    "total_operations": 0,
    "success": 0,
    "carts_prepared": 0,
    "failed": 0,
    "last_operation": "нет данных",
}

def _init_storage():
    os.makedirs(STORAGE_DIR, exist_ok=True)
    os.makedirs(MAFILES_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    if not os.path.exists(PROXIES_FILE):
        with open(PROXIES_FILE, "w", encoding="utf-8") as f:
            f.write("# Список прокси (каждый с новой строки):\n# ip:port\n# ip:port:user:pass\n# http://user:pass@ip:port\n")
    if not os.path.exists(PROXIES_BY_COUNTRY_FILE):
        with open(PROXIES_BY_COUNTRY_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2, ensure_ascii=False)
    if not os.path.exists(CARDS_FILE):
        with open(CARDS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_STATS, f, indent=2, ensure_ascii=False)
    _load_processed_orders()
def _load_config() -> dict[str, Any]:
    with _config_lock:
        if not os.path.exists(CONFIG_FILE):
            return dict(DEFAULT_CONFIG)
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = dict(DEFAULT_CONFIG)
            if isinstance(data, dict):
                cfg.update(data)
            if "refund_reasons" not in cfg or not isinstance(cfg["refund_reasons"], dict):
                cfg["refund_reasons"] = dict(DEFAULT_REFUND_REASONS)
            else:
                merged_reasons = dict(DEFAULT_REFUND_REASONS)
                for k in DEFAULT_REFUND_REASONS:
                    if k in cfg["refund_reasons"]:
                        merged_reasons[k] = bool(cfg["refund_reasons"][k])
                cfg["refund_reasons"] = merged_reasons
            return cfg
        except Exception as e:
            logger.error(f"[{NAME}] Ошибка чтения {CONFIG_FILE}: {e}")
            return dict(DEFAULT_CONFIG)
def _save_config(cfg: dict[str, Any]) -> None:
    with _config_lock:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[{NAME}] Ошибка сохранения {CONFIG_FILE}: {e}")
_stats_lock = threading.Lock()
_cards_lock = threading.Lock()

def _load_stats() -> dict[str, Any]:
    with _stats_lock:
        if not os.path.exists(STATS_FILE):
            return dict(DEFAULT_STATS)
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            res = dict(DEFAULT_STATS)
            if isinstance(data, dict):
                res.update(data)
            return res
        except Exception as e:
            logger.warning(f"[{NAME}] Ошибка чтения {STATS_FILE}: {e}")
            return dict(DEFAULT_STATS)
def _save_stats(st: dict[str, Any]) -> None:
    with _stats_lock:
        try:
            with open(STATS_FILE, "w", encoding="utf-8") as f:
                json.dump(st, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[{NAME}] Ошибка сохранения {STATS_FILE}: {e}")
_proxies_lock = threading.Lock()

def _load_proxies_by_country() -> dict[str, list[str]]:
    with _proxies_lock:
        by_cc: dict[str, list[str]] = {}
        if os.path.exists(PROXIES_BY_COUNTRY_FILE):
            try:
                with open(PROXIES_BY_COUNTRY_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for k, vals in raw.items():
                        cc = str(k or "ANY").strip().upper()
                        if cc != "ANY" and len(cc) != 2:
                            cc = "ANY"
                        if isinstance(vals, list):
                            cleaned = list(dict.fromkeys(p for p in (normalize_proxy_url(x) for x in vals) if p))
                            if cleaned:
                                by_cc[cc] = cleaned
            except Exception as e:
                logger.error(f"[{NAME}] Ошибка чтения {PROXIES_BY_COUNTRY_FILE}: {e}")
        if os.path.exists(PROXIES_FILE):
            try:
                with open(PROXIES_FILE, "r", encoding="utf-8") as f:
                    legacy = parse_proxies(f.read())
                known = {p for lst in by_cc.values() for p in lst}
                unassigned = [p for p in legacy if p not in known]
                if unassigned:
                    by_cc["ANY"] = list(dict.fromkeys((by_cc.get("ANY") or []) + unassigned))
            except Exception as e:
                logger.error(f"[{NAME}] Ошибка чтения {PROXIES_FILE}: {e}")
        return by_cc

def _save_proxies_by_country(by_cc: dict[str, list[str]]) -> None:
    with _proxies_lock:
        clean_map: dict[str, list[str]] = {}
        flat: list[str] = []
        seen: set[str] = set()
        for k, vals in (by_cc or {}).items():
            cc = str(k or "ANY").strip().upper()
            if cc != "ANY" and len(cc) != 2:
                cc = "ANY"
            lst = list(dict.fromkeys(p for p in (normalize_proxy_url(x) for x in (vals or [])) if p))
            if lst:
                clean_map[cc] = lst
                for p in lst:
                    if p not in seen:
                        seen.add(p)
                        flat.append(p)
        try:
            with open(PROXIES_BY_COUNTRY_FILE, "w", encoding="utf-8") as f:
                json.dump(clean_map, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[{NAME}] Ошибка сохранения {PROXIES_BY_COUNTRY_FILE}: {e}")
        try:
            with open(PROXIES_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(flat) + ("\n" if flat else ""))
        except Exception as e:
            logger.error(f"[{NAME}] Ошибка сохранения {PROXIES_FILE}: {e}")

def _add_proxies_for_country(country_code: str, new_proxies: list[str]) -> tuple[int, int]:
    cc = str(country_code or "ANY").strip().upper()
    if cc != "ANY" and len(cc) != 2:
        cc = "ANY"
    by_cc = _load_proxies_by_country()
    existing = list(by_cc.get(cc) or [])
    combined = list(dict.fromkeys(existing + list(new_proxies or [])))
    added = len(combined) - len(existing)
    by_cc[cc] = combined
    _save_proxies_by_country(by_cc)
    return added, len(combined)

def _get_proxies_for_country(country_code: str, include_any: bool = False) -> list[str]:
    cc = str(country_code or "ANY").strip().upper()
    is_eu = cc in EUROZONE_COUNTRIES
    by_cc = _load_proxies_by_country()
    res: list[str] = []
    seen: set[str] = set()
    for k, vals in by_cc.items():
        if k == cc or (is_eu and k in EUROZONE_COUNTRIES):
            for p in vals:
                if p not in seen:
                    seen.add(p)
                    res.append(p)
    if include_any:
        for p in by_cc.get("ANY") or []:
            if p not in seen:
                seen.add(p)
                res.append(p)
    return res

def _get_cards_for_country(country_code: str, include_any: bool = False) -> list[dict[str, Any]]:
    cc = str(country_code or "ANY").strip().upper()
    is_eu = cc in EUROZONE_COUNTRIES
    all_cards = _load_cards()
    res: list[dict[str, Any]] = []
    for c in all_cards:
        c_cc = str(c.get("country", "ANY")).upper()
        if c_cc == cc or (is_eu and c_cc in EUROZONE_COUNTRIES):
            res.append(c)
    if include_any:
        for c in all_cards:
            if str(c.get("country", "ANY")).upper() == "ANY" and c not in res:
                res.append(c)
    return res

def _load_proxies() -> list[str]:
    by_cc = _load_proxies_by_country()
    flat: list[str] = []
    seen: set[str] = set()
    for vals in by_cc.values():
        for p in vals:
            if p not in seen:
                seen.add(p)
                flat.append(p)
    return flat

def _save_proxies(proxies: list[str]) -> None:
    if not proxies:
        _save_proxies_by_country({})
        return
    by_cc = _load_proxies_by_country()
    keep_set = set(proxies)
    new_map: dict[str, list[str]] = {}
    assigned: set[str] = set()
    for cc, vals in by_cc.items():
        kept = [p for p in vals if p in keep_set]
        if kept:
            new_map[cc] = kept
            assigned.update(kept)
    rest = [p for p in proxies if p not in assigned]
    if rest:
        new_map["ANY"] = list(dict.fromkeys((new_map.get("ANY") or []) + rest))
    _save_proxies_by_country(new_map)
def _load_cards() -> list[dict[str, Any]]:
    with _cards_lock:
        if not os.path.exists(CARDS_FILE):
            return []
        try:
            with open(CARDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            cards: list[dict[str, Any]] = []
            for item in data:
                entry = normalize_card_entry(item)
                if entry:
                    cards.append(entry)
            return cards
        except Exception as e:
            logger.error(f"[{NAME}] Ошибка чтения {CARDS_FILE}: {e}")
            return []
def _save_cards(cards: list[dict[str, Any]]) -> None:
    with _cards_lock:
        try:
            clean = [c for c in (normalize_card_entry(x) for x in (cards or [])) if c]
            with open(CARDS_FILE, "w", encoding="utf-8") as f:
                json.dump(clean, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[{NAME}] Ошибка сохранения {CARDS_FILE}: {e}")
def _merge_cards(existing: list[dict[str, Any]], new_cards: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged = list(existing)
    seen = {(c["number"], c["exp_month"], c["exp_year"], c["cvv"]) for c in merged}
    added = 0
    for c in new_cards:
        key = (c["number"], c["exp_month"], c["exp_year"], c["cvv"])
        if key not in seen:
            seen.add(key)
            merged.append(c)
            added += 1
    return merged, added
def _is_card_usable(card: dict[str, Any]) -> bool:
    if not card or not card.get("enabled", True):
        return False
    max_uses = int(card.get("max_uses", 0) or 0)
    used = int(card.get("used_count", 0) or 0)
    if max_uses > 0 and used >= max_uses:
        return False
    return True
def _get_candidate_cards_for_order(lot_cfg: dict[str, Any] | None, country_code: str) -> list[tuple[dict[str, Any], str]]:
    cc = str(country_code or "US").strip().upper()
    is_eu = cc in EUROZONE_COUNTRIES
    cfg = _load_config()
    auto_switch = bool(cfg.get("auto_switch_card", True))
    seen_ids: set[str] = set()

    def _sort_group(grp: list[tuple[dict[str, Any], str]]) -> list[tuple[dict[str, Any], str]]:
        if not auto_switch:
            return grp
        return sorted(
            grp,
            key=lambda pair: (
                int(pair[0].get("fail_count", 0) or 0),
                int(pair[0].get("used_count", 0) or 0),
            ),
        )

    lot_grp: list[tuple[dict[str, Any], str]] = []
    if isinstance(lot_cfg, dict):
        lot_cards = lot_cfg.get("cards") or []
        for raw in lot_cards:
            c = normalize_card_entry(raw, default_country=cc)
            if c and _is_card_usable(c) and c["id"] not in seen_ids:
                seen_ids.add(c["id"])
                lot_grp.append((c, "lot"))

    global_cards = _load_cards()
    cc_grp: list[tuple[dict[str, Any], str]] = []
    for c in global_cards:
        if not _is_card_usable(c) or c["id"] in seen_ids:
            continue
        card_cc = str(c.get("country", "ANY")).upper()
        if card_cc == cc or (is_eu and card_cc in EUROZONE_COUNTRIES):
            seen_ids.add(c["id"])
            cc_grp.append((c, "global_country"))

    any_grp: list[tuple[dict[str, Any], str]] = []
    for c in global_cards:
        if not _is_card_usable(c) or c["id"] in seen_ids:
            continue
        if str(c.get("country", "ANY")).upper() == "ANY":
            seen_ids.add(c["id"])
            any_grp.append((c, "global_any"))

    return _sort_group(lot_grp) + _sort_group(cc_grp) + _sort_group(any_grp)

def _select_card_for_order(lot_cfg: dict[str, Any] | None, country_code: str) -> tuple[dict[str, Any] | None, str]:
    cands = _get_candidate_cards_for_order(lot_cfg, country_code)
    if cands:
        return cands[0]
    return None, "none"

def _increment_card_usage(card_id: str, card_source: str, lot_id: str | None = None) -> None:
    if not card_id:
        return
    def _apply_limit(cards_list: list[dict[str, Any]], disable_used: bool) -> tuple[list[dict[str, Any]], bool]:
        changed = False
        new_list: list[dict[str, Any]] = []
        for c in cards_list:
            if c.get("id") != card_id:
                new_list.append(c)
                continue
            changed = True
            new_used = int(c.get("used_count", 0) or 0) + 1
            c["used_count"] = new_used
            max_u = int(c.get("max_uses", 0) or 0)
            lim_act = str(c.get("limit_action") or ("deactivate" if max_u > 0 else "none")).lower()
            if max_u > 0 and new_used >= max_u:
                if lim_act == "delete":
                    logger.info(f"[{NAME}] Карта {card_id} достигла лимита ({new_used}/{max_u}) — удалена из плагина.")
                    continue
                elif lim_act == "deactivate":
                    c["enabled"] = False
                    logger.info(f"[{NAME}] Карта {card_id} достигла лимита ({new_used}/{max_u}) — переведена в неактивный пул.")
            if disable_used:
                c["enabled"] = False
            new_list.append(c)
        return new_list, changed

    try:
        cfg = _load_config()
        disable_used = bool(cfg.get("disable_used_card", False))
        if card_source == "lot" and lot_id:
            lots = _get_lots_dict(cfg)
            lid = str(lot_id)
            if lid in lots:
                updated_lot_cards, changed = _apply_limit(list(lots[lid].get("cards") or []), disable_used)
                if changed:
                    lots[lid]["cards"] = updated_lot_cards
                    cfg["lots"] = lots
                    _save_config(cfg)
        cards = _load_cards()
        updated_cards, changed = _apply_limit(cards, disable_used)
        if changed:
            _save_cards(updated_cards)
    except Exception as e:
        logger.debug(f"[{NAME}] _increment_card_usage error: {e}")

def _mark_card_failed(card_id: str, card_source: str, lot_id: str | None = None, error_text: str = "") -> None:
    if not card_id:
        return
    try:
        cfg = _load_config()
        disable_failed = bool(cfg.get("disable_failed_card", True))
        err_short = str(error_text or "ошибка оплаты")[:120]
        if card_source == "lot" and lot_id:
            lots = _get_lots_dict(cfg)
            lid = str(lot_id)
            if lid in lots:
                updated = False
                for c in lots[lid].get("cards") or []:
                    if c.get("id") == card_id:
                        c["fail_count"] = int(c.get("fail_count", 0) or 0) + 1
                        c["last_error"] = err_short
                        if disable_failed:
                            c["enabled"] = False
                        updated = True
                        break
                if updated:
                    cfg["lots"] = lots
                    _save_config(cfg)
        cards = _load_cards()
        updated = False
        for c in cards:
            if c.get("id") == card_id:
                c["fail_count"] = int(c.get("fail_count", 0) or 0) + 1
                c["last_error"] = err_short
                if disable_failed:
                    c["enabled"] = False
                updated = True
                break
        if updated:
            _save_cards(cards)
    except Exception as e:
        logger.debug(f"[{NAME}] _mark_card_failed error: {e}")
def _list_mafiles() -> list[str]:
    if not os.path.exists(MAFILES_DIR):
        return []
    try:
        return [f for f in os.listdir(MAFILES_DIR) if f.endswith(".maFile")]
    except Exception:
        return []
def _country_display(code: str) -> str:
    code = (code or "").upper()
    return COUNTRY_NAMES.get(code, f"🌐 {code}")
def _is_authorized(user_id) -> bool:
    if user_id is None:
        return False
    try:
        auth = getattr(getattr(cardinal_instance, 'telegram', None), 'authorized_users', None)
        if isinstance(auth, dict) and auth:
            return user_id in auth or int(user_id) in auth or str(user_id) in auth
    except Exception as e:
        logger.warning(f"[{NAME}] Ошибка проверки авторизации TG: {e}")
    return False
def _tg_send(chat_id, text: str, reply_markup=None) -> None:
    if not bot_instance or not chat_id:
        return
    try:
        bot_instance.send_message(int(chat_id), text, parse_mode='HTML', reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"[{NAME}] TG send error: {e}")
def _tg_edit(chat_id, message_id, text: str, reply_markup=None) -> None:
    if not bot_instance or not chat_id or not message_id:
        return
    try:
        bot_instance.edit_message_text(text, int(chat_id), int(message_id), parse_mode='HTML', reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception:
        _tg_send(chat_id, text, reply_markup)
def _notify_tg(text: str, ntype: str = "order") -> None:
    cfg = _load_config()
    if ntype == "order" and not cfg.get("notify_tg_orders", True):
        return
    if ntype == "success" and not cfg.get("notify_tg_success", True):
        return
    if ntype == "error" and not cfg.get("notify_tg_errors", True):
        return
    targets = []
    if cardinal_instance and getattr(cardinal_instance, 'telegram', None):
        auth = getattr(cardinal_instance.telegram, 'authorized_users', None)
        if isinstance(auth, dict) and auth:
            targets = list(auth.keys())
    if not targets and admin_chat_id:
        targets = [admin_chat_id]
    if not targets or not bot_instance:
        return
    for cid in targets:
        try:
            _tg_send(cid, text)
        except Exception as e:
            logger.debug(f"[{NAME}] TG notification failed: {e}")
def _make_kb(rows: list[list[tuple[str, str]]]):
    if not tg_types:
        return None
    kb = tg_types.InlineKeyboardMarkup()
    for row in rows:
        kb.row(*[tg_types.InlineKeyboardButton(text, callback_data=data) for text, data in row])
    return kb
def _mask_secret(val: str, prefix_len=3, suffix_len=3) -> str:
    if len(val) <= prefix_len + suffix_len:
        return "***"
    return f"{val[:prefix_len]}...{val[-suffix_len:]}"
def _about_text():
    return (
        f"🧩 <b>Плагин:</b> {NAME}\n"
        f"📦 <b>Версия:</b> {VERSION}\n"
        f"👤 <b>Автор:</b> <a href=\"{CREATOR_URL}\">{CREDITS}</a>\n\n"
        "Выберите раздел ниже."
    )
def _home_kb():
    return _make_kb([
        [("⚙️ Настройки", "src_settings_panel"), ("ℹ️ Информация", "src_info")],
        [("⬆️ Обновить плагин", "src_update_menu"), ("🗑 Удалить", "src_delete_ask")],
        [("🔙 К списку плагинов", CB_PLUGINS_LIST_OPEN)],
    ])
def _open_home(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    _tg_edit(chat_id, message_id, _about_text(), _home_kb()) if message_id else _tg_send(chat_id, _about_text(), _home_kb())
def _info_text():
    return (
        "ℹ️ <b>Информация</b>\n\n"
        f"Здесь находятся официальные ссылки {NAME}.\n\n"
        "• <b>Чат</b> - помощь и общение.\n"
        "• <b>Канал</b> - новости и обновления.\n"
        "• <b>Инструкция</b> - настройка и использование плагина.\n"
        "• <b>Мой Telegram</b> - связь с автором."
    )
def _info_kb():
    if not tg_types:
        return None
    kb = tg_types.InlineKeyboardMarkup()
    kb.row(tg_types.InlineKeyboardButton("💬 Чат", url=GROUP_URL), tg_types.InlineKeyboardButton("📢 Канал", url=CHANNEL_URL))
    kb.row(tg_types.InlineKeyboardButton("📖 Инструкция", url=INSTRUCTION_URL))
    kb.row(tg_types.InlineKeyboardButton("👤 Мой Telegram", url=CREATOR_URL))
    kb.add(tg_types.InlineKeyboardButton("◀️ Назад", callback_data="src_home"))
    return kb
def _open_info(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    _tg_edit(chat_id, message_id, _info_text(), _info_kb()) if message_id else _tg_send(chat_id, _info_text(), _info_kb())
def _update_menu_text():
    return (
        f"⬆️ <b>Обновление {NAME}</b>\n\n"
        f"Текущая версия: {VERSION}\n\n"
        "• <b>Обновить локально</b> — пришлите новый файл плагина <code>.py</code> в этот чат. Он будет проверен и установлен вместо текущего файла.\n"
        "• <b>Обновить онлайн</b> — проверить новую версию и скачать её с GitHub.\n\n"
        "Перед заменой автоматически создаётся резервная копия текущего плагина и настроек."
    )
def _update_menu_kb():
    return _make_kb([
        [("📥 Обновить локально", "src_update_local")],
        [("🌐 Обновить онлайн", "src_update_online")],
        [("◀️ Назад", "src_home")],
    ])
def _open_update_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    _tg_edit(chat_id, message_id, _update_menu_text(), _update_menu_kb()) if message_id else _tg_send(chat_id, _update_menu_text(), _update_menu_kb())
def _delete_confirm_text():
    return (
        "⚠️ <b>Удаление плагина</b>\n\n"
        f"Вы точно хотите удалить <b>{NAME}</b>?\n\n"
        "Будут удалены:\n"
        "• файлы плагина\n"
        "• настройки и логи\n\n"
        "<b>Действие необратимо.</b>\n"
        "После удаления выполните перезапуск: напишите команду <code>/restart</code>."
    )
def _delete_confirm_kb():
    return _make_kb([
        [("✅ Да, удалить", "src_delete_yes"), ("❌ Нет", "src_home")],
    ])
def _open_delete_confirm(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    _tg_edit(chat_id, message_id, _delete_confirm_text(), _delete_confirm_kb()) if message_id else _tg_send(chat_id, _delete_confirm_text(), _delete_confirm_kb())
def _settings_panel_text(chat_id):
    cfg = _load_config()
    plugin_state = "🟢 Включено" if cfg.get("plugin_enabled", True) else "🔴 Выключено"
    lots = _get_lots_dict(cfg)
    total_lots = len(lots)
    active_lots = sum(1 for l in lots.values() if l.get("enabled", False))
    proxies = _load_proxies()
    cards = _load_cards()
    active_cards = sum(1 for c in cards if _is_card_usable(c))
    return (
        "⚙️ <b>Панель настроек</b>\n\n"
        f"• Плагин: <b>{plugin_state}</b>\n"
        f"• Лоты: <b>🟢 Активных: {active_lots} / Всего: {total_lots} шт.</b>\n"
        f"• База прокси: <code>{len(proxies)}</code> шт.\n"
        f"• База карт: <code>{active_cards}/{len(cards)}</code> шт.\n\n"
        "Выберите раздел:"
    )
def _settings_panel_kb(chat_id):
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    rows = [
        [("⚙️ Настройки плагина", "src_mini_settings")],
        [(f"⭐ Настройка лотов · {len(lots)}", "src_lots_menu")],
        [("📊 Статистика", "src_stats")],
        [("◀️ Назад", "src_home")],
    ]
    return _make_kb(rows)
def _instruction_gate_text():
    return (
        "📖 <b>Прочитайте инструкцию перед использованием плагина!</b>\n\n"
        f"Перед началом работы с <b>{NAME}</b> обязательно ознакомьтесь с инструкцией "
        "(настройка карт, прокси по странам, цен и лотов):\n\n"
        f"🔗 <a href=\"{INSTRUCTION_URL}\">{INSTRUCTION_URL}</a>\n\n"
        "<i>После нажатия кнопки «✅ Прочитал» это уведомление больше не будет появляться.</i>"
    )
def _instruction_gate_kb():
    if not tg_types:
        return None
    kb = tg_types.InlineKeyboardMarkup()
    kb.row(tg_types.InlineKeyboardButton("📖 Открыть инструкцию", url=INSTRUCTION_URL))
    kb.row(tg_types.InlineKeyboardButton("✅ Прочитал", callback_data="src_instruction_confirm"))
    kb.row(tg_types.InlineKeyboardButton("◀️ Назад", callback_data="src_home"))
    return kb
def _open_instruction_gate(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _instruction_gate_text()
    kb = _instruction_gate_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _open_settings_panel(chat_id, message_id=None, check_intro: bool = False):
    _waiting.pop(chat_id, None)
    if check_intro:
        cfg = _load_config()
        if not cfg.get("instruction_read", False):
            _open_instruction_gate(chat_id, message_id)
            return
    text = _settings_panel_text(chat_id)
    kb = _settings_panel_kb(chat_id)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _mini_settings_text(chat_id):
    cfg = _load_config()
    enabled = cfg.get("plugin_enabled", True)
    auto_checkout = cfg.get("auto_checkout_card", True)
    auto_refund = cfg.get("auto_refund_enabled", True)
    auto_deact = cfg.get("auto_deactivate_lots", True)
    notif_on = any([
        cfg.get("notify_tg_orders", True),
        cfg.get("notify_tg_success", True),
        cfg.get("notify_tg_errors", True),
    ])
    workers = cfg.get("max_workers", 5)
    timeout = cfg.get("request_timeout", 30.0)
    proxies = _load_proxies()
    cards = _load_cards()
    lots = _get_lots_dict(cfg)
    return (
        f"⚙️ <b>Настройки плагина</b>\n\n"
        f"• Состояние: <b>{'🟢 включён' if enabled else '🔴 выключен'}</b>\n"
        f"• Автооплата картой: <b>{'🟢 ВКЛ' if auto_checkout else '🔴 ВЫКЛ (только корзина)'}</b>\n"
        f"• Автовозврат: <b>{'🟢 ВКЛ' if auto_refund else '🔴 ВЫКЛ'}</b>\n"
        f"• Автодеактивация лотов: <b>{'🟢 ВКЛ' if auto_deact else '🔴 ВЫКЛ'}</b>\n"
        f"• Уведомления Telegram: <b>{'🟢 ВКЛ' if notif_on else '🔴 ВЫКЛ'}</b>\n"
        f"• Одновременных смен: <b>{workers}</b>\n"
        f"• Таймаут запросов: <b>{int(timeout)} сек</b>\n"
        f"• База прокси: <b>{len(proxies)} шт.</b>\n"
        f"• База карт: <b>{len(cards)} шт.</b>\n"
        f"• Привязано лотов: <b>{len(lots)} шт.</b>\n\n"
        "Выберите категорию:"
    )
def _mini_settings_kb(chat_id):
    cfg = _load_config()
    enabled = cfg.get("plugin_enabled", True)
    rows = [
        [(f"🧩 Состояние плагина: {'🟢 ВКЛ' if enabled else '🔴 ВЫКЛ'}", "src_tgl_state")],
        [("🛒 Заказы", "src_cat_orders")],
        [("🔔 Уведомления", "src_cat_notif")],
        [("🗄 База данных", "src_db_menu")],
        [("💰 Настройка цен", "src_games_menu")],
        [("📦 Очередь и лимиты", "src_cat_queue")],
        [("🧰 Обслуживание", "src_cat_maint")],
        [("◀️ Назад", "src_settings_panel")],
    ]
    return _make_kb(rows)
def _open_mini_settings(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _mini_settings_text(chat_id)
    kb = _mini_settings_kb(chat_id)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _db_menu_text():
    proxies = _load_proxies()
    cards = _load_cards()
    active_cards = sum(1 for c in cards if _is_card_usable(c))
    return (
        "🗄 <b>База данных</b>\n\n"
        f"• 🌐 База прокси: <b>{len(proxies)} шт.</b>\n"
        f"• 💳 База карт: <b>{active_cards} активных / {len(cards)} всего</b>\n\n"
        "Выберите нужную базу:"
    )
def _db_menu_kb():
    proxies = _load_proxies()
    cards = _load_cards()
    rows = [
        [(f"🌐 База прокси · {len(proxies)} шт.", "src_proxies")],
        [(f"💳 База карт · {len(cards)} шт.", "src_cards")],
        [("◀️ Назад", "src_mini_settings")],
    ]
    return _make_kb(rows)
def _open_db_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _db_menu_text()
    kb = _db_menu_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _refunds_menu_text():
    cfg = _load_config()
    auto_refund = cfg.get("auto_refund_enabled", True)
    reasons = cfg.get("refund_reasons") or DEFAULT_REFUND_REASONS
    enabled_cnt = sum(1 for code in REFUND_REASON_LABELS if reasons.get(code, DEFAULT_REFUND_REASONS.get(code, False)))
    text = (
        "💸 <b>Причины автовозвратов</b>\n\n"
        f"• Общий переключатель: <b>{'🟢 ВКЛ' if auto_refund else '🔴 ВЫКЛ'}</b>\n"
        f"• Активных правил: <b>{enabled_cnt} из {len(REFUND_REASON_LABELS)}</b>\n"
        "<i>Если общий переключатель выключен — возврат не выполняется ни по одной причине.</i>\n\n"
        "<b>Нажмите на причину ниже, чтобы включить или отключить автовозврат:</b>"
    )
    return text
def _refunds_menu_kb():
    cfg = _load_config()
    auto_refund = cfg.get("auto_refund_enabled", True)
    reasons = cfg.get("refund_reasons") or DEFAULT_REFUND_REASONS
    rows = [
        [(f"💸 Автовозврат (общий): {'🟢 ВКЛ' if auto_refund else '🔴 ВЫКЛ'}", "src_tgl_refund_master")],
    ]
    for code, label in REFUND_REASON_LABELS.items():
        val = reasons.get(code, DEFAULT_REFUND_REASONS.get(code, False))
        mark = "✅" if val else "❌"
        rows.append([(f"{mark} {label}", f"src_tgl_ref_reason:{code}")])
    rows.append([("◀️ Назад", "src_cat_orders")])
    return _make_kb(rows)
def _open_refunds_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _refunds_menu_text()
    kb = _refunds_menu_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _notif_menu_text():
    cfg = _load_config()
    n_ord = cfg.get("notify_tg_orders", True)
    n_suc = cfg.get("notify_tg_success", True)
    n_err = cfg.get("notify_tg_errors", True)
    return (
        "🔔 <b>Уведомления в Telegram</b>\n\n"
        "Выберите типы событий, которые бот будет присылать вам:\n\n"
        f"• 🛒 <b>Новые заказы и вход:</b> {'🟢 ВКЛ' if n_ord else '🔴 ВЫКЛ'}\n"
        "  <i>Новый заказ, получение логина/пароля, тайм-ауты ожидания</i>\n\n"
        f"• ✅ <b>Успешная смена / корзина:</b> {'🟢 ВКЛ' if n_suc else '🔴 ВЫКЛ'}\n"
        "  <i>Сводка об оплаченной игре, карте и подтверждении нового региона</i>\n\n"
        f"• ⚠️ <b>Ошибки и автовозвраты:</b> {'🟢 ВКЛ' if n_err else '🔴 ВЫКЛ'}\n"
        "  <i>Отклонённая карта, КД 3 месяца, прокси, ошибки Steam и статус возврата</i>"
    )
def _notif_menu_kb():
    cfg = _load_config()
    n_ord = cfg.get("notify_tg_orders", True)
    n_suc = cfg.get("notify_tg_success", True)
    n_err = cfg.get("notify_tg_errors", True)
    rows = [
        [(f"🛒 Новые заказы и вход: {'🟢 ВКЛ' if n_ord else '🔴 ВЫКЛ'}", "src_tgl_notif_orders")],
        [(f"✅ Успешная смена / корзина: {'🟢 ВКЛ' if n_suc else '🔴 ВЫКЛ'}", "src_tgl_notif_success")],
        [(f"⚠️ Ошибки и автовозвраты: {'🟢 ВКЛ' if n_err else '🔴 ВЫКЛ'}", "src_tgl_notif_errors")],
        [("◀️ Назад", "src_mini_settings")],
    ]
    return _make_kb(rows)
def _open_notif_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _notif_menu_text()
    kb = _notif_menu_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _orders_menu_text():
    cfg = _load_config()
    qr_mode = _get_qr_mode(cfg)
    auto_checkout = cfg.get("auto_checkout_card", True)
    auto_switch = cfg.get("auto_switch_card", True)
    auto_refund = cfg.get("auto_refund_enabled", True)
    auto_deact = cfg.get("auto_deactivate_lots", True)
    remind_inact = cfg.get("buyer_inactivity_reminders", True)
    clear_cart = cfg.get("clear_cart_before_add", True)
    notify_buyer_cart = cfg.get("notify_buyer_on_cart_ready", True)
    return (
        "🛒 <b>Настройки заказов</b>\n\n"
        f"• Режим входа в заказах: <b>{_qr_mode_label(qr_mode)}</b>\n"
        "  <i>(🔄 QR или данные · 📱 Только QR · 🔴 Только данные)</i>\n\n"
        f"• Автооплата картой (смена региона): <b>{'🟢 ВКЛ' if auto_checkout else '🔴 ВЫКЛ'}</b>\n"
        f"• Автосмена карт при проблеме/использовании: <b>{'🟢 ВКЛ' if auto_switch else '🔴 ВЫКЛ'}</b>\n\n"
        f"• Автовозврат: <b>{'🟢 ВКЛ' if auto_refund else '🔴 ВЫКЛ'}</b>\n"
        f"• Напоминания при молчании: <b>{'🟢 ВКЛ' if remind_inact else '🔴 ВЫКЛ'}</b>\n"
        f"• Очищать корзину перед добавлением: <b>{'🟢 ВКЛ' if clear_cart else '🔴 ВЫКЛ'}</b>\n"
        f"• Сообщать покупателю о корзине: <b>{'🟢 ВКЛ' if notify_buyer_cart else '🔴 ВЫКЛ'}</b>\n"
        f"• Автодеактивация лотов: <b>{'🟢 ВКЛ' if auto_deact else '🔴 ВЫКЛ'}</b>\n\n"
        "Выберите параметр для переключения:"
    )
def _orders_menu_kb():
    cfg = _load_config()
    qr_mode = _get_qr_mode(cfg)
    auto_checkout = cfg.get("auto_checkout_card", True)
    auto_refund = cfg.get("auto_refund_enabled", True)
    auto_deact = cfg.get("auto_deactivate_lots", True)
    remind_inact = cfg.get("buyer_inactivity_reminders", True)
    clear_cart = cfg.get("clear_cart_before_add", True)
    notify_buyer_cart = cfg.get("notify_buyer_on_cart_ready", True)
    rows = [
        [(f"📱 Режим входа: {_qr_mode_label(qr_mode)}", "src_cycle_qr_mode")],
        [(f"💳 Автооплата картой: {'🟢 ВКЛ' if auto_checkout else '🔴 ВЫКЛ'}", "src_tgl_auto_checkout")],
        [("🔄 Автосмена карт (правила)", "src_card_rules_menu")],
        [(f"💸 Автовозврат: {'🟢 ВКЛ' if auto_refund else '🔴 ВЫКЛ'}", "src_tgl_refund"), ("⚙️ Причины", "src_cat_refunds")],
        [(f"🔔 Напоминания при молчании: {'🟢 ВКЛ' if remind_inact else '🔴 ВЫКЛ'}", "src_tgl_inact_remind")],
        [(f"🧹 Очищать корзину: {'🟢 ВКЛ' if clear_cart else '🔴 ВЫКЛ'}", "src_tgl_clear_cart")],
        [(f"💬 Писать покупателю (корзина): {'🟢 ВКЛ' if notify_buyer_cart else '🔴 ВЫКЛ'}", "src_tgl_notify_buyer_cart")],
        [(f"🛑 Автодеактивация лотов: {'🟢 ВКЛ' if auto_deact else '🔴 ВЫКЛ'}", "src_tgl_deact")],
        [("💬 Шаблоны сообщений", "src_cat_messages")],
        [("◀️ Назад", "src_mini_settings")],
    ]
    return _make_kb(rows)
def _open_orders_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _orders_menu_text()
    kb = _orders_menu_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _card_rules_menu_text():
    cfg = _load_config()
    sw = cfg.get("auto_switch_card", True)
    df = cfg.get("disable_failed_card", True)
    du = cfg.get("disable_used_card", False)
    return (
        "🔄 <b>Автосмена и ротация карт</b>\n\n"
        f"• <b>Автосмена карты:</b> {'🟢 ВКЛ' if sw else '🔴 ВЫКЛ'}\n"
        "  <i>При ошибке/отказе карты бот автоматически пробует следующую карту из базы. Для новых заказов сначала выбираются свежие (неиспользованные) карты.</i>\n\n"
        f"• <b>Отключать проблемную карту:</b> {'🟢 ВКЛ' if df else '🔴 ВЫКЛ'}\n"
        "  <i>Если банк или Steam отклонил карту — она автоматически выключается в базе, чтобы не тормозить следующие заказы.</i>\n\n"
        f"• <b>Одноразовые карты (откл. после оплаты):</b> {'🟢 ВКЛ' if du else '🔴 ВЫКЛ'}\n"
        "  <i>Сразу после 1 успешной смены региона карта отключается, и следующий заказ гарантированно возьмёт новую карту.</i>"
    )

def _card_rules_menu_kb():
    cfg = _load_config()
    sw = cfg.get("auto_switch_card", True)
    df = cfg.get("disable_failed_card", True)
    du = cfg.get("disable_used_card", False)
    return _make_kb([
        [(f"🔄 Автосмена карт: {'🟢 ВКЛ' if sw else '🔴 ВЫКЛ'}", "src_tgl_auto_switch_card")],
        [(f"🚫 Отключать проблемную: {'🟢 ВКЛ' if df else '🔴 ВЫКЛ'}", "src_tgl_disable_failed_card")],
        [(f"1️⃣ Откл. после 1 оплаты: {'🟢 ВКЛ' if du else '🔴 ВЫКЛ'}", "src_tgl_disable_used_card")],
        [("💳 К базе карт", "src_cards"), ("🛒 К заказам", "src_cat_orders")],
    ])

def _open_card_rules_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _card_rules_menu_text()
    kb = _card_rules_menu_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _messages_menu_text():
    return (
        "💬 <b>Шаблоны сообщений покупателям</b>\n\n"
        "Здесь вы можете изменить тексты сообщений, которые плагин отправляет покупателям в чат FunPay при обработке заказа.\n\n"
        "Выберите сообщение для настройки:"
    )
def _messages_menu_kb():
    rows = []
    for key, label in BUYER_MESSAGE_LABELS.items():
        rows.append([(f"✏️ {label}", f"src_msg_view:{key}")])
    rows.append([("🔄 Сбросить все шаблоны", "src_msg_reset_all")])
    rows.append([("◀️ Назад", "src_cat_orders")])
    return _make_kb(rows)
def _open_messages_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _messages_menu_text()
    kb = _messages_menu_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _open_msg_editor(chat_id, key, message_id=None):
    _waiting[chat_id] = {"action": "edit_buyer_message", "key": key, "prompt_id": message_id}
    label = BUYER_MESSAGE_LABELS.get(key, key)
    cur = _get_buyer_messages().get(key, "")
    hint = BUYER_MESSAGE_HINTS.get(key, "")
    safe_cur = html.escape(cur)
    text = (
        f"💬 <b>Редактирование сообщения</b>\n"
        f"<b>Тип:</b> {label}\n\n"
        f"<b>Текущий текст:</b>\n"
        f"<pre>{safe_cur}</pre>\n\n"
        f"💡 {hint}\n\n"
        "Отправьте новый текст сообщением в чат или выберите действие ниже:"
    )
    kb = _make_kb([
        [("🔄 Сбросить на стандартный", f"src_msg_reset_one:{key}")],
        [("◀️ К списку сообщений", "src_cat_messages")],
    ])
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _queue_menu_text():
    cfg = _load_config()
    workers = cfg.get("max_workers", 5)
    timeout = cfg.get("request_timeout", 30.0)
    delay = cfg.get("rate_limit_delay", 30)
    return (
        "📦 <b>Очередь и лимиты</b>\n\n"
        f"• Одновременных замен аккаунтов: <b>{workers}</b>\n"
        f"• Таймаут запросов к Steam: <b>{int(timeout)} сек</b>\n"
        f"• Задержка при Steam rate-limit: <b>{delay} сек</b>\n\n"
        "Здесь настраивается многопоточность для смены региона и сетевые таймауты запросов через прокси."
    )
def _queue_menu_kb():
    cfg = _load_config()
    workers = cfg.get("max_workers", 5)
    timeout = cfg.get("request_timeout", 30.0)
    rows = [
        [(f"⚡️ Одновременных смен: {workers}", "src_cycle_workers")],
        [(f"⏱ Таймаут запросов: {int(timeout)}с", "src_cycle_timeout")],
        [("◀️ Назад", "src_mini_settings")],
    ]
    return _make_kb(rows)
def _open_queue_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _queue_menu_text()
    kb = _queue_menu_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
CARDS_PER_PAGE = 5
PROXIES_PER_PAGE = 5
REGIONS_PER_PAGE = 12

def _build_region_picker_rows(cb_prefix: str, page: int = 0, page_cb_prefix: str = "", include_any: bool = False, extra_rows: list[list[tuple[str, str]]] | None = None) -> list[list[tuple[str, str]]]:
    codes = (["ANY"] if include_any else []) + list(REGION_CODES_LIST)
    total_pages = max(1, (len(codes) + REGIONS_PER_PAGE - 1) // REGIONS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = codes[page * REGIONS_PER_PAGE : (page + 1) * REGIONS_PER_PAGE]
    rows: list[list[tuple[str, str]]] = []
    cur_row: list[tuple[str, str]] = []
    for cc in chunk:
        if cc == "ANY":
            lbl = "🌐 Любой (ANY)"
        else:
            info = get_region_price_info(cc)
            flag = info["name"].split(" ", 1)[0]
            lbl = f"{flag} {cc} · {info['label']}"
        cur_row.append((lbl, f"{cb_prefix}:{cc}"))
        if len(cur_row) == 2:
            rows.append(cur_row)
            cur_row = []
    if cur_row:
        rows.append(cur_row)
    if total_pages > 1 and page_cb_prefix:
        nav: list[tuple[str, str]] = []
        if page > 0:
            nav.append(("◀️ Пред.", f"{page_cb_prefix}:{page - 1}"))
        nav.append((f"Стр {page + 1}/{total_pages}", "src_lots_noop"))
        if page < total_pages - 1:
            nav.append(("След. ▶️", f"{page_cb_prefix}:{page + 1}"))
        rows.append(nav)
    if extra_rows:
        rows.extend(extra_rows)
    return rows

def _limit_action_label(action: str, max_uses: int = 0) -> str:
    act = str(action or ("deactivate" if max_uses > 0 else "none")).lower()
    if act == "none" or max_uses <= 0:
        return "♾ Безлимит"
    if act == "delete":
        return f"🗑 Удалить после {max_uses} исп."
    return f"⏸ В неактив после {max_uses} исп."

def _cards_menu_text(page: int = 0, country_filter: str | None = None):
    all_cards = _load_cards()
    cf = str(country_filter).upper() if country_filter else None
    cards = [c for c in all_cards if str(c.get("country", "ANY")).upper() == cf] if cf else all_cards
    active_cnt = sum(1 for c in cards if _is_card_usable(c))
    by_cc: dict[str, int] = {}
    for c in all_cards:
        cc = c.get("country", "ANY")
        by_cc[cc] = by_cc.get(cc, 0) + 1
    summary_parts = [f"{_country_display(cc)}: <b>{cnt}</b>" for cc, cnt in list(by_cc.items())[:8]]
    summary_str = ", ".join(summary_parts) if summary_parts else "<i>(база пуста)</i>"
    if len(by_cc) > 8:
        summary_str += f" <i>(+ещё {len(by_cc) - 8} стран)</i>"
    hdr = f"💳 <b>База карт · {_country_display(cf)}</b>" if cf else "💳 <b>Управление общей базой карт</b>"
    return (
        f"{hdr}\n\n"
        f"• Карт: <b>{len(cards)} шт.</b> (активных: <b>🟢 {active_cnt}</b>)\n"
        f"• По странам: {summary_str}\n\n"
        "💡 <i>Карты разделены по странам и подбираются строго под страну лота.</i>\n\n"
        "Нажмите на карту для управления или добавьте новую:"
    )

def _cards_menu_kb(page: int = 0, country_filter: str | None = None):
    all_cards = _load_cards()
    cf = str(country_filter).upper() if country_filter else None
    cards = [c for c in all_cards if str(c.get("country", "ANY")).upper() == cf] if cf else all_cards
    total = len(cards)
    total_pages = max(1, (total + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE) if total > 0 else 1
    page = max(0, min(page, total_pages - 1))
    rows: list[list[tuple[str, str]]] = [
        [("➕ Добавить карту", f"src_cards_add_cc:{cf}" if cf else "src_cards_add")],
        [("🌍 По странам", "src_cards_by_cc"), ("🔄 Автосмена карт", "src_card_rules_menu")],
    ]
    if cf:
        rows.append([("📋 Показать все страны", "src_cards")])
    for c in cards[page * CARDS_PER_PAGE : (page + 1) * CARDS_PER_PAGE]:
        st_ico = "🟢" if c.get("enabled", True) else "🔴"
        masked = _mask_card_number(c.get("number", ""))
        exp = f"{c.get('exp_month', '??')}/{str(c.get('exp_year', '??'))[-2:]}"
        cc = c.get("country", "ANY")
        pm = str(c.get("payment_method") or "visa").upper()[:4]
        lbl_extra = f" · {c['label'][:12]}" if c.get("label") else ""
        rows.append([(f"{st_ico} {cc} · {pm} {masked} ({exp}){lbl_extra}", f"src_card_view:{c['id']}")])
    if total_pages > 1:
        nav = []
        p_pref = f"src_cards_cc_page:{cf}" if cf else "src_cards_page"
        if page > 0:
            nav.append(("◀️ Пред.", f"{p_pref}:{page - 1}"))
        nav.append((f"Стр {page + 1}/{total_pages}", "src_lots_noop"))
        if page < total_pages - 1:
            nav.append(("След. ▶️", f"{p_pref}:{page + 1}"))
        rows.append(nav)
    if all_cards and not cf:
        rows.append([("🗑 Очистить базу", "src_clear_cards_ask")])
    rows.append([("◀️ Назад", "src_cards_by_cc" if cf else "src_db_menu")])
    return _make_kb(rows)

def _open_cards_menu(chat_id, message_id=None, page: int = 0, country_filter: str | None = None):
    _waiting.pop(chat_id, None)
    text = _cards_menu_text(page, country_filter=country_filter)
    kb = _cards_menu_kb(page, country_filter=country_filter)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_cards_by_country(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    cards = _load_cards()
    by_cc: dict[str, list[dict[str, Any]]] = {}
    for c in cards:
        cc = str(c.get("country", "ANY")).upper()
        by_cc.setdefault(cc, []).append(c)
    if not by_cc:
        text = "🌍 <b>Карты по странам</b>\n\n<i>База карт пуста. Добавьте первую карту, выбрав страну.</i>"
        kb = _make_kb([[("➕ Добавить карту", "src_cards_add")], [("◀️ Назад к картам", "src_cards")]])
        _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
        return
    text = (
        "🌍 <b>Карты по странам</b>\n\n"
        f"• Всего карт: <b>{len(cards)} шт.</b> в <b>{len(by_cc)}</b> странах\n\n"
        "Выберите страну, чтобы посмотреть только её карты:"
    )
    rows: list[list[tuple[str, str]]] = []
    cur_row: list[tuple[str, str]] = []
    for cc, lst in sorted(by_cc.items()):
        act = sum(1 for x in lst if _is_card_usable(x))
        cur_row.append((f"{_country_display(cc)} · {act}/{len(lst)}", f"src_cards_filter:{cc}"))
        if len(cur_row) == 2:
            rows.append(cur_row)
            cur_row = []
    if cur_row:
        rows.append(cur_row)
    rows.append([("➕ Добавить карту", "src_cards_add")])
    rows.append([("◀️ Все карты", "src_cards")])
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_cards_add_country(chat_id, message_id=None, page: int = 0):
    _waiting.pop(chat_id, None)
    text = (
        "💳 <b>Добавление карты · Шаг 1 из 5: выбор страны</b>\n\n"
        "Выберите страну карты (или «🌐 Любой (ANY)»):"
    )
    rows = _build_region_picker_rows(
        cb_prefix="src_cards_add_cc",
        page=page,
        page_cb_prefix="src_cards_add_cc_page",
        include_any=True,
        extra_rows=[
            [("✍️ Ввести свой код страны", "src_cards_add_custom_cc")],
            [("◀️ Назад к картам", "src_cards")],
        ],
    )
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_cards_input_prompt(chat_id, cc: str, message_id=None):
    _waiting[chat_id] = {"action": "cw_card_data", "country": cc.upper(), "prompt_id": message_id}
    text = (
        f"💳 <b>Добавление карты · Шаг 2 из 5 ({_country_display(cc)})</b>\n\n"
        "Отправьте <b>номер карты</b> (тогда бот спросит дату и CVV отдельно)\n"
        "<b>ИЛИ</b> сразу все данные карты одной строкой:\n"
        "• <code>4111222233334444:05/35:345</code>\n"
        "• <code>4111222233334444|05/2035|345</code>\n\n"
        "<i>Также можно отправить сразу несколько строк или файл <code>.txt</code>/<code>.json</code> для массовой загрузки.</i>"
    )
    _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_cards")]])) if message_id else _tg_send(chat_id, text, _make_kb([[("❌ Отмена", "src_cards")]]))

def _card_wizard_ask_exp(chat_id, draft: dict[str, Any], message_id=None):
    _waiting[chat_id] = {"action": "cw_card_exp", "draft": draft, "prompt_id": message_id}
    masked = _mask_card_number(draft.get("number", ""))
    text = (
        f"💳 <b>Добавление карты · Срок действия</b>\n\n"
        f"• Страна: <b>{_country_display(draft.get('country', 'ANY'))}</b>\n"
        f"• Номер: <code>{masked}</code>\n\n"
        "Отправьте срок действия карты в формате <code>ММ/ГГ</code> или <code>ММ/ГГГГ</code> (например: <code>05/35</code>):"
    )
    _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_cards")]])) if message_id else _tg_send(chat_id, text, _make_kb([[("❌ Отмена", "src_cards")]]))

def _card_wizard_ask_cvv(chat_id, draft: dict[str, Any], message_id=None):
    _waiting[chat_id] = {"action": "cw_card_cvv", "draft": draft, "prompt_id": message_id}
    masked = _mask_card_number(draft.get("number", ""))
    exp = f"{draft.get('exp_month', '??')}/{draft.get('exp_year', '????')}"
    text = (
        f"💳 <b>Добавление карты · Защитный код (CVV/CVC)</b>\n\n"
        f"• Страна: <b>{_country_display(draft.get('country', 'ANY'))}</b>\n"
        f"• Карта: <code>{masked}</code> ({exp})\n\n"
        "Отправьте 3- или 4-значный код <b>CVV/CVC</b>:"
    )
    _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_cards")]])) if message_id else _tg_send(chat_id, text, _make_kb([[("❌ Отмена", "src_cards")]]))

def _card_wizard_ask_type(chat_id, draft: dict[str, Any], message_id=None):
    _waiting[chat_id] = {"action": "cw_wait_type", "draft": draft, "prompt_id": message_id}
    masked = _mask_card_number(draft.get("number", ""))
    exp = f"{draft.get('exp_month', '??')}/{draft.get('exp_year', '????')}"
    detected = _detect_card_payment_method(draft.get("number", ""))
    det_lbl = "MasterCard" if detected == "mastercard" else ("Visa" if detected == "visa" else detected.upper())
    text = (
        f"💳 <b>Добавление карты · Шаг 3 из 5: тип карты</b>\n\n"
        f"• Страна: <b>{_country_display(draft.get('country', 'ANY'))}</b>\n"
        f"• Карта: <code>{masked}</code> ({exp})\n"
        f"• Определено по номеру: <b>{det_lbl}</b>\n\n"
        "Выберите способ оплаты для Steam (Visa или MasterCard):"
    )
    visa_lbl = "💳 Visa ✅" if detected == "visa" else "💳 Visa"
    mc_lbl = "💳 MasterCard ✅" if detected == "mastercard" else "💳 MasterCard"
    kb = _make_kb([
        [(visa_lbl, "src_cw_type:visa"), (mc_lbl, "src_cw_type:mastercard")],
        [("❌ Отмена", "src_cards")],
    ])
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _card_wizard_ask_billing(chat_id, draft: dict[str, Any], message_id=None):
    _waiting[chat_id] = {"action": "cw_wait_billing_choice", "draft": draft, "prompt_id": message_id}
    masked = _mask_card_number(draft.get("number", ""))
    exp = f"{draft.get('exp_month', '??')}/{draft.get('exp_year', '????')}"
    pm_lbl = "MasterCard" if draft.get("payment_method") == "mastercard" else "Visa"
    cc = draft.get("country", "ANY")
    text = (
        f"🧾 <b>Добавление карты · Шаг 4 из 5: данные счёта</b>\n\n"
        f"• Способ оплаты: <b>{pm_lbl}</b>\n"
        f"• Карта: <code>{masked}</code> ({exp}) · {_country_display(cc)}\n\n"
        "Как заполнять адрес счёта при оплате в Steam?\n"
        "• <b>Авто с сайтов</b> — сейчас покажет 1 тестовый адрес, а при <b>каждом заказе</b> будет генерировать <b>новый</b> адрес.\n"
        "• <b>Вручную</b> — задать один постоянный адрес для этой карты."
    )
    kb = _make_kb([
        [("🌐 Авто с сайтов (новый на каждый заказ)", "src_cw_billing_auto")],
        [("✍️ Ввести постоянный адрес вручную", "src_cw_billing_manual")],
        [("❌ Отмена", "src_cards")],
    ])
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _card_wizard_fetch_billing_worker(chat_id, draft: dict[str, Any], message_id=None, go_preview: bool = False):
    cc = draft.get("country", "US")
    wait_txt = (
        f"⏳ <b>Генерируем тестовый адрес для {_country_display(cc)}...</b>\n"
        "Запрашиваем пример с сайтов (addressrussia.com / dummydatatools.com / randaddress.com)..."
    )
    if message_id:
        _tg_edit(chat_id, message_id, wait_txt)
    else:
        _tg_send(chat_id, wait_txt)
    addr = fetch_random_billing_address(cc)
    draft["auto_billing"] = True
    draft["holder"] = addr.get("holder", "")
    draft["address"] = addr.get("address", "")
    draft["address_two"] = addr.get("address_two", "")
    draft["city"] = addr.get("city", "")
    draft["state"] = addr.get("state", "")
    draft["zip"] = addr.get("zip", "")
    draft["phone"] = addr.get("phone", "")
    draft["billing_source"] = addr.get("source", "")
    draft["billing_source_url"] = addr.get("source_url", "")
    if go_preview or draft.get("limit_configured"):
        _card_wizard_preview(chat_id, draft, message_id=message_id)
    else:
        _card_wizard_show_test_addr(chat_id, draft, message_id=message_id)

def _card_wizard_show_test_addr(chat_id, draft: dict[str, Any], message_id=None):
    _waiting[chat_id] = {"action": "cw_wait_billing_choice", "draft": draft, "prompt_id": message_id}
    holder = draft.get("holder") or "—"
    parts = holder.split(None, 1) if holder != "—" else ["—", "—"]
    fname = parts[0]
    lname = parts[1] if len(parts) > 1 else "—"
    s_name = html.escape(str(draft.get("billing_source") or "генератор"))
    s_url = draft.get("billing_source_url") or ""
    src_line = f"<a href=\"{s_url}\">{s_name}</a>" if s_url else s_name
    text = (
        f"🧪 <b>Тестовый адрес для {_country_display(draft.get('country', 'US'))}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Имя / Фамилия:</b> <code>{html.escape(fname)}</code> / <code>{html.escape(lname)}</code>\n"
        f"• <b>Расчётный адрес:</b> <code>{html.escape(draft.get('address') or '—')}</code>\n"
        f"• <b>Город / Индекс:</b> <code>{html.escape(draft.get('city') or '—')}</code> / <code>{html.escape(draft.get('zip') or '—')}</code>\n"
        f"• <b>Телефон:</b> <code>{html.escape(draft.get('phone') or '—')}</code>\n"
        f"• <b>Источник:</b> {src_line}\n\n"
        "<i>Это пример для проверки. При каждом реальном заказе бот будет генерировать новый адрес автоматически.</i>"
    )
    kb = _make_kb([
        [("➡️ Далее: настроить лимит", "src_cw_go_limit")],
        [("🔄 Сгенерировать другой тест", "src_cw_billing_auto")],
        [("✍️ Ввести постоянный адрес вручную", "src_cw_billing_manual")],
        [("❌ Отмена", "src_cards")],
    ])
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _card_wizard_ask_limit(chat_id, draft: dict[str, Any], message_id=None):
    _waiting[chat_id] = {"action": "cw_wait_limit_action", "draft": draft, "prompt_id": message_id}
    masked = _mask_card_number(draft.get("number", ""))
    text = (
        f"📊 <b>Добавление карты · Шаг 5 из 5: лимит использований</b>\n\n"
        f"• Карта: <code>{masked}</code> ({_country_display(draft.get('country', 'ANY'))})\n\n"
        "Выберите, <b>что делать с картой</b>, когда она достигнет лимита:\n"
        "• <b>⏸ Убрать в неактивный пул</b> — карта выключится, но останется в базе\n"
        "• <b>🗑 Удалить из плагина</b> — карта автоматически удалится из базы\n"
        "• <b>♾ Ничего (безлимит)</b> — без ограничений (лимит можно изменить позже)"
    )
    kb = _make_kb([
        [("⏸ Убрать в неактивный пул", "src_cw_lim_act:deactivate")],
        [("🗑 Удалить карту из плагина", "src_cw_lim_act:delete")],
        [("♾ Ничего (безлимит)", "src_cw_lim_act:none")],
        [("❌ Отмена", "src_cards")],
    ])
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _card_wizard_ask_limit_count(chat_id, draft: dict[str, Any], message_id=None):
    _waiting[chat_id] = {"action": "cw_limit_count", "draft": draft, "prompt_id": message_id}
    act = draft.get("limit_action", "deactivate")
    act_txt = "🗑 удалить карту из плагина" if act == "delete" else "⏸ убрать карту в неактивный пул"
    text = (
        f"🔢 <b>Укажите число лимита для карты</b>\n\n"
        f"• Действие при достижении лимита: <b>{act_txt}</b>\n\n"
        "Отправьте целое число — сколько раз можно использовать эту карту (например: <code>1</code>, <code>3</code>, <code>5</code>, <code>10</code>):"
    )
    kb = _make_kb([
        [("◀️ Выбрать другое действие", "src_cw_go_limit")],
        [("❌ Отмена", "src_cards")],
    ])
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _card_wizard_preview(chat_id, draft: dict[str, Any], message_id=None):
    _waiting[chat_id] = {"action": "cw_confirm", "draft": draft, "prompt_id": message_id}
    masked = _mask_card_number(draft.get("number", ""))
    exp = f"{draft.get('exp_month', '??')}/{draft.get('exp_year', '????')}"
    pm_lbl = "MasterCard" if draft.get("payment_method") == "mastercard" else "Visa"
    cc = draft.get("country", "ANY")
    is_auto_bill = bool(draft.get("auto_billing", False))
    lim_str = _limit_action_label(draft.get("limit_action", "none"), int(draft.get("max_uses", 0) or 0))
    holder = draft.get("holder") or "—"
    parts = holder.split(None, 1) if holder != "—" else ["—", "—"]
    fname = parts[0]
    lname = parts[1] if len(parts) > 1 else "—"
    addr2_line = f"\n• <b>Адрес (строка 2):</b> <code>{html.escape(draft['address_two'])}</code>" if draft.get("address_two") else ""
    bill_hdr = (
        "🌐 <b>Адрес:</b> новый с сайтов на каждый заказ\n<i>(тестовый пример ниже)</i>"
        if is_auto_bill
        else "✍️ <b>Адрес:</b> постоянный (введён вручную)"
    )
    text = (
        f"✅ <b>Проверьте данные карты перед сохранением</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Способ оплаты:</b> {pm_lbl}\n"
        f"• <b>Номер карты:</b> <code>{masked}</code>\n"
        f"• <b>Срок / CVV:</b> <code>{exp}</code> / <code>***</code>\n"
        f"• <b>Страна:</b> {_country_display(cc)} (<code>{cc}</code>)\n"
        f"• <b>Лимит:</b> {lim_str}\n\n"
        f"{bill_hdr}\n"
        f"• <b>Имя / Фамилия:</b> <code>{html.escape(fname)}</code> / <code>{html.escape(lname)}</code>\n"
        f"• <b>Расчётный адрес:</b> <code>{html.escape(draft.get('address') or '—')}</code>{addr2_line}\n"
        f"• <b>Город / Индекс:</b> <code>{html.escape(draft.get('city') or '—')}</code> / <code>{html.escape(draft.get('zip') or '—')}</code>\n"
        f"• <b>Телефон:</b> <code>{html.escape(draft.get('phone') or '—')}</code>"
    )
    kb = _make_kb([
        [("✅ Сохранить карту", "src_cw_save")],
        [("📊 Изменить лимит", "src_cw_go_limit"), ("🔄 Другой тест-адрес", "src_cw_retest_addr")],
        [("✍️ Ввести адрес вручную", "src_cw_billing_manual")],
        [("❌ Отмена", "src_cards")],
    ])
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _card_wizard_ask_manual_billing(chat_id, draft: dict[str, Any], message_id=None):
    _waiting[chat_id] = {"action": "cw_billing_manual_input", "draft": draft, "prompt_id": message_id}
    cc = draft.get("country", "US")
    text = (
        f"✍️ <b>Ввод данных счёта вручную ({_country_display(cc)})</b>\n\n"
        "Отправьте данные <b>одной строкой через <code>|</code></b>:\n"
        "<code>Имя Фамилия | Расчётный адрес | Город | Индекс | Телефон</code>\n"
        "<i>(Можно добавить строку 2 адреса: <code>Имя Фамилия | Адрес | Адрес 2 | Город | Индекс | Телефон</code>)</i>\n\n"
        "<b>Пример:</b>\n"
        "<code>John Smit | 202 Telok Ayer St | Singapore | 068639 | +6587981783</code>\n\n"
        "Или отправьте только <b>Имя и Фамилию</b> — тогда бот спросит остальные поля по шагам."
    )
    kb = _make_kb([
        [("🌐 Лучше авто с сайтов", "src_cw_billing_auto")],
        [("❌ Отмена", "src_cards")],
    ])
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_card_detail(chat_id, card_id: str, message_id=None):
    _waiting.pop(chat_id, None)
    cards = _load_cards()
    card = next((c for c in cards if c.get("id") == card_id), None)
    if not card:
        _tg_send(chat_id, "⚠️ Карта не найдена.")
        _open_cards_menu(chat_id, message_id)
        return
    enabled = card.get("enabled", True)
    masked = _mask_card_number(card.get("number", ""))
    exp = f"{card.get('exp_month', '??')}/{card.get('exp_year', '????')}"
    cc = card.get("country", "ANY")
    pm = str(card.get("payment_method") or _detect_card_payment_method(card.get("number", ""))).strip().lower()
    pm_lbl = "MasterCard" if pm == "mastercard" else ("Visa" if pm == "visa" else pm.upper())
    used = int(card.get("used_count", 0) or 0)
    max_u = int(card.get("max_uses", 0) or 0)
    lim_act = str(card.get("limit_action") or ("deactivate" if max_u > 0 else "none")).lower()
    fails = int(card.get("fail_count", 0) or 0)
    uses_str = f"{used} / {max_u}" if max_u > 0 else f"{used} / ∞"
    lim_desc = _limit_action_label(lim_act, max_u)
    auto_bill = bool(card.get("auto_billing", False))
    lines = [
        f"💳 <b>Карта <code>{card['id']}</code></b>\n",
        f"• Способ оплаты: <b>{pm_lbl}</b>",
        f"• Номер: <code>{masked}</code>",
        f"• Срок: <code>{exp}</code> | CVV: <code>***</code>",
        f"• Страна: <b>{_country_display(cc)}</b> (<code>{cc}</code>)",
        f"• Статус: <b>{'🟢 Активна' if enabled else '🔴 Отключена'}</b>",
        f"• Использований: <b>{uses_str}</b> ({lim_desc}) | Ошибок: <b>{fails}</b>",
        f"• Режим адреса: <b>{'🌐 Авто с сайтов (новый на заказ)' if auto_bill else '✍️ Постоянный (вручную)'}</b>",
    ]
    if card.get("last_error"):
        lines.append(f"• Последняя ошибка: <i>{html.escape(card['last_error'])}</i>")
    if not auto_bill:
        if card.get("holder"):
            lines.append(f"• Владелец: <code>{html.escape(card['holder'])}</code>")
        if card.get("address") or card.get("address_two") or card.get("city") or card.get("zip"):
            addr = ", ".join(x for x in (card.get("address"), card.get("address_two"), card.get("city"), card.get("zip")) if x)
            lines.append(f"• Адрес: <code>{html.escape(addr)}</code>")
        if card.get("phone"):
            lines.append(f"• Телефон: <code>{html.escape(card['phone'])}</code>")
    if card.get("label"):
        lines.append(f"• Заметка: <i>{html.escape(card['label'])}</i>")
    rows = [
        [(f"🧩 Статус: {'🟢 ВКЛ' if enabled else '🔴 ВЫКЛ'}", f"src_card_tgl:{card_id}")],
        [(f"📊 Лимит: {lim_desc}", f"src_card_limit_menu:{card_id}")],
        [(f"🌐 Адрес: {'Авто (новый)' if auto_bill else 'Вручную'}", f"src_card_tgl_autobill:{card_id}")],
        [(f"🌍 Изменить страну ({cc})", f"src_card_cc:{card_id}:0")],
        [("✏️ Изменить заметку", f"src_card_edit_note:{card_id}")],
        [("🗑 Удалить карту", f"src_card_del_ask:{card_id}")],
        [("◀️ К списку карт", "src_cards")],
    ]
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, "\n".join(lines), kb) if message_id else _tg_send(chat_id, "\n".join(lines), kb)

def _open_card_limit_menu(chat_id, card_id: str, message_id=None):
    _waiting.pop(chat_id, None)
    cards = _load_cards()
    card = next((c for c in cards if c.get("id") == card_id), None)
    if not card:
        _open_cards_menu(chat_id, message_id)
        return
    max_u = int(card.get("max_uses", 0) or 0)
    lim_act = str(card.get("limit_action") or ("deactivate" if max_u > 0 else "none")).lower()
    text = (
        f"📊 <b>Настройка лимита карты <code>{card_id}</code></b>\n\n"
        f"• Текущее правило: <b>{_limit_action_label(lim_act, max_u)}</b>\n"
        f"• Использовано сейчас: <b>{int(card.get('used_count', 0) or 0)}</b>\n\n"
        "Выберите действие при достижении лимита:"
    )
    kb = _make_kb([
        [("⏸ Убрать в неактивный пул", f"src_card_lim_act:{card_id}:deactivate")],
        [("🗑 Удалить карту из плагина", f"src_card_lim_act:{card_id}:delete")],
        [("♾ Ничего (безлимит)", f"src_card_lim_act:{card_id}:none")],
        [("◀️ Назад к карте", f"src_card_view:{card_id}")],
    ])
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_card_change_cc(chat_id, card_id: str, page: int = 0, message_id=None):
    _waiting.pop(chat_id, None)
    text = f"🌍 <b>Выберите новый регион для карты <code>{card_id}</code>:</b>"
    rows = _build_region_picker_rows(
        cb_prefix=f"src_card_set_cc:{card_id}",
        page=page,
        page_cb_prefix=f"src_card_cc:{card_id}",
        include_any=True,
        extra_rows=[[("◀️ Назад к карте", f"src_card_view:{card_id}")]],
    )
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _games_menu_text(page: int = 0):
    cfg = _load_config()
    sp = bool(cfg.get("search_specials_only", True))
    mult = float(cfg.get("search_max_price_multiplier", 2.0))
    custom_prices = cfg.get("custom_region_prices")
    custom_cnt = len(custom_prices) if isinstance(custom_prices, dict) else 0
    return (
        "💰 <b>Настройка цен и поиск дешёвых игр</b>\n\n"
        f"• Регионов в таблице: <b>{len(REGION_CODES_LIST)}</b> (изменено вручную: <b>{custom_cnt}</b>)\n"
        f"• Только со скидкой: <b>{'🟢 ВКЛ' if sp else '🔴 ВЫКЛ'}</b>\n"
        f"• Фиксация макс. цены: <b>×{mult:g} от цены региона</b>\n\n"
        "Нажмите на любой регион ниже, чтобы проверить актуальные дешёвые игры в Steam, или задайте свою цену для региона:"
    )

def _games_menu_kb(page: int = 0):
    cfg = _load_config()
    sp = bool(cfg.get("search_specials_only", True))
    mult = float(cfg.get("search_max_price_multiplier", 2.0))
    custom_prices = cfg.get("custom_region_prices")
    custom_cnt = len(custom_prices) if isinstance(custom_prices, dict) else 0
    top_rows: list[list[tuple[str, str]]] = [
        [(f"🏷 Только скидки: {'🟢 ВКЛ' if sp else '🔴 ВЫКЛ'}", "src_tgl_search_specials")],
        [(f"📌 Фиксация макс. цены: ×{mult:g}", "src_input_search_mult")],
        [("✏️ Изменить цену региона", "src_price_edit_menu:0")],
        [("📋 Таблица цен (39 рег.)", "src_games_table:0"), ("🔍 Поиск по коду", "src_games_input_cc")],
    ]
    if custom_cnt > 0:
        top_rows.append([(f"🔄 Сбросить свои цены ({custom_cnt})", "src_price_reset_all")])
    region_rows = _build_region_picker_rows(
        cb_prefix="src_games_find",
        page=page,
        page_cb_prefix="src_games_page",
        include_any=False,
        extra_rows=[[("◀️ Назад", "src_mini_settings")]],
    )
    return _make_kb(top_rows + region_rows)

def _open_games_menu(chat_id, message_id=None, page: int = 0):
    _waiting.pop(chat_id, None)
    text = _games_menu_text(page)
    kb = _games_menu_kb(page)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_price_edit_menu(chat_id, page: int = 0, message_id=None):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    custom_prices = cfg.get("custom_region_prices")
    custom_cnt = len(custom_prices) if isinstance(custom_prices, dict) else 0
    text = (
        "✏️ <b>Изменение цены по регионам</b>\n\n"
        f"• Изменено вручную: <b>{custom_cnt}</b> из {len(REGION_CODES_LIST)}\n\n"
        "Выберите регион, чтобы задать свою фиксированную цену для поиска дешёвых игр:"
    )
    extra: list[list[tuple[str, str]]] = []
    if custom_cnt > 0:
        extra.append([(f"🔄 Сбросить все свои цены ({custom_cnt})", "src_price_reset_all")])
    extra.append([("◀️ К настройке цен", "src_games_menu")])
    rows = _build_region_picker_rows(
        cb_prefix="src_price_edit_cc",
        page=page,
        page_cb_prefix="src_price_edit_menu",
        include_any=False,
        extra_rows=extra,
    )
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_price_edit_prompt(chat_id, cc: str, message_id=None):
    cc = str(cc or "US").strip().upper()
    info = get_region_price_info(cc)
    base = REGION_CHEAP_PRICES.get(cc) or info
    cfg = _load_config()
    custom_map = cfg.get("custom_region_prices") if isinstance(cfg.get("custom_region_prices"), dict) else {}
    is_custom = cc in custom_map
    _waiting[chat_id] = {"action": "price_edit_val", "country": cc, "prompt_id": message_id}
    text = (
        f"💰 <b>Настройка цены · {info['name']} (<code>{cc}</code>)</b>\n\n"
        f"• Валюта: <b>{info['currency']}</b>\n"
        f"• Текущая цена: <b>{info['label']}</b>\n"
        f"• Стандартная цена: <b>{base['label']}</b>\n"
        f"• Статус: {'✏️ изменено вручную' if is_custom else '📋 стандартное значение'}\n\n"
        "Отправьте новую цену числом в чат:\n"
        "• Например: <code>20</code> или <code>0.75</code>\n"
        "• Отправьте <code>-</code>, чтобы сбросить регион на стандартную цену"
    )
    rows: list[list[tuple[str, str]]] = []
    if is_custom:
        rows.append([("🔄 Сбросить на стандартную", f"src_price_reset_cc:{cc}")])
    rows.append([("◀️ К выбору региона", "src_price_edit_menu:0")])
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_games_price_table(chat_id, page: int = 0, message_id=None):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    custom_map = cfg.get("custom_region_prices") if isinstance(cfg.get("custom_region_prices"), dict) else {}
    per_page = 15
    total_pages = max(1, (len(REGION_CODES_LIST) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = REGION_CODES_LIST[page * per_page : (page + 1) * per_page]
    lines = [f"📋 <b>Таблица цен по регионам (стр. {page + 1}/{total_pages})</b>\n"]
    for cc in chunk:
        info = get_region_price_info(cc)
        mark = " ✏️" if cc in custom_map else ""
        lines.append(f"• {info['name']} (<code>{cc}</code> / {info['currency']}): <b>{info['label']}</b>{mark}")
    nav: list[tuple[str, str]] = []
    if page > 0:
        nav.append(("◀️ Пред.", f"src_games_table:{page - 1}"))
    nav.append((f"{page + 1}/{total_pages}", "src_lots_noop"))
    if page < total_pages - 1:
        nav.append(("След. ▶️", f"src_games_table:{page + 1}"))
    rows = [
        nav,
        [("✏️ Изменить цену региона", "src_price_edit_menu:0")],
        [("◀️ К настройке цен", "src_games_menu")],
    ]
    _tg_edit(chat_id, message_id, "\n".join(lines), _make_kb(rows)) if message_id else _tg_send(chat_id, "\n".join(lines), _make_kb(rows))

def _search_games_worker(chat_id, country_code: str, message_id=None, back_cb: str = "src_games_menu", proxy: str | None = None):
    info = get_region_price_info(country_code)
    cc = info["code"]
    wait_text = (
        f"⏳ <b>Ищем дешёвые игры в Steam для {info['name']} ({cc})...</b>\n"
        f"Ориентир цены: <b>{info['label']}</b> ({info['currency']})"
    )
    wait_kb = _make_kb([[("◀️ Назад", back_cb)]])
    if message_id:
        _tg_edit(chat_id, message_id, wait_text, wait_kb)
    elif bot_instance and chat_id:
        try:
            sent_msg = bot_instance.send_message(int(chat_id), wait_text, parse_mode='HTML', reply_markup=wait_kb, disable_web_page_preview=True)
            message_id = getattr(sent_msg, "message_id", None)
        except Exception:
            pass
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def _run():
        st = ProxyRequestStrategy(proxy) if proxy else None
        try:
            return await search_cheap_games(st, cc, limit=5, resolve_packages=True)
        finally:
            if st is not None:
                await st.close()
    try:
        games = loop.run_until_complete(_run())
        cfg = _load_config()
        sp = bool(cfg.get("search_specials_only", True))
        web_url = build_steam_cheap_search_url(cc, specials=sp, for_results_api=False)
        if not games:
            res_text = (
                f"⚠️ <b>Дешёвые игры для {info['name']} ({cc}) не найдены.</b>\n\n"
                f"• Ориентир цены: <b>{info['label']}</b>\n"
                f"• Ссылка поиска: <a href=\"{web_url}\">Открыть в Steam</a>"
            )
        else:
            lines = [
                f"💰 <b>Дешёвые игры для {info['name']} ({cc})</b>\n",
                f"• Валюта: <b>{info['currency']}</b> | Ориентир цены: <b>{info['label']}</b>",
                f"• Поиск Steam: <a href=\"{web_url}\">Открыть список</a>\n",
            ]
            for i, g in enumerate(games, 1):
                title = html.escape(str(g.get("title", "Игра")))
                price = html.escape(str(g.get("price_formatted", "")))
                disc = f" (-{g['discount_pct']}%)" if g.get("discount_pct") else ""
                appid = g.get("appid") or "—"
                subid = g.get("packageid") or "—"
                url = g.get("url") or URL_STORE
                lines.append(
                    f"{i}. <a href=\"{url}\">{title}</a> — <b>{price}</b>{disc}\n"
                    f"   <code>AppID: {appid}</code> | <code>SubID: {subid}</code>"
                )
            res_text = "\n".join(lines)
    except Exception as e:
        res_text = f"❌ Ошибка поиска дешёвых игр ({cc}): <code>{html.escape(str(e)[:200])}</code>"
    finally:
        loop.close()
    kb = _make_kb([
        [("🔄 Обновить поиск", f"src_games_find:{cc}"), (f"✏️ Цена ({cc})", f"src_price_edit_cc:{cc}")],
        [("◀️ Назад", back_cb)],
    ])
    _tg_edit(chat_id, message_id, res_text, kb) if message_id else _tg_send(chat_id, res_text, kb)

def _proxies_menu_text(page: int = 0, country_filter: str | None = None):
    by_cc = _load_proxies_by_country()
    cf = str(country_filter).upper() if country_filter else None
    if cf:
        proxies = list(by_cc.get(cf) or [])
        total = len(proxies)
        total_pages = max(1, (total + PROXIES_PER_PAGE - 1) // PROXIES_PER_PAGE) if total > 0 else 1
        page = max(0, min(page, total_pages - 1))
        page_info = f" (стр. {page + 1}/{total_pages})" if total_pages > 1 else ""
        hint = "Нажмите на прокси ниже для проверки или управления:" if total > 0 else "<i>Список прокси этой страны пуст.</i>"
        return (
            f"🌐 <b>База прокси · {_country_display(cf)}{page_info}</b>\n\n"
            f"• Прокси: <b>{total} шт.</b>\n\n"
            f"{hint}"
        )
    total = sum(len(v) for v in by_cc.values())
    summary_parts = [f"{_country_display(cc)}: <b>{len(lst)}</b>" for cc, lst in list(sorted(by_cc.items()))[:8]]
    summary_str = ", ".join(summary_parts) if summary_parts else "<i>(база пуста)</i>"
    if len(by_cc) > 8:
        summary_str += f" <i>(+ещё {len(by_cc) - 8} стран)</i>"
    return (
        "🌐 <b>Управление общей базой прокси</b>\n\n"
        f"• Прокси: <b>{total} шт.</b>\n"
        f"• По странам: {summary_str}"
    )

def _proxies_menu_kb(page: int = 0, country_filter: str | None = None):
    cf = str(country_filter).upper() if country_filter else None
    if cf:
        by_cc = _load_proxies_by_country()
        proxies = list(by_cc.get(cf) or [])
        total = len(proxies)
        total_pages = max(1, (total + PROXIES_PER_PAGE - 1) // PROXIES_PER_PAGE) if total > 0 else 1
        page = max(0, min(page, total_pages - 1))
        start = page * PROXIES_PER_PAGE
        end = start + PROXIES_PER_PAGE
        rows: list[list[tuple[str, str]]] = []
        for idx in range(start, min(end, total)):
            clean_p = _mask_proxy(proxies[idx])
            rows.append([(f"🌐 {idx + 1}. {clean_p[:38]}", f"src_proxy_view:{cf}:{idx}")])
        if total_pages > 1:
            nav: list[tuple[str, str]] = []
            if page > 0:
                nav.append(("◀️ Пред.", f"src_proxies_cc_page:{cf}:{page - 1}"))
            nav.append((f"Стр {page + 1}/{total_pages}", "src_lots_noop"))
            if page < total_pages - 1:
                nav.append(("След. ▶️", f"src_proxies_cc_page:{cf}:{page + 1}"))
            rows.append(nav)
        if proxies:
            rows.append([(f"🗑 Удалить прокси ({cf})", f"src_clear_proxies_cc:{cf}")])
        rows.append([("◀️ Назад", "src_proxies_by_cc")])
        return _make_kb(rows)

    proxies = _load_proxies()
    rows = [
        [("➕ Добавить прокси", "src_input_proxies")],
        [("🌍 Прокси по странам", "src_proxies_by_cc")],
    ]
    if proxies:
        rows.append([("🧪 Тест всех прокси", "src_test_proxies")])
        rows.append([("🗑 Очистить базу прокси", "src_clear_proxies_ask")])
    rows.append([("◀️ Назад", "src_db_menu")])
    return _make_kb(rows)

def _menu_proxies(chat_id, message_id=None, page: int = 0, country_filter: str | None = None):
    _waiting.pop(chat_id, None)
    text = _proxies_menu_text(page=page, country_filter=country_filter)
    kb = _proxies_menu_kb(page=page, country_filter=country_filter)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_proxy_detail(chat_id, cc: str, idx: int, message_id=None, check_status: str | None = None):
    _waiting.pop(chat_id, None)
    cc = str(cc or "ANY").upper()
    by_cc = _load_proxies_by_country()
    lst = list(by_cc.get(cc) or [])
    if idx < 0 or idx >= len(lst):
        _menu_proxies(chat_id, message_id, page=0, country_filter=cc)
        return
    clean_p = _mask_proxy(lst[idx])
    lines = [
        f"🌐 <b>Настройка прокси #{idx + 1}</b>\n",
        f"• Страна: <b>{_country_display(cc)}</b> (<code>{cc}</code>)",
        f"• Прокси: <code>{html.escape(clean_p)}</code>",
    ]
    if check_status:
        lines.append(f"• Проверка: {check_status}")
    rows = [
        [("🧪 Проверить прокси", f"src_proxy_test_one:{cc}:{idx}")],
        [(f"🌍 Изменить страну ({cc})", f"src_proxy_ch_cc:{cc}:{idx}:0")],
        [("🗑 Удалить прокси", f"src_proxy_del:{cc}:{idx}")],
        [("◀️ Назад", f"src_proxies_filter:{cc}")],
    ]
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, "\n".join(lines), kb) if message_id else _tg_send(chat_id, "\n".join(lines), kb)

def _test_one_proxy_worker(chat_id, cc: str, idx: int, message_id=None):
    cc = str(cc or "ANY").upper()
    by_cc = _load_proxies_by_country()
    lst = list(by_cc.get(cc) or [])
    if idx < 0 or idx >= len(lst):
        _menu_proxies(chat_id, message_id, page=0, country_filter=cc)
        return
    proxy = lst[idx]
    clean_p = _mask_proxy(proxy)
    _tg_edit(
        chat_id,
        message_id,
        f"⏳ <b>Проверяем прокси...</b>\n<code>{html.escape(clean_p)}</code>",
        _make_kb([[("◀️ Назад", f"src_proxy_view:{cc}:{idx}")]]),
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        ok, diag = loop.run_until_complete(check_proxy(proxy, timeout=8.0))
        status_str = f"🟢 <b>Работает</b> ({html.escape(str(diag))} · Steam OK)" if ok else f"🔴 <b>Ошибка</b> ({html.escape(str(diag))})"
    except Exception as e:
        status_str = f"🔴 <b>Ошибка:</b> <code>{html.escape(str(e)[:120])}</code>"
    finally:
        loop.close()
    _open_proxy_detail(chat_id, cc, idx, message_id=message_id, check_status=status_str)

def _open_proxy_change_cc(chat_id, cc: str, idx: int, page: int = 0, message_id=None):
    _waiting.pop(chat_id, None)
    text = f"🌍 <b>Выберите новую страну для прокси #{idx + 1} ({cc}):</b>"
    rows = _build_region_picker_rows(
        cb_prefix=f"src_proxy_set_cc:{cc}:{idx}",
        page=page,
        page_cb_prefix=f"src_proxy_ch_cc:{cc}:{idx}",
        include_any=True,
        extra_rows=[[("◀️ Назад к прокси", f"src_proxy_view:{cc}:{idx}")]],
    )
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_proxies_by_country(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    by_cc = _load_proxies_by_country()
    total = sum(len(v) for v in by_cc.values())
    if not by_cc:
        text = "🌍 <b>Прокси по странам</b>\n\n<i>База прокси пуста. Добавьте прокси, выбрав нужную страну.</i>"
        kb = _make_kb([[("➕ Добавить прокси", "src_input_proxies")], [("◀️ Назад к прокси", "src_proxies")]])
        _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
        return
    text = (
        "🌍 <b>Прокси по странам</b>\n\n"
        f"• Всего прокси: <b>{total} шт.</b> в <b>{len(by_cc)}</b> странах\n\n"
        "Выберите страну, чтобы посмотреть только её прокси:"
    )
    rows: list[list[tuple[str, str]]] = []
    cur_row: list[tuple[str, str]] = []
    for cc, lst in sorted(by_cc.items()):
        cur_row.append((f"{_country_display(cc)} · {len(lst)} шт.", f"src_proxies_filter:{cc}"))
        if len(cur_row) == 2:
            rows.append(cur_row)
            cur_row = []
    if cur_row:
        rows.append(cur_row)
    rows.append([("➕ Добавить прокси", "src_input_proxies")])
    rows.append([("◀️ Все прокси", "src_proxies")])
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_proxies_add_country(chat_id, message_id=None, page: int = 0):
    _waiting.pop(chat_id, None)
    text = (
        "🌐 <b>Добавление прокси · Шаг 1: выбор страны</b>\n\n"
        "Выберите страну прокси (прокси будут привязаны к этой стране для лотов):"
    )
    rows = _build_region_picker_rows(
        cb_prefix="src_proxies_add_cc",
        page=page,
        page_cb_prefix="src_proxies_add_cc_page",
        include_any=True,
        extra_rows=[
            [("✍️ Ввести свой код страны", "src_proxies_add_custom_cc")],
            [("◀️ Назад к прокси", "src_proxies")],
        ],
    )
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_proxies_input_prompt(chat_id, cc: str, message_id=None):
    _waiting[chat_id] = {"action": "input_proxies", "country": cc.upper(), "prompt_id": message_id}
    text = (
        f"➕ <b>Добавление прокси ({_country_display(cc)})</b>\n\n"
        "Отправьте список прокси текстом (каждый с новой строки) или загрузите файл <code>.txt</code>:\n\n"
        "💡 <b>Поддерживаются форматы (HTTP/HTTPS/SOCKS5):</b>\n"
        "• <code>ip:port</code>\n"
        "• <code>ip:port:user:pass</code>\n"
        "• <code>http://user:pass@ip:port</code>\n"
        "• <code>socks5://user:pass@ip:port</code>"
    )
    _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_proxies")]])) if message_id else _tg_send(chat_id, text, _make_kb([[("❌ Отмена", "src_proxies")]]))
def _maint_menu_text():
    return (
        "🧰 <b>Обслуживание плагина</b>\n\n"
        "Здесь вы можете управлять конфигурацией плагина или скачать файлы логов:\n\n"
        "• <b>Конфигурация</b> — резервное копирование и восстановление настроек (settings.json).\n"
        "• <b>Скачать логи</b> — выгрузка лог-файлов Cardinal и плагина."
    )
def _maint_menu_kb():
    rows = [
        [("⚙️ Конфигурация", "src_maint_cfg_menu")],
        [("📄 Скачать логи", "src_maint_download_logs")],
        [("◀️ Назад", "src_mini_settings")],
    ]
    return _make_kb(rows)
def _open_maint_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _maint_menu_text()
    kb = _maint_menu_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _build_export_payload(kind: str) -> tuple[str, str, dict[str, Any]]:
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    settings_only = {k: v for k, v in cfg.items() if k != "lots"}
    cards = _load_cards()
    proxies_by_cc = _load_proxies_by_country()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    meta = {"plugin": NAME, "version": VERSION, "export_type": kind, "exported_at": stamp}

    if kind == "full":
        return (
            f"src_v2_full_backup_{stamp}.json",
            f"📦 Полный конфиг {NAME} (настройки + {len(lots)} лотов + {len(cards)} карт + {sum(len(v) for v in proxies_by_cc.values())} прокси)",
            {"meta": meta, "settings": settings_only, "lots": lots, "cards": cards, "proxies_by_country": proxies_by_cc},
        )
    if kind == "settings":
        return (
            f"src_v2_settings_{stamp}.json",
            f"⚙️ Данные настроек {NAME}",
            {"meta": meta, "settings": settings_only},
        )
    if kind == "lots":
        return (
            f"src_v2_lots_{stamp}.json",
            f"⭐ Данные настройки лотов ({len(lots)} шт.)",
            {"meta": meta, "lots": lots},
        )
    if kind == "cards":
        return (
            f"src_v2_cards_{stamp}.json",
            f"💳 Данные карт ({len(cards)} шт.)",
            {"meta": meta, "cards": cards},
        )
    if kind == "proxies":
        total_px = sum(len(v) for v in proxies_by_cc.values())
        return (
            f"src_v2_proxies_{stamp}.json",
            f"🌐 Данные прокси ({total_px} шт.)",
            {"meta": meta, "proxies_by_country": proxies_by_cc},
        )
    if kind == "cards_proxies":
        total_px = sum(len(v) for v in proxies_by_cc.values())
        return (
            f"src_v2_cards_proxies_{stamp}.json",
            f"💳🌐 Данные карт ({len(cards)} шт.) + прокси ({total_px} шт.)",
            {"meta": meta, "cards": cards, "proxies_by_country": proxies_by_cc},
        )
    return (
        f"src_v2_config_{stamp}.json",
        f"📄 Конфигурация {NAME}",
        cfg,
    )

def _apply_imported_backup(data: Any) -> str:
    if isinstance(data, list):
        valid = [c for c in (normalize_card_entry(x) for x in data) if c]
        if valid:
            _save_cards(valid)
            return f"💳 Карты: импортировано <b>{len(valid)} шт.</b>"
        raise ValueError("Пустой или некорректный список карт")
    if not isinstance(data, dict):
        raise ValueError("Ожидается JSON-объект")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    if os.path.exists(CONFIG_FILE):
        try:
            shutil.copy2(CONFIG_FILE, CONFIG_FILE + f".{stamp}.bak")
        except Exception:
            pass

    imported_parts: list[str] = []
    if "meta" in data or any(k in data for k in ("settings", "cards", "proxies_by_country")):
        cfg = _load_config()
        if isinstance(data.get("settings"), dict):
            for k, v in data["settings"].items():
                if k != "lots":
                    cfg[k] = v
            _save_config(cfg)
            imported_parts.append("⚙️ Настройки плагина")
        if isinstance(data.get("lots"), dict):
            cfg = _load_config()
            cfg["lots"] = data["lots"]
            _save_config(cfg)
            imported_parts.append(f"⭐ Лоты ({len(data['lots'])} шт.)")
        if isinstance(data.get("cards"), list):
            clean_cards = [c for c in (normalize_card_entry(x) for x in data["cards"]) if c]
            _save_cards(clean_cards)
            imported_parts.append(f"💳 Карты ({len(clean_cards)} шт.)")
        if isinstance(data.get("proxies_by_country"), dict):
            _save_proxies_by_country(data["proxies_by_country"])
            total_px = sum(len(v) for v in _load_proxies_by_country().values())
            imported_parts.append(f"🌐 Прокси ({total_px} шт.)")
        if imported_parts:
            return "\n".join(f"• {p}" for p in imported_parts)

    _save_config(data)
    return "• ⚙️ Полный settings.json"

def _maint_cfg_menu_text():
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    cards = _load_cards()
    proxies = _load_proxies()
    return (
        "⚙️ <b>Конфигурация и экспорт данных</b>\n\n"
        f"• Лотов: <b>{len(lots)} шт.</b>\n"
        f"• Карт в базе: <b>{len(cards)} шт.</b>\n"
        f"• Прокси в базе: <b>{len(proxies)} шт.</b>\n\n"
        "Выберите, какие данные скачать (или импортируйте ранее сохранённый <code>.json</code>):"
    )
def _maint_cfg_menu_kb():
    rows = [
        [("📦 Скачать фулл конфиг", "src_maint_export:full")],
        [("⚙️ Данные настроек", "src_maint_export:settings")],
        [("⭐ Данные настройки лотов", "src_maint_export:lots")],
        [("💳 Данные карт", "src_maint_export:cards")],
        [("🌐 Данные прокси", "src_maint_export:proxies")],
        [("💳🌐 Данные карт + прокси", "src_maint_export:cards_proxies")],
        [("📥 Импортировать конфиг (.json)", "src_maint_import_cfg")],
        [("◀️ Назад", "src_cat_maint")],
    ]
    return _make_kb(rows)
def _open_maint_cfg_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _maint_cfg_menu_text()
    kb = _maint_cfg_menu_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
LOTS_PER_PAGE = 5

def _open_lots_menu(chat_id, message_id=None, page: int = 0):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    total_lots = len(lots)
    active_lots = sum(1 for l in lots.values() if l.get("enabled", False))
    text = (
        f"⭐ <b>Настройка лотов {NAME}</b>\n\n"
        f"• Всего лотов в плагине: <b>{total_lots} шт.</b>\n"
        f"• Активных: <b>🟢 {active_lots}</b> | Выключенных: <b>🔴 {total_lots - active_lots}</b>"
    )
    rows: list[list[tuple[str, str]]] = [
        [("🔄 Автодобавление (кат. 2044)", "src_lots_auto_2044"), ("➕ Добавить вручную", "src_lot_add_wizard")],
        [("⚙️ Общая", "src_lots_bulk_menu")],
    ]
    lot_items = list(lots.values())
    total_pages = max(1, (total_lots + LOTS_PER_PAGE - 1) // LOTS_PER_PAGE) if total_lots > 0 else 1
    page = max(0, min(page, total_pages - 1))
    start = page * LOTS_PER_PAGE
    end = start + LOTS_PER_PAGE
    page_lots = lot_items[start:end]
    for l in page_lots:
        lid = str(l["id"])
        st_ico = "🟢" if l.get("enabled", False) else "🔴"
        cc = l.get("country", "KZ")
        px_cnt = len(l.get("proxies") or [])
        cards_cnt = len(l.get("cards") or [])
        lbl = f"{st_ico} #{lid} · {_country_display(cc)} ({px_cnt} px · {cards_cnt} 💳)"
        rows.append([(lbl, f"src_lot_card:{lid}")])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(("◀️ Пред.", f"src_lots_page:{page - 1}"))
        nav.append((f"Стр {page + 1}/{total_pages}", "src_lots_noop"))
        if page < total_pages - 1:
            nav.append(("След. ▶️", f"src_lots_page:{page + 1}"))
        rows.append(nav)
    rows.append([("◀️ Назад", "src_settings_panel")])
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_lots_bulk_menu(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    total_lots = len(lots)
    active_lots = sum(1 for l in lots.values() if l.get("enabled", False))
    inactive_lots = total_lots - active_lots

    if total_lots == 0:
        text = (
            f"⚙️ <b>Общее управление лотами {NAME}</b>\n\n"
            f"В плагине пока нет добавленных лотов."
        )
        rows: list[list[tuple[str, str]]] = [
            [("◀️ Назад к лотам", "src_lots_menu")],
        ]
    else:
        text = (
            f"⚙️ <b>Общее управление лотами {NAME}</b>\n\n"
            f"• Всего лотов в плагине: <b>{total_lots} шт.</b>\n"
            f"• Активных: <b>🟢 {active_lots}</b> | Выключенных: <b>🔴 {inactive_lots}</b>\n\n"
            f"Выберите необходимое действие:"
        )
        rows = []
        if active_lots == 0:
            rows.append([("🟢 Включить все лоты", "src_lots_bulk_toggle:enable")])
        else:
            rows.append([("🔴 Выключить все лоты", "src_lots_bulk_toggle:disable")])
        rows.append([("🗑 Удалить все лоты", "src_lots_bulk_del_ask")])
        rows.append([("◀️ Назад к лотам", "src_lots_menu")])

    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _open_lot_card(chat_id, lot_id: str, message_id=None):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    lot = lots.get(str(lot_id))
    if not lot:
        _tg_send(chat_id, f"⚠️ Лот #{lot_id} не найден.")
        _open_lots_menu(chat_id, message_id)
        return
    title = lot.get("title", f"Лот {lot_id}")
    enabled = lot.get("enabled", True)
    country = lot.get("country", "KZ")
    proxies = lot.get("proxies", [])
    lot_cards = lot.get("cards", [])
    pinfo = get_region_price_info(country)
    matched_card, card_src = _select_card_for_order(lot, country)
    if card_src == "lot":
        card_note = f"{len(lot_cards)} шт. (свои карты лота)"
    elif card_src in ("global_country", "global_any") and matched_card:
        card_note = f"0 своих (подхватит из общей базы: {_mask_card_number(matched_card.get('number', ''))})"
    else:
        card_note = "0 шт. (⚠️ нет подходящих карт в базе)"
    text = (
        f"📦 <b>Настройки лота #{lot_id}</b>\n\n"
        f"• Название: <i>{title}</i>\n"
        f"• Состояние: <b>{'🟢 Включён' if enabled else '🔴 Выключен'}</b>\n"
        f"• Целевая страна: <b>{_country_display(country)}</b> (<code>{country}</code> · {pinfo['currency']} · ~{pinfo['label']})\n"
        f"• Привязано прокси: <b>{len(proxies)} шт.</b>\n"
        f"• Карты: <b>{card_note}</b>"
    )
    rows = [
        [(f"🧩 Состояние: {'🟢 ВКЛ' if enabled else '🔴 ВЫКЛ'}", f"src_lot_tgl:{lot_id}")],
        [(f"🌍 Изменить страну ({country})", f"src_lot_change_cc:{lot_id}:0")],
        [(f"🌐 Прокси лота · {len(proxies)} шт.", f"src_lot_px_menu:{lot_id}"), (f"💳 Карты лота · {len(lot_cards)} шт.", f"src_lot_cards_menu:{lot_id}")],
        [(f"🎮 Найти дешёвую игру ({country})", f"src_lot_search_game:{lot_id}")],
        [("🗑 Удалить лот из плагина", f"src_lot_delete_ask:{lot_id}")],
        [("◀️ К списку лотов", "src_lots_menu")],
    ]
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _lot_cards_menu_text(lot_id: str):
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    lot = lots.get(str(lot_id)) or {}
    title = lot.get("title", f"Лот {lot_id}")
    country = lot.get("country", "KZ")
    lot_cards = lot.get("cards", [])
    preview = []
    for i, c in enumerate(lot_cards[:6], 1):
        preview.append(f"{i}. {_format_card_short(c)}")
    cards_preview = "\n".join(preview) if preview else "<i>(своих карт у лота нет — при заказе будет подбираться карта из общей базы по региону)</i>"
    if len(lot_cards) > 6:
        cards_preview += f"\n<i>... и ещё {len(lot_cards) - 6} карт</i>"
    return (
        f"💳 <b>Настройка карт лота #{lot_id}</b>\n\n"
        f"• Лот: <i>{title}</i>\n"
        f"• Страна: <b>{_country_display(country)}</b> (<code>{country}</code>)\n"
        f"• Привязано карт к лоту: <b>{len(lot_cards)} шт.</b>\n\n"
        f"<b>Карты лота:</b>\n{cards_preview}\n\n"
        "💡 <i>Карты, привязанные к лоту, имеют приоритет над общей базой карт.</i>"
    )
def _lot_cards_menu_kb(lot_id: str):
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    lot = lots.get(str(lot_id)) or {}
    cc = lot.get("country", "KZ")
    lot_cards = lot.get("cards", [])
    db_cc_cards = _get_cards_for_country(cc, include_any=False)
    rows = [
        [(f"📥 Выбрать из базы ({cc}) · {len(db_cc_cards)} шт.", f"src_lot_pick_cards:{lot_id}")],
        [("➕ Добавить карты вручную (текст / .txt)", f"src_lot_add_cards:{lot_id}")],
    ]
    if lot_cards:
        rows.append([("💾 Экспорт (.txt)", f"src_lot_export_cards:{lot_id}"), ("🗑 Очистить карты", f"src_lot_clear_cards:{lot_id}")])
    rows.append([("💳 Открыть общую базу карт", "src_cards")])
    rows.append([("◀️ Назад к лоту", f"src_lot_card:{lot_id}")])
    return _make_kb(rows)
def _open_lot_cards_menu(chat_id, lot_id: str, message_id=None):
    _waiting.pop(chat_id, None)
    text = _lot_cards_menu_text(lot_id)
    kb = _lot_cards_menu_kb(lot_id)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_lot_pick_cards(chat_id, lot_id: str, message_id=None, page: int = 0):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    lot = lots.get(str(lot_id)) or {}
    cc = lot.get("country", "KZ")
    lot_cards = lot.get("cards", [])
    existing_keys = {(c.get("number"), c.get("exp_month"), c.get("exp_year"), c.get("cvv")) for c in lot_cards}
    db_cards = _get_cards_for_country(cc, include_any=False)
    if not db_cards:
        text = (
            f"💳 <b>Выбор карт для лота #{lot_id} ({_country_display(cc)})</b>\n\n"
            f"В общей базе пока нет карт страны <b>{_country_display(cc)}</b> (<code>{cc}</code>).\n"
            "<i>Показываются только карты той же страны, что и лот.</i>"
        )
        kb = _make_kb([
            [(f"➕ Добавить карту ({cc}) в базу", f"src_cards_add_cc:{cc}")],
            [("◀️ К картам лота", f"src_lot_cards_menu:{lot_id}")],
        ])
        _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
        return
    total_pages = max(1, (len(db_cards) + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = db_cards[page * CARDS_PER_PAGE : (page + 1) * CARDS_PER_PAGE]
    text = (
        f"💳 <b>Выбор карт из базы для лота #{lot_id}</b>\n\n"
        f"• Страна лота: <b>{_country_display(cc)}</b> (<code>{cc}</code>)\n"
        f"• Найдено карт этой страны: <b>{len(db_cards)} шт.</b>\n\n"
        "Нажмите на карту, чтобы привязать её к лоту (или привяжите все сразу):"
    )
    rows: list[list[tuple[str, str]]] = [
        [(f"✅ Привязать все карты {cc} ({len(db_cards)} шт.)", f"src_lot_attach_all_cards:{lot_id}")],
    ]
    for c in chunk:
        key = (c.get("number"), c.get("exp_month"), c.get("exp_year"), c.get("cvv"))
        attached = "✅ " if key in existing_keys else "➕ "
        masked = _mask_card_number(c.get("number", ""))
        pm = str(c.get("payment_method") or "visa").upper()[:4]
        exp = f"{c.get('exp_month', '??')}/{str(c.get('exp_year', '??'))[-2:]}"
        rows.append([(f"{attached}{pm} {masked} ({exp})", f"src_lot_attach_card:{lot_id}:{c['id']}")])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(("◀️ Пред.", f"src_lot_pick_cards_p:{lot_id}:{page - 1}"))
        nav.append((f"Стр {page + 1}/{total_pages}", "src_lots_noop"))
        if page < total_pages - 1:
            nav.append(("След. ▶️", f"src_lot_pick_cards_p:{lot_id}:{page + 1}"))
        rows.append(nav)
    rows.append([("◀️ К картам лота", f"src_lot_cards_menu:{lot_id}")])
    _tg_edit(chat_id, message_id, text, _make_kb(rows)) if message_id else _tg_send(chat_id, text, _make_kb(rows))

def _lot_proxies_menu_text(lot_id: str):
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    lot = lots.get(str(lot_id)) or {}
    title = lot.get("title", f"Лот {lot_id}")
    country = lot.get("country", "KZ")
    proxies = lot.get("proxies", [])
    preview = []
    for i, p in enumerate(proxies[:5], 1):
        clean_p = re.sub(r':([^@:]+)@', ':***@', p)
        preview.append(f"{i}. <code>{clean_p}</code>")
    px_preview = "\n".join(preview) if preview else "<i>(нет привязанных прокси — добавьте для работы смены региона!)</i>"
    if len(proxies) > 5:
        px_preview += f"\n<i>... и ещё {len(proxies) - 5} прокси</i>"
    return (
        f"🌐 <b>Настройка прокси лота #{lot_id}</b>\n\n"
        f"• Лот: <i>{title}</i>\n"
        f"• Страна: <b>{_country_display(country)}</b> (<code>{country}</code>)\n"
        f"• Всего прокси: <b>{len(proxies)} шт.</b>\n\n"
        f"<b>Текущие прокси:</b>\n{px_preview}"
    )
def _lot_proxies_menu_kb(lot_id: str):
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    lot = lots.get(str(lot_id)) or {}
    cc = lot.get("country", "KZ")
    proxies = lot.get("proxies", [])
    db_cc_px = _get_proxies_for_country(cc, include_any=False)
    rows = [
        [(f"📥 Выбрать из базы ({cc}) · {len(db_cc_px)} шт.", f"src_lot_pick_px:{lot_id}")],
        [("➕ Добавить прокси вручную (текст / .txt)", f"src_lot_add_px:{lot_id}")],
    ]
    if proxies:
        rows.append([("🧪 Тестировать прокси", f"src_lot_test_px:{lot_id}"), ("🗑 Очистить прокси", f"src_lot_clear_px:{lot_id}")])
    rows.append([("◀️ Назад к лоту", f"src_lot_card:{lot_id}")])
    return _make_kb(rows)
def _open_lot_proxies_menu(chat_id, lot_id: str, message_id=None):
    _waiting.pop(chat_id, None)
    text = _lot_proxies_menu_text(lot_id)
    kb = _lot_proxies_menu_kb(lot_id)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)

def _open_lot_pick_proxies(chat_id, lot_id: str, message_id=None, page: int = 0):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    lot = lots.get(str(lot_id)) or {}
    cc = lot.get("country", "KZ")
    lot_px = set(lot.get("proxies", []))
    db_px = _get_proxies_for_country(cc, include_any=False)
    if not db_px:
        text = (
            f"🌐 <b>Выбор прокси для лота #{lot_id} ({_country_display(cc)})</b>\n\n"
            f"В общей базе пока нет прокси страны <b>{_country_display(cc)}</b> (<code>{cc}</code>).\n"
            "<i>Показываются только прокси той же страны, что и лот.</i>"
        )
        kb = _make_kb([
            [(f"➕ Добавить прокси ({cc}) в базу", f"src_proxies_add_cc:{cc}")],
            [("◀️ К прокси лота", f"src_lot_px_menu:{lot_id}")],
        ])
        _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
        return
    total_pages = max(1, (len(db_px) + PROXIES_PER_PAGE - 1) // PROXIES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * PROXIES_PER_PAGE
    end = start + PROXIES_PER_PAGE
    text = (
        f"🌐 <b>Выбор прокси из базы для лота #{lot_id}</b>\n\n"
        f"• Страна лота: <b>{_country_display(cc)}</b> (<code>{cc}</code>)\n"
        f"• Найдено прокси этой страны: <b>{len(db_px)} шт.</b>\n\n"
        "Нажмите на прокси для привязки или привяжите все сразу:"
    )
    rows: list[list[tuple[str, str]]] = [
        [(f"✅ Привязать все прокси {cc} ({len(db_px)} шт.)", f"src_lot_attach_all_px:{lot_id}")],
    ]
    for idx in range(start, min(end, len(db_px))):
        p = db_px[idx]
        attached = "✅ " if p in lot_px else "➕ "
        clean_p = re.sub(r":([^@:]+)@", ":***@", p)
        rows.append([(f"{attached}{clean_p[:38]}", f"src_lot_attach_px:{lot_id}:{idx}")])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(("◀️ Пред.", f"src_lot_pick_px_p:{lot_id}:{page - 1}"))
        nav.append((f"Стр {page + 1}/{total_pages}", "src_lots_noop"))
        if page < total_pages - 1:
            nav.append(("След. ▶️", f"src_lot_pick_px_p:{lot_id}:{page + 1}"))
        rows.append(nav)
    rows.append([("◀️ К прокси лота", f"src_lot_px_menu:{lot_id}")])
    _tg_edit(chat_id, message_id, text, _make_kb(rows)) if message_id else _tg_send(chat_id, text, _make_kb(rows))
def _open_lot_change_cc(chat_id, lot_id: str, message_id=None, page: int = 0):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    lot = lots.get(str(lot_id)) or {}
    cur_cc = lot.get("country", "KZ")
    text = (
        f"🌍 <b>Выбор страны для лота #{lot_id}</b>\n\n"
        f"Текущая страна: <b>{_country_display(cur_cc)}</b> (<code>{cur_cc}</code>)\n\n"
        "Выберите страну из таблицы (39 регионов) или введите двухбуквенный код текстом:"
    )
    rows = _build_region_picker_rows(
        cb_prefix=f"src_lot_set_cc:{lot_id}",
        page=page,
        page_cb_prefix=f"src_lot_change_cc:{lot_id}",
        include_any=False,
        extra_rows=[
            [("✍️ Ввести свой код", f"src_lot_input_cc:{lot_id}")],
            [("◀️ Назад", f"src_lot_card:{lot_id}")],
        ],
    )
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _test_lot_proxies_worker(chat_id, lot_id: str, message_id=None):
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    lot = lots.get(str(lot_id)) or {}
    proxies = lot.get("proxies", [])
    if not proxies:
        _tg_edit(chat_id, message_id, f"⚠️ У лота #{lot_id} нет привязанных прокси.", _make_kb([[("◀️ Назад", f"src_lot_px_menu:{lot_id}")]]))
        return
    _tg_edit(chat_id, message_id, f"⏳ <b>Тестирование {len(proxies)} прокси лота #{lot_id}...</b>\nПожалуйста, подождите.", _make_kb([[("◀️ Назад", f"src_lot_px_menu:{lot_id}")]]))
    _log_event("lot_proxy_test_start", lot_id=lot_id, count=len(proxies))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def _test():
        details = []
        alive = 0
        dead = 0
        sem = asyncio.Semaphore(5)
        async def _check(p):
            nonlocal alive, dead
            clean_p = _mask_proxy(p)
            async with sem:
                ok, diag = await check_proxy(p, timeout=8.0)
                if ok:
                    alive += 1
                    details.append(f"🟢 <code>{clean_p}</code> → {diag} (Steam OK)")
                    _log_event("lot_proxy_test", status="ok", lot_id=lot_id, proxy=clean_p, diag=diag)
                else:
                    dead += 1
                    details.append(f"🔴 <code>{clean_p}</code> → {diag}")
                    _log_event("lot_proxy_test", level=logging.WARNING, status="fail", lot_id=lot_id, proxy=clean_p, diag=diag)
        await asyncio.gather(*[_check(p) for p in proxies])
        return alive, dead, details
    try:
        alive, dead, details = loop.run_until_complete(_test())
        _log_event("lot_proxy_summary", lot_id=lot_id, total=len(proxies), alive=alive, dead=dead)
        det_str = "\n".join(details[:10])
        if len(details) > 10:
            det_str += f"\n<i>... и ещё {len(details) - 10} прокси</i>"
        res_text = (
            f"🧪 <b>Результаты тестирования прокси лота #{lot_id}:</b>\n\n"
            f"• Всего проверено: <b>{len(proxies)}</b>\n"
            f"• Живые (Steam доступен): <b>🟢 {alive}</b>\n"
            f"• Недоступны / ошибки: <b>🔴 {dead}</b>\n\n"
            f"<b>Статус по прокси:</b>\n{det_str}"
        )
    except Exception as e:
        _log_event("lot_proxy_test_error", level=logging.ERROR, lot_id=lot_id, err=str(e))
        res_text = f"❌ Ошибка проверки: {e}"
    finally:
        loop.close()
    _tg_edit(chat_id, message_id, res_text, _make_kb([[("🔄 Проверить снова", f"src_lot_test_px:{lot_id}"), ("◀️ К прокси лота", f"src_lot_px_menu:{lot_id}")]]))
def _handle_auto_lots_2044(chat_id, message_id=None):
    if not cardinal_instance or not getattr(cardinal_instance, 'account', None):
        _tg_edit(chat_id, message_id, "⚠️ Аккаунт FunPay в Cardinal не инициализирован. Убедитесь, что Cardinal запущен и авторизован в FunPay.", _make_kb([[("◀️ Назад", "src_lots_menu")]]))
        return
    _tg_edit(chat_id, message_id, "⏳ <b>Поиск ваших лотов в категории 2044...</b>", _make_kb([[("◀️ Назад", "src_lots_menu")]]))
    def _worker():
        try:
            account = cardinal_instance.account
            found_lots = []
            try:
                found_lots = account.get_my_subcategory_lots(2044)
            except Exception as e:
                logger.warning(f"[{NAME}] get_my_subcategory_lots(2044): {e}")
            if not found_lots and getattr(account, 'id', None):
                try:
                    profile = account.get_user(int(account.id))
                    if profile:
                        for lot in profile.get_lots():
                            sub = getattr(lot, 'subcategory', None)
                            if sub and getattr(sub, 'id', None) == 2044:
                                found_lots.append(lot)
                except Exception as e:
                    logger.warning(f"[{NAME}] get_user lots fallback: {e}")
            if not found_lots:
                _tg_edit(chat_id, message_id, "ℹ️ В категории 2044 не найдено ваших лотов. Создайте лот на FunPay или добавьте лот вручную по ID.", _make_kb([[("➕ Добавить вручную", "src_lot_add_wizard"), ("◀️ Назад", "src_lots_menu")]]))
                return
            cfg = _load_config()
            lots = _get_lots_dict(cfg)
            added_cnt = 0
            already_cnt = 0
            for l in found_lots:
                lid = str(getattr(l, 'id', '') or '')
                if not lid:
                    continue
                if lid in lots:
                    already_cnt += 1
                    continue
                title = str(getattr(l, 'description', None) or getattr(l, 'title', None) or f"Лот {lid}")
                t_low = title.lower()
                cc = "KZ"
                _cc_rules = [
                    ("UA", ["украин", "гривн", " ua", "(ua)"]),
                    ("KZ", ["казахстан", "тенге", " kz", "(kz)"]),
                    ("TR", ["турци", "лир", " tr", "(tr)"]),
                    ("RU", ["росси", "рубл", " ru", "(ru)"]),
                    ("US", ["сша", "доллар", " us", "(us)", "usa"]),
                    ("AR", ["аргентин", "песо", " ar", "(ar)"]),
                    ("BR", ["бразили", "реал", " br", "(br)"]),
                    ("IN", ["инди", "руп", " in", "(in)"]),
                    ("CN", ["кита", "юан", " cn", "(cn)"]),
                    ("ID", ["индонези", " id", "(id)"]),
                    ("VN", ["вьетнам", " vn", "(vn)"]),
                    ("PH", ["филиппин", " ph", "(ph)"]),
                    ("MY", ["малайзи", " my", "(my)"]),
                    ("TH", ["таиланд", " th", "(th)"]),
                    ("JP", ["япони", "иен", " jp", "(jp)"]),
                    ("KR", ["коре", " kr", "(kr)"]),
                    ("TW", ["тайван", " tw", "(tw)"]),
                    ("HK", ["гонконг", " hk", "(hk)"]),
                    ("SG", ["сингапур", " sg", "(sg)"]),
                    ("CA", ["канад", " ca", "(ca)"]),
                    ("AU", ["австрали", " au", "(au)"]),
                    ("NZ", ["зеланди", " nz", "(nz)"]),
                    ("GB", ["великобритани", "англи", "фунт", " gb", "(gb)", " uk"]),
                    ("EU", ["еврозон", "европ", "германи", " eu", "(eu)", " de", "(de)"]),
                    ("CH", ["швейцари", " ch", "(ch)"]),
                    ("PL", ["польш", "злот", " pl", "(pl)"]),
                    ("NO", ["норвеги", " no", "(no)"]),
                    ("MX", ["мексик", " mx", "(mx)"]),
                    ("CL", ["чили", " cl", "(cl)"]),
                    ("CO", ["колумби", " co", "(co)"]),
                    ("PE", ["перу", " pe", "(pe)"]),
                    ("UY", ["уругва", " uy", "(uy)"]),
                    ("CR", ["коста", " cr", "(cr)"]),
                    ("ZA", ["юар", " za", "(za)"]),
                    ("SA", ["саудов", " sa", "(sa)"]),
                    ("AE", ["оаэ", "эмират", " ae", "(ae)"]),
                    ("QA", ["катар", " qa", "(qa)"]),
                    ("KW", ["кувейт", " kw", "(kw)"]),
                    ("IL", ["израил", "шекел", " il", "(il)"]),
                ]
                for code_cand, kws in _cc_rules:
                    if any(k in t_low for k in kws):
                        cc = code_cand
                        break
                lots[lid] = {
                    "id": lid,
                    "title": title,
                    "enabled": False,
                    "country": cc,
                    "proxies": [],
                    "cards": [],
                }
                added_cnt += 1
            cfg["lots"] = lots
            _save_config(cfg)
            msg = (
                f"✅ <b>Автодобавление лотов (кат. 2044) завершено!</b>\n\n"
                f"• Найдено лотов: <b>{len(found_lots)} шт.</b>\n"
                f"• Добавлено новых: <b>{added_cnt} шт.</b>\n"
                f"• Уже было в плагине: <b>{already_cnt} шт.</b>"
            )
            _tg_edit(chat_id, message_id, msg, _make_kb([[("⭐ Открыть список лотов", "src_lots_menu")]]))
        except Exception as e:
            logger.error(f"[{NAME}] Ошибка автодобавления лотов: {e}", exc_info=True)
            _tg_edit(chat_id, message_id, f"❌ Ошибка сканирования: {e}", _make_kb([[("◀️ Назад", "src_lots_menu")]]))
    threading.Thread(target=_worker, daemon=True).start()
def _start_lot_wizard(chat_id, message_id=None):
    _waiting[chat_id] = {"action": "wiz_step1_id", "prompt_id": message_id}
    text = (
        "➕ <b>Добавление лота · Шаг 1 из 4</b>\n\n"
        "Введите числовой ID лота FunPay (например: <code>12345678</code>):"
    )
    kb = _make_kb([[("❌ Отмена", "src_lots_menu")]])
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _lot_wizard_ask_country(chat_id, lot_id: str, title: str, message_id=None, page: int = 0):
    _waiting[chat_id] = {"action": "wiz_step2_country", "lot_id": lot_id, "title": title, "prompt_id": message_id}
    text = (
        f"🌍 <b>Добавление лота · Шаг 2 из 4</b>\n\n"
        f"Лот: <code>{lot_id}</code>\n"
        f"Название: <i>{title}</i>\n\n"
        "Выберите целевую страну (39 регионов) или отправьте её 2-буквенный код текстом (например: <code>UA</code>):"
    )
    rows = _build_region_picker_rows(
        cb_prefix="src_wiz_cc",
        page=page,
        page_cb_prefix="src_wiz_cc_page",
        include_any=False,
        extra_rows=[[("❌ Отмена", "src_lots_menu")]],
    )
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _lot_wizard_ask_proxy(chat_id, lot_id: str, title: str, cc: str, message_id=None):
    _waiting[chat_id] = {"action": "wiz_step3_proxy", "lot_id": lot_id, "title": title, "country": cc, "prompt_id": message_id}
    db_cc_px = _get_proxies_for_country(cc, include_any=False)
    db_hint = (
        f"В общей базе найдено <b>{len(db_cc_px)} шт.</b> прокси для {_country_display(cc)} — можете привязать их кнопкой ниже.\n\n"
        if db_cc_px
        else ""
    )
    text = (
        f"🌐 <b>Добавление лота · Шаг 3 из 4</b>\n\n"
        f"Лот: <code>{lot_id}</code> | Страна: <b>{_country_display(cc)}</b>\n\n"
        f"{db_hint}"
        f"Отправьте прокси текстом (каждый с новой строки) или загрузите файл <code>.txt</code>:\n\n"
        "<b>Поддерживаемые форматы (HTTP/HTTPS/SOCKS5):</b>\n"
        "• <code>ip:port</code>\n"
        "• <code>ip:port:login:password</code>\n"
        "• <code>http://login:password@ip:port</code>\n"
        "• <code>socks5://login:password@ip:port</code>"
    )
    rows: list[list[tuple[str, str]]] = []
    if db_cc_px:
        rows.append([(f"📥 Взять прокси из базы ({cc}) · {len(db_cc_px)} шт.", "src_wiz_use_db_px")])
    rows.append([("❌ Отмена", "src_lots_menu")])
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _lot_wizard_ask_card(chat_id, lot_id: str, title: str, cc: str, proxies: list[str], message_id=None, page: int = 0):
    _waiting[chat_id] = {
        "action": "wiz_step4_card",
        "lot_id": lot_id,
        "title": title,
        "country": cc,
        "proxies": list(proxies),
        "prompt_id": message_id,
    }
    db_cc_cards = _get_cards_for_country(cc, include_any=False)
    db_hint = (
        f"В общей базе найдено <b>{len(db_cc_cards)} шт.</b> карт для {_country_display(cc)} — можете выбрать карту или привязать все кнопками ниже.\n\n"
        if db_cc_cards
        else f"В общей базе пока нет карт для {_country_display(cc)} — отправьте карту вручную или пропустите шаг.\n\n"
    )
    text = (
        f"💳 <b>Добавление лота · Шаг 4 из 4</b>\n\n"
        f"Лот: <code>{lot_id}</code> | Страна: <b>{_country_display(cc)}</b>\n"
        f"Прокси: <b>{len(proxies)} шт.</b>\n\n"
        f"{db_hint}"
        "Отправьте карту(ы) текстом (каждая с новой строки) или загрузите файл <code>.txt</code>:\n"
        "• <code>номер:ММ/ГГ:CVV</code> (например: <code>4111222233334444:05/28:123</code>)"
    )
    rows: list[list[tuple[str, str]]] = []
    if db_cc_cards:
        rows.append([(f"📥 Взять все карты из базы ({cc}) · {len(db_cc_cards)} шт.", "src_wiz_use_db_cards")])
        total_pages = max(1, (len(db_cc_cards) + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))
        chunk = db_cc_cards[page * CARDS_PER_PAGE : (page + 1) * CARDS_PER_PAGE]
        for c in chunk:
            masked = _mask_card_number(c.get("number", ""))
            pm = str(c.get("payment_method") or "visa").upper()[:4]
            exp = f"{c.get('exp_month', '??')}/{str(c.get('exp_year', '??'))[-2:]}"
            rows.append([(f"💳 {pm} {masked} ({exp})", f"src_wiz_pick_card:{c['id']}")])
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(("◀️ Пред.", f"src_wiz_card_page:{page - 1}"))
            nav.append((f"Стр {page + 1}/{total_pages}", "src_lots_noop"))
            if page < total_pages - 1:
                nav.append(("След. ▶️", f"src_wiz_card_page:{page + 1}"))
            rows.append(nav)
    rows.append([("⏭ Пропустить (брать из общей базы)", "src_wiz_skip_cards")])
    rows.append([("❌ Отмена", "src_lots_menu")])
    kb = _make_kb(rows)
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _lot_wizard_finish(
    chat_id,
    lot_id: str,
    title: str,
    cc: str,
    proxies: list[str],
    cards: list[dict[str, Any]],
    message_id=None,
    invalid_cnt: int = 0,
):
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    lots[lot_id] = {
        "id": lot_id,
        "title": title,
        "enabled": True,
        "country": cc,
        "proxies": list(proxies),
        "cards": [dict(c) for c in (cards or [])],
    }
    cfg["lots"] = lots
    _save_config(cfg)
    _waiting.pop(chat_id, None)
    cards_line = (
        f"<b>{len(cards)} шт.</b> (привязаны к лоту)"
        if cards
        else f"из общей базы (<code>{cc}</code>)"
    )
    warn_line = f"\n⚠️ Пропущено строк карт с ошибкой формата: {invalid_cnt}" if invalid_cnt else ""
    msg = (
        f"✅ <b>Лот #{lot_id} успешно настроен и добавлен!</b>\n\n"
        f"• Название: <i>{html.escape(str(title))}</i>\n"
        f"• Целевая страна: <b>{_country_display(cc)}</b> (<code>{cc}</code>)\n"
        f"• Привязано прокси: <b>{len(proxies)} шт.</b>\n"
        f"• Карты для оплаты: {cards_line}\n"
        f"• Состояние: <b>🟢 Включён</b>{warn_line}\n\n"
        "Теперь при покупке этого лота плагин сразу начнёт автоматическую смену региона."
    )
    kb = _make_kb([
        [("⚙️ Настройки этого лота", f"src_lot_card:{lot_id}"), ("⭐ К списку лотов", "src_lots_menu")],
    ])
    _tg_edit(chat_id, message_id, msg, kb) if message_id else _tg_send(chat_id, msg, kb)
def _test_proxies_worker(chat_id, message_id=None):
    proxies = _load_proxies()
    if not proxies:
        _tg_edit(chat_id, message_id, "⚠️ Список прокси пуст. Добавьте прокси для проверки.", _make_kb([[("◀️ Назад", "src_proxies")]]))
        return
    _tg_edit(chat_id, message_id, f"⏳ <b>Тестирование {len(proxies)} прокси...</b>\nПожалуйста, подождите.", _make_kb([[("◀️ Назад", "src_proxies")]]))
    _log_event("pool_proxy_test_start", count=len(proxies))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def _test_all():
        details = []
        alive = 0
        dead = 0
        sem = asyncio.Semaphore(10)
        async def _check(p):
            nonlocal alive, dead
            clean_p = _mask_proxy(p)
            async with sem:
                ok, diag = await check_proxy(p, timeout=8.0)
                if ok:
                    alive += 1
                    details.append(f"🟢 <code>{clean_p}</code> → {diag} (Steam OK)")
                    _log_event("pool_proxy_test", status="ok", proxy=clean_p, diag=diag)
                else:
                    dead += 1
                    details.append(f"🔴 <code>{clean_p}</code> → {diag}")
                    _log_event("pool_proxy_test", level=logging.WARNING, status="fail", proxy=clean_p, diag=diag)
        await asyncio.gather(*[_check(p) for p in proxies])
        return alive, dead, details
    try:
        alive, dead, details = loop.run_until_complete(_test_all())
        _log_event("pool_proxy_summary", total=len(proxies), alive=alive, dead=dead)
        det_str = "\n".join(details[:10])
        if len(details) > 10:
            det_str += f"\n<i>... и ещё {len(details) - 10} прокси</i>"
        res_text = (
            f"🧪 <b>Результаты тестирования общей базы:</b>\n\n"
            f"• Проверено: <b>{len(proxies)}</b>\n"
            f"• Живые (Steam доступен): <b>🟢 {alive}</b>\n"
            f"• Недоступны / таймаут: <b>🔴 {dead}</b>\n\n"
            f"<b>Статус по прокси:</b>\n{det_str}"
        )
    except Exception as e:
        _log_event("pool_proxy_test_error", level=logging.ERROR, err=str(e))
        res_text = f"❌ Ошибка проверки прокси: {e}"
    finally:
        loop.close()
    kb = _make_kb([[("🔄 Проверить снова", "src_test_proxies"), ("◀️ К прокси", "src_proxies")]])
    _tg_edit(chat_id, message_id, res_text, kb)
def _stats_text():
    stats = _load_stats()
    return (
        f"📊 <b>Статистика {NAME}</b>\n\n"
        f"• Всего попыток: <b>{stats.get('total_operations', 0)}</b>\n"
        f"• Подготовлено корзин: <b>🛒 {stats.get('carts_prepared', 0)}</b>\n"
        f"• Успешных смен: <b>🟢 {stats.get('success', 0)}</b>\n"
        f"• Ошибок / отмен: <b>🔴 {stats.get('failed', 0)}</b>\n"
        f"• Последняя операция: <code>{stats.get('last_operation', 'нет данных')}</code>"
    )
def _stats_kb():
    return _make_kb([
        [("🔄 Сбросить статистику", "src_stats_reset")],
        [("◀️ Назад", "src_settings_panel")],
    ])
def _open_stats(chat_id, message_id=None):
    _waiting.pop(chat_id, None)
    text = _stats_text()
    kb = _stats_kb()
    _tg_edit(chat_id, message_id, text, kb) if message_id else _tg_send(chat_id, text, kb)
def _plugin_version_key(value):
    nums = [int(x) for x in re.findall(r'\d+', str(value or ''))[:4]]
    nums.extend([0] * (4 - len(nums)))
    return tuple(nums[:4])
def _online_update_worker(chat_id, message_id=None):
    _tg_edit(chat_id, message_id, "⏳ <b>Проверка обновлений на GitHub...</b>", _make_kb([[("◀️ Назад", "src_update_menu")]]))
    try:
        candidate_urls = [
            GITHUB_UPDATE_URL,
            "https://raw.githubusercontent.com/tinechelovec/FPC-Steam-Region-Changer-v2/main/SRCv2.py",
            "https://raw.githubusercontent.com/tinechelovec/FPC-Steam-Region-Changer-v2/main/SRC-Plugin-v2.py",
            "https://raw.githubusercontent.com/tinechelovec/FPC-Steam-Region-Changer-v2/main/SRC-Plugin.py",
            "https://raw.githubusercontent.com/tinechelovec/FPC-Steam-Region-Changer-v2/master/SRCv2.py",
        ]
        resp = None
        for url_try in dict.fromkeys(candidate_urls):
            if not url_try:
                continue
            r = requests.get(url_try, timeout=15)
            if r.status_code == 200 and r.content:
                resp = r
                break
            resp = r
        if resp is None or resp.status_code != 200 or not resp.content:
            code_st = resp.status_code if resp is not None else "—"
            _tg_edit(chat_id, message_id, f"⚠️ Не удалось загрузить обновление с GitHub (код {code_st}).\nПроверьте репозиторий: {GITHUB_URL}", _make_kb([[("◀️ Назад", "src_update_menu")]]))
            return
        source = resp.text
        m = re.search(r'(?m)^\s*VERSION\s*=\s*["\']([^"\']+)["\']', source)
        if not m:
            _tg_edit(chat_id, message_id, "❌ В файле на GitHub не найдено поле VERSION.", _make_kb([[("◀️ Назад", "src_update_menu")]]))
            return
        remote_version = m.group(1).strip()
        if _plugin_version_key(remote_version) <= _plugin_version_key(VERSION):
            _tg_edit(chat_id, message_id, f"✅ <b>У вас уже установлена актуальная версия {VERSION}.</b>", _make_kb([[("◀️ Назад", "src_update_menu")]]))
            return
        plugin_file = os.path.abspath(__file__)
        compile(source, plugin_file, "exec")
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup_file = plugin_file + f'.pre-online-update.{stamp}.bak'
        shutil.copy2(plugin_file, backup_file)
        with open(plugin_file, "w", encoding="utf-8") as f:
            f.write(source)
        text = f"✅ <b>Онлайн-обновление установлено: v{remote_version}!</b>\n\nРезервная копия сохранена в <code>{os.path.basename(backup_file)}</code>.\nВыполните <code>/restart</code> для применения."
        _tg_edit(chat_id, message_id, text, _make_kb([[("◀️ В меню", "src_home")]]))
    except Exception as e:
        _tg_edit(chat_id, message_id, f"❌ Ошибка обновления: {e}", _make_kb([[("◀️ Назад", "src_update_menu")]]))
def _self_delete(chat_id, message_id=None):
    errors = []
    try:
        shutil.rmtree(STORAGE_DIR, ignore_errors=True)
    except Exception as e:
        errors.append(f"данные: {e}")
    plugin_file = os.path.abspath(__file__)
    try:
        if os.path.exists(plugin_file):
            os.remove(plugin_file)
    except Exception as e:
        errors.append(f"файл: {e}")
    if not errors:
        text = f"✅ <b>Плагин {NAME} успешно удалён.</b>\n\nВыполните команду <code>/restart</code>."
    else:
        text = f"⚠️ <b>Плагин удалён с замечаниями:</b>\n" + "\n".join(errors)
    _tg_edit(chat_id, message_id, text, _make_kb([[("🔙 К списку плагинов", CB_PLUGINS_LIST_OPEN)]]))
def _cb_router(call):
    chat_id = getattr(getattr(getattr(call, 'message', None), 'chat', None), 'id', None)
    message_id = getattr(getattr(call, 'message', None), 'message_id', None)
    data = str(getattr(call, 'data', '') or '')
    from_user = getattr(call, 'from_user', None)
    user_id = getattr(from_user, 'id', None)
    if not chat_id or not _is_authorized(user_id):
        return
    try:
        bot_instance.answer_callback_query(call.id)
    except Exception:
        pass
    if data == "src_home":
        _open_home(chat_id, message_id)
    elif data == "src_info":
        _open_info(chat_id, message_id)
    elif data == "src_update_menu":
        _open_update_menu(chat_id, message_id)
    elif data == "src_settings_panel":
        _open_settings_panel(chat_id, message_id, check_intro=True)
    elif data == "src_instruction_confirm":
        cfg = _load_config()
        cfg["instruction_read"] = True
        _save_config(cfg)
        _open_settings_panel(chat_id, message_id, check_intro=False)
    elif data == "src_delete_ask":
        _open_delete_confirm(chat_id, message_id)
    elif data == "src_delete_yes":
        _self_delete(chat_id, message_id)
    elif data == "src_update_local":
        _waiting[chat_id] = {"action": "update_local", "prompt_id": message_id}
        text = (
            "📥 <b>Локальное обновление</b>\n\n"
            f"Пришлите новый файл <code>SRC-Plugin.py</code> с расширением <code>.py</code> в этот чат.\n"
            "После проверки файл заменит текущий плагин. Старый файл будет сохранён в резервной копии.\n\n"
            "Для отмены нажмите кнопку ниже."
        )
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_update_menu")]]))
    elif data == "src_update_online":
        threading.Thread(target=_online_update_worker, args=(chat_id, message_id), daemon=True).start()
    elif data == "src_mini_settings":
        _open_mini_settings(chat_id, message_id)
    elif data == "src_lots_menu":
        _open_lots_menu(chat_id, message_id)
    elif data.startswith("src_lots_page:"):
        p = int(data.split(":", 1)[1])
        _open_lots_menu(chat_id, message_id, page=p)
    elif data == "src_lots_noop":
        pass
    elif data == "src_stats":
        _open_stats(chat_id, message_id)
    elif data == "src_tgl_state":
        cfg = _load_config()
        cfg["plugin_enabled"] = not cfg.get("plugin_enabled", True)
        _save_config(cfg)
        _open_mini_settings(chat_id, message_id)
    elif data == "src_cat_orders":
        _open_orders_menu(chat_id, message_id)
    elif data in ("src_cycle_qr_mode", "src_tgl_login_by_qr"):
        cfg = _load_config()
        curr_mode = _get_qr_mode(cfg)
        modes = ["choice", "qr_only", "off"]
        idx = modes.index(curr_mode) if curr_mode in modes else 0
        new_mode = modes[(idx + 1) % len(modes)]
        cfg["qr_login_mode"] = new_mode
        cfg["login_by_qr"] = (new_mode != "off")
        _save_config(cfg)
        _open_orders_menu(chat_id, message_id)
    elif data == "src_tgl_auto_checkout":
        cfg = _load_config()
        cfg["auto_checkout_card"] = not cfg.get("auto_checkout_card", True)
        _save_config(cfg)
        _open_orders_menu(chat_id, message_id)
    elif data == "src_card_rules_menu":
        _open_card_rules_menu(chat_id, message_id)
    elif data == "src_tgl_auto_switch_card":
        cfg = _load_config()
        cfg["auto_switch_card"] = not cfg.get("auto_switch_card", True)
        _save_config(cfg)
        _open_card_rules_menu(chat_id, message_id)
    elif data == "src_tgl_disable_failed_card":
        cfg = _load_config()
        cfg["disable_failed_card"] = not cfg.get("disable_failed_card", True)
        _save_config(cfg)
        _open_card_rules_menu(chat_id, message_id)
    elif data == "src_tgl_disable_used_card":
        cfg = _load_config()
        cfg["disable_used_card"] = not cfg.get("disable_used_card", False)
        _save_config(cfg)
        _open_card_rules_menu(chat_id, message_id)
    elif data == "src_tgl_refund":
        cfg = _load_config()
        cfg["auto_refund_enabled"] = not cfg.get("auto_refund_enabled", True)
        _save_config(cfg)
        _open_orders_menu(chat_id, message_id)
    elif data == "src_cat_refunds":
        _open_refunds_menu(chat_id, message_id)
    elif data == "src_tgl_refund_master":
        cfg = _load_config()
        cfg["auto_refund_enabled"] = not cfg.get("auto_refund_enabled", True)
        _save_config(cfg)
        _open_refunds_menu(chat_id, message_id)
    elif data.startswith("src_tgl_ref_reason:"):
        code = data.split(":", 1)[1]
        cfg = _load_config()
        reasons = cfg.get("refund_reasons")
        if not isinstance(reasons, dict):
            reasons = dict(DEFAULT_REFUND_REASONS)
        current_val = reasons.get(code, DEFAULT_REFUND_REASONS.get(code, False))
        reasons[code] = not current_val
        cfg["refund_reasons"] = reasons
        _save_config(cfg)
        _open_refunds_menu(chat_id, message_id)
    elif data == "src_tgl_clear_cart":
        cfg = _load_config()
        cfg["clear_cart_before_add"] = not cfg.get("clear_cart_before_add", True)
        _save_config(cfg)
        _open_orders_menu(chat_id, message_id)
    elif data == "src_tgl_notify_buyer_cart":
        cfg = _load_config()
        cfg["notify_buyer_on_cart_ready"] = not cfg.get("notify_buyer_on_cart_ready", True)
        _save_config(cfg)
        _open_orders_menu(chat_id, message_id)
    elif data == "src_tgl_deact":
        cfg = _load_config()
        cfg["auto_deactivate_lots"] = not cfg.get("auto_deactivate_lots", True)
        _save_config(cfg)
        _open_orders_menu(chat_id, message_id)
    elif data == "src_tgl_inact_remind":
        cfg = _load_config()
        cfg["buyer_inactivity_reminders"] = not cfg.get("buyer_inactivity_reminders", True)
        _save_config(cfg)
        _open_orders_menu(chat_id, message_id)
    elif data == "src_cat_notif":
        _open_notif_menu(chat_id, message_id)
    elif data == "src_tgl_notif_orders":
        cfg = _load_config()
        cfg["notify_tg_orders"] = not cfg.get("notify_tg_orders", True)
        _save_config(cfg)
        _open_notif_menu(chat_id, message_id)
    elif data == "src_tgl_notif_success":
        cfg = _load_config()
        cfg["notify_tg_success"] = not cfg.get("notify_tg_success", True)
        _save_config(cfg)
        _open_notif_menu(chat_id, message_id)
    elif data == "src_tgl_notif_errors":
        cfg = _load_config()
        cfg["notify_tg_errors"] = not cfg.get("notify_tg_errors", True)
        _save_config(cfg)
        _open_notif_menu(chat_id, message_id)
    elif data == "src_cat_messages":
        _open_messages_menu(chat_id, message_id)
    elif data.startswith("src_msg_view:"):
        key = data.split(":", 1)[1]
        _open_msg_editor(chat_id, key, message_id)
    elif data.startswith("src_msg_reset_one:"):
        key = data.split(":", 1)[1]
        cfg = _load_config()
        bm = cfg.get("buyer_messages") or {}
        if key in bm:
            bm.pop(key, None)
            cfg["buyer_messages"] = bm
            _save_config(cfg)
        _open_msg_editor(chat_id, key, message_id)
    elif data == "src_msg_reset_all":
        cfg = _load_config()
        cfg["buyer_messages"] = dict(DEFAULT_BUYER_MESSAGES)
        _save_config(cfg)
        _open_messages_menu(chat_id, message_id)
    elif data == "src_cat_queue":
        _open_queue_menu(chat_id, message_id)
    elif data in ("src_cycle_workers", "src_set_workers"):
        _waiting[chat_id] = {"action": "input_workers", "prompt_id": message_id}
        cfg = _load_config()
        cur = cfg.get("max_workers", 5)
        text = (
            f"⚡ <b>Количество одновременных смен</b>\n\n"
            f"Текущее значение: <b>{cur}</b>\n\n"
            "Введите желаемое число параллельных замен аккаунтов (например: <code>5</code> или <code>10</code>):"
        )
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_cat_queue")]]))
    elif data in ("src_cycle_timeout", "src_set_timeout"):
        _waiting[chat_id] = {"action": "input_timeout", "prompt_id": message_id}
        cfg = _load_config()
        cur = int(cfg.get("request_timeout", 30.0))
        text = (
            f"⏱ <b>Таймаут сетевых запросов</b>\n\n"
            f"Текущее значение: <b>{cur} сек</b>\n\n"
            "Введите таймаут запросов в секундах (например: <code>30</code> или <code>60</code>):"
        )
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_cat_queue")]]))
    elif data == "src_cat_maint":
        _open_maint_menu(chat_id, message_id)
    elif data == "src_maint_cfg_menu":
        _open_maint_cfg_menu(chat_id, message_id)
    elif data.startswith("src_maint_export:"):
        kind = data.split(":", 1)[1].strip().lower()
        if bot_instance:
            try:
                fname, caption, payload = _build_export_payload(kind)
                content = json.dumps(payload, indent=2, ensure_ascii=False)
                bio = io.BytesIO(content.encode("utf-8"))
                bio.name = fname
                bot_instance.send_document(chat_id, bio, caption=caption)
            except Exception as e:
                _tg_send(chat_id, f"❌ Ошибка отправки файла: {e}")
    elif data == "src_maint_export_cfg":
        if os.path.exists(CONFIG_FILE) and bot_instance:
            try:
                with open(CONFIG_FILE, "rb") as doc:
                    bot_instance.send_document(chat_id, doc, caption=f"📄 Конфигурация {NAME} (settings.json)")
            except Exception as e:
                _tg_send(chat_id, f"❌ Ошибка отправки файла: {e}")
    elif data == "src_maint_import_cfg":
        _waiting[chat_id] = {"action": "import_config", "prompt_id": message_id}
        text = "📥 <b>Отправьте ранее скачанный <code>.json</code> файл (фулл-бэкап, настройки, лоты, карты или прокси) или текст JSON в этот чат:</b>"
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_maint_cfg_menu")]]))
    elif data == "src_maint_download_logs":
        log_paths = [
            PLUGIN_LOG_FILE,
            os.path.join(BASE_DIR, "logs", "cardinal.log"),
            os.path.join(BASE_DIR, "cardinal.log"),
        ]
        sent = False
        last_err = ""
        for lp in log_paths:
            if os.path.exists(lp) and os.path.getsize(lp) > 0 and bot_instance:
                try:
                    with open(lp, "rb") as doc:
                        bot_instance.send_document(chat_id, doc, caption=f"📄 Лог {os.path.basename(lp)} ({NAME})")
                    sent = True
                    break
                except Exception as e:
                    last_err = str(e)
                    logger.warning(f"[{NAME}] Ошибка отправки лог-файла {lp} в TG: {e}")
        if not sent:
            if last_err:
                _tg_send(chat_id, f"❌ Ошибка отправки файла логов: {last_err}")
            else:
                _tg_send(chat_id, "ℹ️ Файл логов пока не найден или пуст.")
    elif data == "src_db_menu":
        _open_db_menu(chat_id, message_id)
    elif data == "src_cards":
        _open_cards_menu(chat_id, message_id, page=0)
    elif data == "src_cards_by_cc":
        _open_cards_by_country(chat_id, message_id)
    elif data.startswith("src_cards_filter:"):
        cf = data.split(":", 1)[1].upper()
        _open_cards_menu(chat_id, message_id, page=0, country_filter=cf)
    elif data.startswith("src_cards_cc_page:"):
        parts = data.split(":")
        cf = parts[1].upper() if len(parts) > 1 else None
        p = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        _open_cards_menu(chat_id, message_id, page=p, country_filter=cf)
    elif data.startswith("src_cards_page:"):
        p = int(data.split(":", 1)[1])
        _open_cards_menu(chat_id, message_id, page=p)
    elif data == "src_cards_add":
        _open_cards_add_country(chat_id, message_id, page=0)
    elif data.startswith("src_cards_add_cc_page:"):
        p = int(data.split(":", 1)[1])
        _open_cards_add_country(chat_id, message_id, page=p)
    elif data.startswith("src_cards_add_cc:"):
        cc = data.split(":", 1)[1].upper()
        _open_cards_input_prompt(chat_id, cc, message_id)
    elif data == "src_cards_add_custom_cc":
        _waiting[chat_id] = {"action": "cards_custom_cc", "prompt_id": message_id}
        text = "✍️ <b>Введите двухбуквенный код страны для добавляемых карт</b> (например: <code>UA</code>, <code>KZ</code>, <code>TR</code> или <code>ANY</code>):"
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_cards_add")]]))
    elif data.startswith("src_cw_type:"):
        pm = data.split(":", 1)[1].strip().lower()
        st_w = _waiting.get(chat_id) or {}
        draft = st_w.get("draft")
        if isinstance(draft, dict) and draft.get("number"):
            draft["payment_method"] = pm if pm in ("visa", "mastercard") else "visa"
            _card_wizard_ask_billing(chat_id, draft, message_id=message_id)
        else:
            _open_cards_menu(chat_id, message_id)
    elif data == "src_cw_billing_auto":
        st_w = _waiting.get(chat_id) or {}
        draft = st_w.get("draft")
        if isinstance(draft, dict) and draft.get("number"):
            threading.Thread(
                target=_card_wizard_fetch_billing_worker,
                args=(chat_id, draft, message_id, False),
                daemon=True,
            ).start()
        else:
            _open_cards_menu(chat_id, message_id)
    elif data == "src_cw_retest_addr":
        st_w = _waiting.get(chat_id) or {}
        draft = st_w.get("draft")
        if isinstance(draft, dict) and draft.get("number"):
            threading.Thread(
                target=_card_wizard_fetch_billing_worker,
                args=(chat_id, draft, message_id, True),
                daemon=True,
            ).start()
        else:
            _open_cards_menu(chat_id, message_id)
    elif data == "src_cw_go_limit":
        st_w = _waiting.get(chat_id) or {}
        draft = st_w.get("draft")
        if isinstance(draft, dict) and draft.get("number"):
            _card_wizard_ask_limit(chat_id, draft, message_id=message_id)
        else:
            _open_cards_menu(chat_id, message_id)
    elif data.startswith("src_cw_lim_act:"):
        act = data.split(":", 1)[1].strip().lower()
        st_w = _waiting.get(chat_id) or {}
        draft = st_w.get("draft")
        if isinstance(draft, dict) and draft.get("number"):
            if act == "none":
                draft["limit_action"] = "none"
                draft["max_uses"] = 0
                draft["limit_configured"] = True
                _card_wizard_preview(chat_id, draft, message_id=message_id)
            else:
                draft["limit_action"] = "delete" if act == "delete" else "deactivate"
                _card_wizard_ask_limit_count(chat_id, draft, message_id=message_id)
        else:
            _open_cards_menu(chat_id, message_id)
    elif data == "src_cw_billing_manual":
        st_w = _waiting.get(chat_id) or {}
        draft = st_w.get("draft")
        if isinstance(draft, dict) and draft.get("number"):
            _card_wizard_ask_manual_billing(chat_id, draft, message_id=message_id)
        else:
            _open_cards_menu(chat_id, message_id)
    elif data == "src_cw_save":
        st_w = _waiting.pop(chat_id, None) or {}
        draft = st_w.get("draft")
        if isinstance(draft, dict) and draft.get("number"):
            entry = normalize_card_entry(draft, default_country=draft.get("country", "ANY"))
            if entry:
                existing = _load_cards()
                combined, added_cnt = _merge_cards(existing, [entry])
                _save_cards(combined)
                if added_cnt:
                    _tg_send(chat_id, f"✅ <b>Карта сохранена в базу!</b> (<code>{_mask_card_number(entry['number'])}</code>)")
                    _open_card_detail(chat_id, entry["id"], message_id=message_id)
                else:
                    _tg_send(chat_id, "ℹ️ Такая карта уже есть в базе.")
                    _open_cards_menu(chat_id, message_id)
            else:
                _tg_send(chat_id, "❌ Не удалось сохранить карту — проверьте корректность данных.")
                _open_cards_menu(chat_id, message_id)
        else:
            _open_cards_menu(chat_id, message_id)
    elif data.startswith("src_card_view:"):
        cid_card = data.split(":", 1)[1]
        _open_card_detail(chat_id, cid_card, message_id)
    elif data.startswith("src_card_tgl:"):
        cid_card = data.split(":", 1)[1]
        cards = _load_cards()
        for c in cards:
            if c.get("id") == cid_card:
                new_en = not c.get("enabled", True)
                c["enabled"] = new_en
                if new_en:
                    c["fail_count"] = 0
                    c["last_error"] = ""
                break
        _save_cards(cards)
        _open_card_detail(chat_id, cid_card, message_id)
    elif data.startswith("src_card_tgl_autobill:"):
        cid_card = data.split(":", 1)[1]
        cards = _load_cards()
        for c in cards:
            if c.get("id") == cid_card:
                c["auto_billing"] = not bool(c.get("auto_billing", False))
                break
        _save_cards(cards)
        _open_card_detail(chat_id, cid_card, message_id)
    elif data.startswith("src_card_limit_menu:"):
        cid_card = data.split(":", 1)[1]
        _open_card_limit_menu(chat_id, cid_card, message_id)
    elif data.startswith("src_card_lim_act:"):
        parts = data.split(":")
        if len(parts) >= 3:
            cid_card, act = parts[1], parts[2].lower()
            if act == "none":
                cards = _load_cards()
                for c in cards:
                    if c.get("id") == cid_card:
                        c["limit_action"] = "none"
                        c["max_uses"] = 0
                        break
                _save_cards(cards)
                _open_card_detail(chat_id, cid_card, message_id)
            else:
                act_norm = "delete" if act == "delete" else "deactivate"
                _waiting[chat_id] = {"action": "card_edit_limit_count", "card_id": cid_card, "limit_action": act_norm, "prompt_id": message_id}
                act_txt = "🗑 удалить карту из плагина" if act_norm == "delete" else "⏸ убрать в неактивный пул"
                text = (
                    f"🔢 <b>Лимит для карты <code>{cid_card}</code></b>\n\n"
                    f"• Действие при достижении лимита: <b>{act_txt}</b>\n\n"
                    "Отправьте целое число — сколько раз можно использовать карту (например: <code>1</code>, <code>3</code>, <code>5</code>):"
                )
                _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", f"src_card_limit_menu:{cid_card}")]]))
    elif data.startswith("src_card_cc:"):
        parts = data.split(":")
        cid_card = parts[1]
        p = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        _open_card_change_cc(chat_id, cid_card, page=p, message_id=message_id)
    elif data.startswith("src_card_set_cc:"):
        parts = data.split(":")
        if len(parts) >= 3:
            cid_card, cc = parts[1], parts[2].upper()
            cards = _load_cards()
            for c in cards:
                if c.get("id") == cid_card:
                    c["country"] = cc
                    break
            _save_cards(cards)
            _open_card_detail(chat_id, cid_card, message_id)
    elif data.startswith("src_card_edit_note:"):
        cid_card = data.split(":", 1)[1]
        _waiting[chat_id] = {"action": "card_edit_note", "card_id": cid_card, "prompt_id": message_id}
        text = (
            f"✏️ <b>Редактирование карты <code>{cid_card}</code></b>\n\n"
            "Отправьте новую заметку и/или лимит использований:\n"
            "• <code>Основная UA</code> — только заметка\n"
            "• <code>Основная UA | 10</code> — заметка и лимит 10 использований (0 = без лимита)\n"
            "• <code>-</code> — очистить заметку"
        )
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", f"src_card_view:{cid_card}")]]))
    elif data.startswith("src_card_del_ask:"):
        cid_card = data.split(":", 1)[1]
        text = f"⚠️ <b>Удалить карту <code>{cid_card}</code> из базы?</b>"
        _tg_edit(chat_id, message_id, text, _make_kb([[("✅ Да, удалить", f"src_card_del_yes:{cid_card}"), ("❌ Отмена", f"src_card_view:{cid_card}")]]))
    elif data.startswith("src_card_del_yes:"):
        cid_card = data.split(":", 1)[1]
        cards = [c for c in _load_cards() if c.get("id") != cid_card]
        _save_cards(cards)
        _open_cards_menu(chat_id, message_id)
    elif data == "src_export_cards":
        cards = _load_cards()
        if not cards:
            _tg_send(chat_id, "ℹ️ База карт пуста.")
        elif bot_instance:
            try:
                content = "\n".join(format_card_export_line(c) for c in cards)
                bio = io.BytesIO(content.encode("utf-8"))
                bio.name = "steam_cards.txt"
                bot_instance.send_document(chat_id, bio, caption=f"💳 База карт ({len(cards)} шт.)")
            except Exception as e:
                _tg_send(chat_id, f"❌ Ошибка отправки файла: {e}")
    elif data == "src_clear_cards_ask":
        text = "⚠️ <b>Очистить всю общую базу карт?</b>"
        _tg_edit(chat_id, message_id, text, _make_kb([[("✅ Да, очистить", "src_clear_cards_yes"), ("❌ Отмена", "src_cards")]]))
    elif data == "src_clear_cards_yes":
        _save_cards([])
        _open_cards_menu(chat_id, message_id)
    elif data == "src_games_menu":
        _open_games_menu(chat_id, message_id, page=0)
    elif data.startswith("src_games_page:"):
        p = int(data.split(":", 1)[1])
        _open_games_menu(chat_id, message_id, page=p)
    elif data == "src_tgl_search_specials":
        cfg = _load_config()
        cfg["search_specials_only"] = not cfg.get("search_specials_only", True)
        _save_config(cfg)
        _open_games_menu(chat_id, message_id, page=0)
    elif data in ("src_input_search_mult", "src_cycle_search_mult"):
        cfg = _load_config()
        cur = float(cfg.get("search_max_price_multiplier", 2.0))
        _waiting[chat_id] = {"action": "input_search_mult", "prompt_id": message_id}
        text = (
            "📌 <b>Фиксация максимальной цены (множитель)</b>\n\n"
            f"• Текущее значение: <b>×{cur:g}</b> от базовой цены региона\n"
            "<i>Например, при цене региона 150 ₸ и множителе ×2 бот будет брать игры не дороже 300 ₸.</i>\n\n"
            "Отправьте нужное число в чат (от <code>1</code> до <code>50</code>, например: <code>1.5</code>, <code>2</code>, <code>3.5</code>):"
        )
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_games_menu")]]))
    elif data.startswith("src_games_table:"):
        p = int(data.split(":", 1)[1])
        _open_games_price_table(chat_id, page=p, message_id=message_id)
    elif data.startswith("src_price_edit_menu:"):
        p = int(data.split(":", 1)[1])
        _open_price_edit_menu(chat_id, page=p, message_id=message_id)
    elif data.startswith("src_price_edit_cc:"):
        cc = data.split(":", 1)[1].upper()
        _open_price_edit_prompt(chat_id, cc, message_id=message_id)
    elif data.startswith("src_price_reset_cc:"):
        cc = data.split(":", 1)[1].upper()
        cfg = _load_config()
        custom = cfg.get("custom_region_prices")
        if isinstance(custom, dict) and cc in custom:
            custom.pop(cc, None)
            cfg["custom_region_prices"] = custom
            _save_config(cfg)
        _open_price_edit_prompt(chat_id, cc, message_id=message_id)
    elif data == "src_price_reset_all":
        cfg = _load_config()
        cfg["custom_region_prices"] = {}
        _save_config(cfg)
        _open_games_menu(chat_id, message_id, page=0)
    elif data == "src_games_input_cc":
        _waiting[chat_id] = {"action": "games_input_cc", "prompt_id": message_id}
        text = "✍️ <b>Введите двухбуквенный код страны для поиска дешёвых игр</b> (например: <code>UA</code>, <code>KZ</code>, <code>JP</code>, <code>EU</code>):"
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_games_menu")]]))
    elif data.startswith("src_games_find:"):
        cc = data.split(":", 1)[1].upper()
        threading.Thread(target=_search_games_worker, args=(chat_id, cc, message_id, "src_games_menu", None), daemon=True).start()
    elif data == "src_proxies":
        _waiting.pop(chat_id, None)
        _menu_proxies(chat_id, message_id, page=0)
    elif data.startswith("src_proxies_page:"):
        p = int(data.split(":", 1)[1])
        _menu_proxies(chat_id, message_id, page=p)
    elif data.startswith("src_proxies_cc_page:"):
        parts = data.split(":")
        cf = parts[1].upper() if len(parts) > 1 else None
        p = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        _menu_proxies(chat_id, message_id, page=p, country_filter=cf)
    elif data == "src_proxies_by_cc":
        _open_proxies_by_country(chat_id, message_id)
    elif data.startswith("src_proxies_filter:"):
        cf = data.split(":", 1)[1].upper()
        _menu_proxies(chat_id, message_id, page=0, country_filter=cf)
    elif data.startswith("src_proxy_view:"):
        parts = data.split(":")
        if len(parts) >= 3 and parts[2].isdigit():
            _open_proxy_detail(chat_id, parts[1].upper(), int(parts[2]), message_id=message_id)
    elif data.startswith("src_proxy_test_one:"):
        parts = data.split(":")
        if len(parts) >= 3 and parts[2].isdigit():
            threading.Thread(
                target=_test_one_proxy_worker,
                args=(chat_id, parts[1].upper(), int(parts[2]), message_id),
                daemon=True,
            ).start()
    elif data.startswith("src_proxy_ch_cc:"):
        parts = data.split(":")
        if len(parts) >= 3 and parts[2].isdigit():
            p = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            _open_proxy_change_cc(chat_id, parts[1].upper(), int(parts[2]), page=p, message_id=message_id)
    elif data.startswith("src_proxy_set_cc:"):
        parts = data.split(":")
        if len(parts) >= 4 and parts[2].isdigit():
            old_cc, idx_p, new_cc = parts[1].upper(), int(parts[2]), parts[3].upper()
            by_cc = _load_proxies_by_country()
            old_lst = list(by_cc.get(old_cc) or [])
            if 0 <= idx_p < len(old_lst):
                moved = old_lst.pop(idx_p)
                if old_lst:
                    by_cc[old_cc] = old_lst
                else:
                    by_cc.pop(old_cc, None)
                new_lst = list(by_cc.get(new_cc) or [])
                if moved not in new_lst:
                    new_lst.append(moved)
                by_cc[new_cc] = new_lst
                _save_proxies_by_country(by_cc)
                new_idx = new_lst.index(moved)
                _open_proxy_detail(chat_id, new_cc, new_idx, message_id=message_id)
            else:
                _menu_proxies(chat_id, message_id, page=0, country_filter=old_cc)
    elif data.startswith("src_proxy_del:"):
        parts = data.split(":")
        if len(parts) >= 3 and parts[2].isdigit():
            cc_p, idx_p = parts[1].upper(), int(parts[2])
            by_cc = _load_proxies_by_country()
            lst = list(by_cc.get(cc_p) or [])
            if 0 <= idx_p < len(lst):
                lst.pop(idx_p)
                if lst:
                    by_cc[cc_p] = lst
                else:
                    by_cc.pop(cc_p, None)
                _save_proxies_by_country(by_cc)
            if cc_p in by_cc:
                _menu_proxies(chat_id, message_id, page=0, country_filter=cc_p)
            else:
                _open_proxies_by_country(chat_id, message_id)
    elif data.startswith("src_proxies_add_cc_page:"):
        p = int(data.split(":", 1)[1])
        _open_proxies_add_country(chat_id, message_id, page=p)
    elif data.startswith("src_proxies_add_cc:"):
        cc = data.split(":", 1)[1].upper()
        _open_proxies_input_prompt(chat_id, cc, message_id)
    elif data == "src_proxies_add_custom_cc":
        _waiting[chat_id] = {"action": "proxies_custom_cc", "prompt_id": message_id}
        text = "✍️ <b>Введите двухбуквенный код страны для прокси</b> (например: <code>UA</code>, <code>KZ</code>, <code>TR</code> или <code>ANY</code>):"
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", "src_proxies")]]))
    elif data.startswith("src_clear_proxies_cc:"):
        cf = data.split(":", 1)[1].upper()
        by_cc = _load_proxies_by_country()
        by_cc.pop(cf, None)
        _save_proxies_by_country(by_cc)
        _open_proxies_by_country(chat_id, message_id)
    elif data == "src_export_proxies":
        proxies = _load_proxies()
        if not proxies:
            _tg_send(chat_id, "ℹ️ Общая база прокси пуста.")
        elif bot_instance:
            try:
                bio = io.BytesIO("\n".join(proxies).encode("utf-8"))
                bio.name = "proxies_pool.txt"
                bot_instance.send_document(chat_id, bio, caption=f"🌐 Общая база прокси ({len(proxies)} шт.)")
            except Exception as e:
                _tg_send(chat_id, f"❌ Ошибка отправки файла: {e}")
    elif data == "src_input_proxies":
        _open_proxies_add_country(chat_id, message_id, page=0)
    elif data == "src_clear_proxies_ask":
        text = "⚠️ <b>Очистить всю общую базу прокси?</b>"
        _tg_edit(chat_id, message_id, text, _make_kb([[("✅ Да, очистить", "src_clear_proxies_yes"), ("❌ Отмена", "src_proxies")]]))
    elif data == "src_clear_proxies_yes":
        _save_proxies([])
        _menu_proxies(chat_id, message_id)
    elif data == "src_test_proxies":
        threading.Thread(target=_test_proxies_worker, args=(chat_id, message_id), daemon=True).start()
    elif data == "src_lots_auto_2044":
        _handle_auto_lots_2044(chat_id, message_id)
    elif data == "src_lot_add_wizard":
        _start_lot_wizard(chat_id, message_id)
    elif data.startswith("src_wiz_cc_page:"):
        p = int(data.split(":", 1)[1])
        st = _waiting.get(chat_id) or {}
        lid = st.get("lot_id", "")
        title = st.get("title", f"Лот {lid}")
        _lot_wizard_ask_country(chat_id, lid, title, message_id, page=p)
    elif data.startswith("src_wiz_cc:"):
        cc = data.split(":", 1)[1].upper()
        st = _waiting.get(chat_id) or {}
        lid = st.get("lot_id", "")
        title = st.get("title", f"Лот {lid}")
        _lot_wizard_ask_proxy(chat_id, lid, title, cc, message_id)
    elif data == "src_wiz_use_db_px":
        st = _waiting.get(chat_id) or {}
        lid = st.get("lot_id", "")
        title = st.get("title", f"Лот #{lid}")
        cc = st.get("country", "KZ")
        db_px = _get_proxies_for_country(cc, include_any=False)
        if not lid or not db_px:
            _tg_send(chat_id, f"⚠️ В общей базе нет прокси для {_country_display(cc)}. Отправьте прокси вручную.")
        else:
            _lot_wizard_ask_card(chat_id, lid, title, cc, list(db_px), message_id)
    elif data == "src_wiz_use_db_cards":
        st = _waiting.get(chat_id) or {}
        lid = st.get("lot_id", "")
        title = st.get("title", f"Лот #{lid}")
        cc = st.get("country", "KZ")
        proxies = list(st.get("proxies") or [])
        db_cards = _get_cards_for_country(cc, include_any=False)
        if not lid:
            _open_lots_menu(chat_id, message_id)
        elif not db_cards:
            _tg_send(chat_id, f"⚠️ В общей базе нет карт для {_country_display(cc)}. Отправьте карту вручную или нажмите «Пропустить».")
        else:
            _lot_wizard_finish(chat_id, lid, title, cc, proxies, db_cards, message_id)
    elif data.startswith("src_wiz_pick_card:"):
        cid_pick = data.split(":", 1)[1]
        st = _waiting.get(chat_id) or {}
        lid = st.get("lot_id", "")
        title = st.get("title", f"Лот #{lid}")
        cc = st.get("country", "KZ")
        proxies = list(st.get("proxies") or [])
        if not lid:
            _open_lots_menu(chat_id, message_id)
        else:
            db_cards = _get_cards_for_country(cc, include_any=False)
            picked = [c for c in db_cards if str(c.get("id")) == cid_pick]
            if not picked:
                _tg_send(chat_id, "⚠️ Карта не найдена в базе. Выберите другую или нажмите «Пропустить».")
            else:
                _lot_wizard_finish(chat_id, lid, title, cc, proxies, picked, message_id)
    elif data.startswith("src_wiz_card_page:"):
        p = int(data.split(":", 1)[1])
        st = _waiting.get(chat_id) or {}
        lid = st.get("lot_id", "")
        title = st.get("title", f"Лот #{lid}")
        cc = st.get("country", "KZ")
        proxies = list(st.get("proxies") or [])
        if lid:
            _lot_wizard_ask_card(chat_id, lid, title, cc, proxies, message_id, page=p)
    elif data == "src_wiz_skip_cards":
        st = _waiting.get(chat_id) or {}
        lid = st.get("lot_id", "")
        title = st.get("title", f"Лот #{lid}")
        cc = st.get("country", "KZ")
        proxies = list(st.get("proxies") or [])
        if not lid:
            _open_lots_menu(chat_id, message_id)
        else:
            _lot_wizard_finish(chat_id, lid, title, cc, proxies, [], message_id)
    elif data == "src_lots_clear_ask":
        text = "⚠️ <b>Очистить все привязанные лоты?</b>"
        _tg_edit(chat_id, message_id, text, _make_kb([[("✅ Да, очистить", "src_lots_clear_yes"), ("❌ Отмена", "src_lots_menu")]]))
    elif data == "src_lots_clear_yes":
        cfg = _load_config()
        cfg["lots"] = {}
        _save_config(cfg)
        _open_lots_menu(chat_id, message_id)
    elif data == "src_lots_bulk_menu":
        _open_lots_bulk_menu(chat_id, message_id)
    elif data.startswith("src_lots_bulk_toggle:"):
        action = data.split(":", 1)[1]
        enable = (action == "enable")
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        for l in lots.values():
            l["enabled"] = enable
        cfg["lots"] = lots
        _save_config(cfg)
        _open_lots_bulk_menu(chat_id, message_id)
        if cfg.get("auto_deactivate_lots", True):
            def _sync_fp_bulk_toggle():
                for lid in list(lots.keys()):
                    try:
                        _set_funpay_lot_active(cardinal_instance, lid, enable)
                    except Exception as e:
                        logger.warning(f"[{NAME}] Ошибка синхронизации статуса лота #{lid} на FunPay: {e}")
            threading.Thread(target=_sync_fp_bulk_toggle, daemon=True).start()
    elif data == "src_lots_bulk_del_ask":
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        total = len(lots)
        text = (
            f"🗑 <b>Удаление всех лотов ({total} шт.)</b>\n\n"
            f"⚠️ <b>Выберите вариант удаления:</b>\n\n"
            f"1️⃣ <b>Из плагина</b> — лоты удаляются только из базы плагина. На сайте FunPay лоты останутся без изменений.\n\n"
            f"2️⃣ <b>Из плагина и FunPay</b> — лоты будут удалены из плагина и навсегда удалены с сайта FunPay."
        )
        rows = [
            [("📦 Из плагина", "src_lots_bulk_del_plugin")],
            [("💥 Из плагина и FunPay", "src_lots_bulk_del_both_ask")],
            [("◀️ Отмена", "src_lots_bulk_menu")],
        ]
        _tg_edit(chat_id, message_id, text, _make_kb(rows))
    elif data == "src_lots_bulk_del_plugin":
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        count = len(lots)
        cfg["lots"] = {}
        _save_config(cfg)
        text = (
            f"✅ <b>Все лоты удалены из плагина!</b>\n\n"
            f"• Удалено из плагина: <b>{count} шт.</b>\n"
            f"• Лоты на FunPay сохранены без изменений."
        )
        _tg_edit(chat_id, message_id, text, _make_kb([[("⭐ К списку лотов", "src_lots_menu")]]))
    elif data == "src_lots_bulk_del_both_ask":
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        total = len(lots)
        text = (
            f"⚠️ <b>Подтверждение полного удаления</b>\n\n"
            f"Вы уверены, что хотите удалить <b>{total} шт.</b> лотов из плагина <b>и навсегда с сайта FunPay</b>?\n\n"
            f"❗️ <i>Восстановить удалённые с FunPay лоты будет невозможно!</i>"
        )
        rows = [
            [("💥 Да, удалить отовсюду", "src_lots_bulk_del_both_yes")],
            [("◀️ Отмена", "src_lots_bulk_del_ask")],
        ]
        _tg_edit(chat_id, message_id, text, _make_kb(rows))
    elif data == "src_lots_bulk_del_both_yes":
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        total = len(lots)
        _tg_edit(chat_id, message_id, "⏳ <b>Удаление лотов с FunPay и из плагина... Пожалуйста, подождите.</b>")
        def _delete_both_worker():
            fp_deleted = 0
            fp_failed = 0
            for lid in list(lots.keys()):
                ok = _delete_funpay_lot(cardinal_instance, lid)
                if ok:
                    fp_deleted += 1
                else:
                    fp_failed += 1
            curr_cfg = _load_config()
            curr_cfg["lots"] = {}
            _save_config(curr_cfg)
            msg = (
                f"✅ <b>Удаление завершено!</b>\n\n"
                f"• Удалено из базы плагина: <b>{total} шт.</b>\n"
                f"• Удалено с сайта FunPay: <b>{fp_deleted} шт.</b>\n"
            )
            if fp_failed > 0:
                msg += f"• Ошибок при удалении с FunPay: <b>{fp_failed} шт.</b>\n"
            _tg_edit(chat_id, message_id, msg, _make_kb([[("⭐ К списку лотов", "src_lots_menu")]]))
        threading.Thread(target=_delete_both_worker, daemon=True).start()
    elif data.startswith("src_lot_card:"):
        lid = data.split(":", 1)[1]
        _open_lot_card(chat_id, lid, message_id)
    elif data.startswith("src_lot_tgl:"):
        lid = data.split(":", 1)[1]
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        if lid in lots:
            new_st = not lots[lid].get("enabled", True)
            lots[lid]["enabled"] = new_st
            cfg["lots"] = lots
            _save_config(cfg)
            if cfg.get("auto_deactivate_lots", True):
                _set_funpay_lot_active(cardinal_instance, lid, new_st)
        _open_lot_card(chat_id, lid, message_id)
    elif data.startswith("src_lot_change_cc:"):
        parts = data.split(":")
        lid = parts[1]
        p = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        _open_lot_change_cc(chat_id, lid, message_id, page=p)
    elif data.startswith("src_lot_set_cc:"):
        parts = data.split(":")
        lid, cc = parts[1], parts[2].upper()
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        if lid in lots:
            lots[lid]["country"] = cc
            cfg["lots"] = lots
            _save_config(cfg)
        _open_lot_card(chat_id, lid, message_id)
    elif data.startswith("src_lot_input_cc:"):
        lid = data.split(":", 1)[1]
        _waiting[chat_id] = {"action": "lot_input_cc", "lot_id": lid, "prompt_id": message_id}
        text = f"✍️ <b>Введите двухбуквенный код страны для лота #{lid}</b> (например: <code>KZ</code>, <code>UA</code>, <code>US</code>, <code>EU</code>):"
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", f"src_lot_card:{lid}")]]))
    elif data.startswith("src_lot_cards_menu:"):
        lid = data.split(":", 1)[1]
        _open_lot_cards_menu(chat_id, lid, message_id)
    elif data.startswith("src_lot_pick_cards:"):
        lid = data.split(":", 1)[1]
        _open_lot_pick_cards(chat_id, lid, message_id, page=0)
    elif data.startswith("src_lot_pick_cards_p:"):
        parts = data.split(":")
        if len(parts) >= 3 and parts[2].isdigit():
            _open_lot_pick_cards(chat_id, parts[1], message_id, page=int(parts[2]))
    elif data.startswith("src_lot_attach_all_cards:"):
        lid = data.split(":", 1)[1]
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        if lid in lots:
            cc = lots[lid].get("country", "KZ")
            db_cards = _get_cards_for_country(cc, include_any=False)
            comb, _ = _merge_cards(lots[lid].get("cards", []), db_cards)
            lots[lid]["cards"] = comb
            cfg["lots"] = lots
            _save_config(cfg)
        _open_lot_pick_cards(chat_id, lid, message_id)
    elif data.startswith("src_lot_attach_card:"):
        parts = data.split(":")
        if len(parts) >= 3:
            lid, cid_card = parts[1], parts[2]
            cfg = _load_config()
            lots = _get_lots_dict(cfg)
            if lid in lots:
                cc = lots[lid].get("country", "KZ")
                db_cards = _get_cards_for_country(cc, include_any=False)
                chosen = next((c for c in db_cards if c.get("id") == cid_card), None)
                if chosen:
                    comb, _ = _merge_cards(lots[lid].get("cards", []), [chosen])
                    lots[lid]["cards"] = comb
                    cfg["lots"] = lots
                    _save_config(cfg)
        _open_lot_pick_cards(chat_id, lid, message_id)
    elif data.startswith("src_lot_add_cards:"):
        lid = data.split(":", 1)[1]
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        lot_cc = (lots.get(str(lid)) or {}).get("country", "ANY")
        _waiting[chat_id] = {"action": "lot_add_cards", "lot_id": lid, "country": lot_cc, "prompt_id": message_id}
        text = (
            f"➕ <b>Добавление карт для лота #{lid} ({_country_display(lot_cc)})</b>\n\n"
            "Отправьте список карт текстом (каждая с новой строки) или загрузите файл <code>.txt</code> / <code>.json</code>:\n\n"
            "<b>Форматы:</b>\n"
            "• <code>4111222233334444:12/28:123</code>\n"
            "• <code>4111222233334444|12/2028|123|Имя|Адрес|Город|Индекс|Телефон|Заметка</code>"
        )
        _tg_edit(chat_id, message_id, text, _make_kb([[("❌ Отмена", f"src_lot_cards_menu:{lid}")]]))
    elif data.startswith("src_lot_export_cards:"):
        lid = data.split(":", 1)[1]
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        lot_cards = (lots.get(str(lid)) or {}).get("cards", [])
        if not lot_cards:
            _tg_send(chat_id, f"ℹ️ У лота #{lid} нет привязанных карт.")
        elif bot_instance:
            try:
                content = "\n".join(format_card_export_line(c) for c in lot_cards)
                bio = io.BytesIO(content.encode("utf-8"))
                bio.name = f"cards_lot_{lid}.txt"
                bot_instance.send_document(chat_id, bio, caption=f"💳 Карты лота #{lid} ({len(lot_cards)} шт.)")
            except Exception as e:
                _tg_send(chat_id, f"❌ Ошибка отправки файла: {e}")
    elif data.startswith("src_lot_clear_cards:"):
        lid = data.split(":", 1)[1]
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        if lid in lots:
            lots[lid]["cards"] = []
            cfg["lots"] = lots
            _save_config(cfg)
        _open_lot_cards_menu(chat_id, lid, message_id)
    elif data.startswith("src_lot_search_game:"):
        lid = data.split(":", 1)[1]
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        lot = lots.get(str(lid)) or {}
        lot_cc = lot.get("country", "KZ")
        lot_px = (lot.get("proxies") or [None])[0]
        threading.Thread(
            target=_search_games_worker,
            args=(chat_id, lot_cc, message_id, f"src_lot_card:{lid}", lot_px),
            daemon=True,
        ).start()
    elif data.startswith("src_lot_px_menu:"):
        lid = data.split(":", 1)[1]
        _open_lot_proxies_menu(chat_id, lid, message_id)
    elif data.startswith("src_lot_pick_px:"):
        lid = data.split(":", 1)[1]
        _open_lot_pick_proxies(chat_id, lid, message_id, page=0)
    elif data.startswith("src_lot_pick_px_p:"):
        parts = data.split(":")
        if len(parts) >= 3 and parts[2].isdigit():
            _open_lot_pick_proxies(chat_id, parts[1], message_id, page=int(parts[2]))
    elif data.startswith("src_lot_attach_all_px:"):
        lid = data.split(":", 1)[1]
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        if lid in lots:
            cc = lots[lid].get("country", "KZ")
            db_px = _get_proxies_for_country(cc, include_any=False)
            comb = list(dict.fromkeys((lots[lid].get("proxies") or []) + db_px))
            lots[lid]["proxies"] = comb
            cfg["lots"] = lots
            _save_config(cfg)
        _open_lot_pick_proxies(chat_id, lid, message_id)
    elif data.startswith("src_lot_attach_px:"):
        parts = data.split(":")
        if len(parts) >= 3 and parts[2].isdigit():
            lid, idx_px = parts[1], int(parts[2])
            cfg = _load_config()
            lots = _get_lots_dict(cfg)
            if lid in lots:
                cc = lots[lid].get("country", "KZ")
                db_px = _get_proxies_for_country(cc, include_any=False)
                if 0 <= idx_px < len(db_px):
                    comb = list(dict.fromkeys((lots[lid].get("proxies") or []) + [db_px[idx_px]]))
                    lots[lid]["proxies"] = comb
                    cfg["lots"] = lots
                    _save_config(cfg)
        _open_lot_pick_proxies(chat_id, lid, message_id)
    elif data.startswith("src_lot_add_px:"):
        lid = data.split(":", 1)[1]
        _waiting[chat_id] = {"action": "lot_add_px", "lot_id": lid, "prompt_id": message_id}
        text = (
            f"➕ <b>Добавление прокси для лота #{lid}</b>\n\n"
            "Вы можете:\n"
            "1. Отправить список прокси текстом (каждый с новой строки)\n"
            "2. Загрузить файл <code>.txt</code> со списком прокси\n\n"
            "<b>Форматы (HTTP/HTTPS):</b>\n"
            "• <code>ip:port</code>\n"
            "• <code>ip:port:user:pass</code>\n"
            "• <code>http://user:pass@ip:port</code>"
        )
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        proxies = (lots.get(str(lid)) or {}).get("proxies", [])
        rows = []
        if proxies:
            rows.append([("💾 Скачать текущие прокси (.txt)", f"src_lot_export_px:{lid}")])
        rows.append([("❌ Отмена", f"src_lot_px_menu:{lid}")])
        _tg_edit(chat_id, message_id, text, _make_kb(rows))
    elif data.startswith("src_lot_export_px:"):
        lid = data.split(":", 1)[1]
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        proxies = (lots.get(str(lid)) or {}).get("proxies", [])
        if not proxies:
            _tg_send(chat_id, f"ℹ️ У лота #{lid} нет привязанных прокси.")
        elif bot_instance:
            try:
                bio = io.BytesIO("\n".join(proxies).encode('utf-8'))
                bio.name = f"proxies_lot_{lid}.txt"
                bot_instance.send_document(chat_id, bio, caption=f"🌐 Прокси лота #{lid} ({len(proxies)} шт.)")
            except Exception as e:
                _tg_send(chat_id, f"❌ Ошибка отправки файла: {e}")
    elif data.startswith("src_lot_test_px:"):
        lid = data.split(":", 1)[1]
        threading.Thread(target=_test_lot_proxies_worker, args=(chat_id, lid, message_id), daemon=True).start()
    elif data.startswith("src_lot_clear_px:"):
        lid = data.split(":", 1)[1]
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        if lid in lots:
            lots[lid]["proxies"] = []
            if cfg.get("auto_deactivate_lots", True):
                lots[lid]["enabled"] = False
                _deactivate_funpay_lot(cardinal_instance, lid)
            cfg["lots"] = lots
            _save_config(cfg)
        _open_lot_proxies_menu(chat_id, lid, message_id)
    elif data.startswith("src_lot_delete_ask:"):
        lid = data.split(":", 1)[1]
        text = f"⚠️ <b>Удалить лот #{lid} из плагина?</b>"
        _tg_edit(chat_id, message_id, text, _make_kb([[("✅ Да, удалить", f"src_lot_delete_yes:{lid}"), ("❌ Отмена", f"src_lot_card:{lid}")]]))
    elif data.startswith("src_lot_delete_yes:"):
        lid = data.split(":", 1)[1]
        cfg = _load_config()
        lots = _get_lots_dict(cfg)
        lots.pop(lid, None)
        cfg["lots"] = lots
        _save_config(cfg)
        _open_lots_menu(chat_id, message_id)
    elif data == "src_stats_reset":
        _save_stats(DEFAULT_STATS)
        _open_stats(chat_id, message_id)
def _handle_waiting_message(message):
    chat_id = getattr(getattr(message, 'chat', None), 'id', None)
    from_user = getattr(message, 'from_user', None)
    user_id = getattr(from_user, 'id', None)
    if not chat_id or not _is_authorized(user_id):
        return
    document = getattr(message, 'document', None)
    file_text = None
    if document is not None:
        file_name = str(getattr(document, 'file_name', '') or '').strip()
        st = _waiting.get(chat_id) or {}
        if st.get("action") == "update_local" or file_name.endswith('.py'):
            if file_name.endswith('.py'):
                try:
                    file_info = bot_instance.get_file(document.file_id)
                    payload = bot_instance.download_file(file_info.file_path)
                    source = payload.decode('utf-8-sig')
                    plugin_file = os.path.abspath(__file__)
                    compile(source, plugin_file, 'exec')
                    m = re.search(r'(?m)^\s*VERSION\s*=\s*["\']([^"\']+)["\']', source)
                    new_ver = m.group(1).strip() if m else 'неизвестно'
                    stamp = time.strftime('%Y%m%d-%H%M%S')
                    backup_file = plugin_file + f'.pre-local-update.{stamp}.bak'
                    shutil.copy2(plugin_file, backup_file)
                    with open(plugin_file, "w", encoding="utf-8") as f:
                        f.write(source)
                    _waiting.pop(chat_id, None)
                    _tg_send(chat_id, f"✅ <b>Локальное обновление установлено: v{new_ver}!</b>\n\nРезервная копия: <code>{os.path.basename(backup_file)}</code>\nВыполните <code>/restart</code> для применения.", _make_kb([[("◀️ В меню", "src_home")]]))
                    return
                except Exception as e:
                    _tg_send(chat_id, f"❌ Ошибка проверки/установки файла: {e}")
                    return
        if st.get("action") in ("input_cards", "cw_card_data", "lot_add_cards", "wiz_step4_card") and (file_name.endswith('.txt') or file_name.endswith('.json')):
            try:
                file_info = bot_instance.get_file(document.file_id)
                payload = bot_instance.download_file(file_info.file_path)
                file_text = payload.decode('utf-8-sig', errors='ignore').strip()
            except Exception as e:
                _tg_send(chat_id, f"❌ Ошибка чтения файла карт: {e}")
                return
        elif st.get("action") == "import_config" or file_name.endswith('.json'):
            try:
                file_info = bot_instance.get_file(document.file_id)
                payload = bot_instance.download_file(file_info.file_path)
                data = json.loads(payload.decode('utf-8-sig'))
                summary = _apply_imported_backup(data)
                _waiting.pop(chat_id, None)
                _tg_send(chat_id, f"✅ <b>Данные успешно импортированы!</b>\n\n{summary}", _make_kb([[("⚙️ В обслуживание", "src_maint_cfg_menu")]]))
                return
            except Exception as e:
                _tg_send(chat_id, f"❌ Ошибка импорта конфига: {e}")
                return
        elif file_name.endswith('.txt') or st.get("action") in ("input_cards", "cw_card_data", "lot_add_cards", "wiz_step4_card", "lot_add_px", "wiz_step3_proxy", "input_proxies"):
            try:
                file_info = bot_instance.get_file(document.file_id)
                payload = bot_instance.download_file(file_info.file_path)
                file_text = payload.decode('utf-8-sig', errors='ignore').strip()
            except Exception as e:
                _tg_send(chat_id, f"❌ Ошибка чтения файла: {e}")
                return
    st = _waiting.get(chat_id)
    if not st:
        return
    action = st.get("action")
    text = (file_text if file_text is not None else str(getattr(message, "text", "") or "")).strip()
    if action == "import_config":
        try:
            data = json.loads(text)
            summary = _apply_imported_backup(data)
            _waiting.pop(chat_id, None)
            _tg_send(chat_id, f"✅ <b>Данные успешно импортированы!</b>\n\n{summary}", _make_kb([[("⚙️ В обслуживание", "src_maint_cfg_menu")]]))
            return
        except Exception as e:
            _tg_send(chat_id, f"❌ Ошибка разбора JSON: {e}")
            return
    elif action == "wiz_step1_id":
        lot_id = re.sub(r'\D', '', text)
        if not lot_id:
            _tg_send(chat_id, "⚠️ ID лота должен содержать только цифры. Попробуйте ещё раз:")
            return
        title = f"Лот #{lot_id}"
        try:
            if cardinal_instance and getattr(cardinal_instance, 'account', None):
                lf = cardinal_instance.account.get_lot_fields(int(lot_id))
                extracted = _extract_lot_fields_title(lf)
                if extracted:
                    title = extracted
        except Exception:
            pass
        _lot_wizard_ask_country(chat_id, lot_id, title)
    elif action == "wiz_step2_country":
        cc = re.sub(r'[^A-Za-z]', '', text).upper()
        if len(cc) == 2:
            lid = st.get("lot_id", "")
            title = st.get("title", f"Лот {lid}")
            _lot_wizard_ask_proxy(chat_id, lid, title, cc)
        else:
            _tg_send(chat_id, "⚠️ Код страны должен состоять ровно из 2 букв (например KZ, US, RU). Выберите кнопку или введите 2 буквы:")
    elif action == "wiz_step3_proxy":
        lower_t = text.lower()
        if any(w in lower_t for w in ["нет", "нету", "no", "none", "-", "отсутствует"]):
            _tg_send(chat_id, "⚠️ <b>Для добавления лота необходим прокси!</b>\n\nБез прокси смена региона Steam невозможна. Отправьте хотя бы один рабочий прокси под этот регион или нажмите «Отмена».")
            return
        new_p = parse_proxies(text)
        if not new_p:
            _tg_send(
                chat_id,
                "⚠️ Не удалось распознать прокси. Пожалуйста, отправьте прокси в поддерживаемом формате:\n"
                "• <code>ip:port</code>\n"
                "• <code>ip:port:login:password</code>\n"
                "• <code>http://ip:port</code>\n"
                "• <code>http://login:password@ip:port</code>"
            )
            return
        lid = st.get("lot_id", "")
        title = st.get("title", f"Лот #{lid}")
        cc = st.get("country", "KZ")
        _lot_wizard_ask_card(chat_id, lid, title, cc, new_p)
    elif action == "wiz_step4_card":
        lid = st.get("lot_id", "")
        title = st.get("title", f"Лот #{lid}")
        cc = st.get("country", "KZ")
        proxies = list(st.get("proxies") or [])
        lower_t = text.lower().strip()
        if lower_t in ("-", "нет", "нету", "no", "none", "skip", "пропустить", "пропуск"):
            _lot_wizard_finish(chat_id, lid, title, cc, proxies, [])
            return
        valid, invalid = parse_cards_text(text, default_country=cc)
        if not valid:
            _tg_send(
                chat_id,
                "⚠️ Валидных карт не найдено.\n"
                "Отправьте карту в формате <code>номер:ММ/ГГ:CVV</code>, загрузите <code>.txt</code> или нажмите «Пропустить»."
            )
            return
        _lot_wizard_finish(chat_id, lid, title, cc, proxies, valid, invalid_cnt=len(invalid))
    elif action == "lot_add_px":
        lid = st.get("lot_id", "")
        _waiting.pop(chat_id, None)
        new_p = parse_proxies(text)
        if new_p:
            cfg = _load_config()
            lots = _get_lots_dict(cfg)
            if lid in lots:
                old_p = lots[lid].get("proxies", [])
                comb = list(dict.fromkeys(old_p + new_p))
                lots[lid]["proxies"] = comb
                cfg["lots"] = lots
                _save_config(cfg)
                _tg_send(chat_id, f"✅ Добавлено <b>{len(comb) - len(old_p)}</b> прокси к лоту #{lid}. Всего: <b>{len(comb)}</b>.")
        else:
            _tg_send(chat_id, "⚠️ Не удалось распознать прокси.")
        _open_lot_proxies_menu(chat_id, lid)
    elif action == "lot_input_cc":
        lid = st.get("lot_id", "")
        _waiting.pop(chat_id, None)
        cc = re.sub(r'[^A-Za-z]', '', text).upper()
        if len(cc) == 2:
            cfg = _load_config()
            lots = _get_lots_dict(cfg)
            if lid in lots:
                lots[lid]["country"] = cc
                cfg["lots"] = lots
                _save_config(cfg)
                _tg_send(chat_id, f"✅ Страна для лота #{lid} изменена на <b>{_country_display(cc)}</b>.")
        else:
            _tg_send(chat_id, "⚠️ Код страны должен состоять ровно из 2 букв.")
        _open_lot_card(chat_id, lid)
    elif action == "input_proxies":
        cc = st.get("country", "ANY")
        _waiting.pop(chat_id, None)
        new_p = parse_proxies(text)
        if new_p:
            added_cnt, total_cc = _add_proxies_for_country(cc, new_p)
            _tg_send(
                chat_id,
                f"✅ Добавлено <b>{added_cnt}</b> прокси для <b>{_country_display(cc)}</b>. "
                f"Всего для {cc}: <b>{total_cc} шт.</b>",
            )
        else:
            _tg_send(chat_id, "⚠️ Не удалось распознать прокси в сообщении.")
        _menu_proxies(chat_id, country_filter=cc if cc != "ANY" else None)
    elif action == "proxies_custom_cc":
        cc = re.sub(r'[^A-Za-z]', '', text).upper()
        if cc == "ANY" or len(cc) == 2:
            _open_proxies_input_prompt(chat_id, cc)
        else:
            _tg_send(chat_id, "⚠️ Введите 2-буквенный код страны (например: <code>UA</code>, <code>KZ</code>) или <code>ANY</code>:")
    elif action == "cards_custom_cc":
        cc = re.sub(r'[^A-Za-z]', '', text).upper()
        if cc == "ANY" or len(cc) == 2:
            _open_cards_input_prompt(chat_id, cc)
        else:
            _tg_send(chat_id, "⚠️ Введите 2-буквенный код страны (например: <code>UA</code>, <code>KZ</code>) или <code>ANY</code>:")
    elif action == "cw_card_data":
        def_cc = st.get("country", "ANY")
        non_empty_lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if file_text is not None or len(non_empty_lines) > 1 or text.startswith("[") or text.startswith("{"):
            _waiting.pop(chat_id, None)
            valid, invalid = parse_cards_text(text, default_country=def_cc)
            if valid:
                existing = _load_cards()
                combined, added_cnt = _merge_cards(existing, valid)
                _save_cards(combined)
                msg = f"✅ Массово добавлено <b>{added_cnt}</b> карт ({_country_display(def_cc)}). Всего в базе: <b>{len(combined)}</b>."
                if invalid:
                    msg += f"\n⚠️ Пропущено строк с ошибкой формата: {len(invalid)}."
                _tg_send(chat_id, msg)
            else:
                _tg_send(chat_id, "⚠️ Валидных карт не найдено.")
            _open_cards_menu(chat_id)
        else:
            valid_one, _ = parse_cards_text(text, default_country=def_cc)
            if valid_one:
                draft = dict(valid_one[0])
                _card_wizard_ask_type(chat_id, draft)
            else:
                pan_only = re.sub(r"\D", "", text)
                if 13 <= len(pan_only) <= 19 and not re.search(r"[:|;/]", text):
                    draft = {
                        "country": def_cc,
                        "number": pan_only,
                        "payment_method": _detect_card_payment_method(pan_only),
                    }
                    _card_wizard_ask_exp(chat_id, draft)
                else:
                    _tg_send(
                        chat_id,
                        "⚠️ Не удалось распознать данные карты.\n"
                        "Отправьте <b>только номер карты</b> (13–19 цифр) или сразу <code>номер:мм/гг:cvv</code> (например: <code>4111222233334444:05/35:345</code>):",
                    )
    elif action == "cw_card_exp":
        draft = dict(st.get("draft") or {})
        m_exp = re.match(r"^\s*(\d{1,2})\s*[/\-:.\s]\s*(\d{2,4})\s*$", text)
        if not m_exp and re.fullmatch(r"\d{4}", text):
            m_exp = re.match(r"^(\d{2})(\d{2})$", text)
        if m_exp:
            mm_i = int(m_exp.group(1))
            yy_i = int(m_exp.group(2))
            if yy_i < 100:
                yy_i += 2000
            if 1 <= mm_i <= 12 and 2024 <= yy_i <= 2099:
                draft["exp_month"] = f"{mm_i:02d}"
                draft["exp_year"] = str(yy_i)
                _card_wizard_ask_cvv(chat_id, draft)
                return
        _tg_send(chat_id, "⚠️ Неверный срок действия. Отправьте в формате <code>ММ/ГГ</code> (например: <code>05/35</code>):")
    elif action == "cw_card_cvv":
        draft = dict(st.get("draft") or {})
        cvv_d = re.sub(r"\D", "", text)
        if 3 <= len(cvv_d) <= 4:
            draft["cvv"] = cvv_d
            _card_wizard_ask_type(chat_id, draft)
        else:
            _tg_send(chat_id, "⚠️ Код CVV/CVC должен состоять из 3 или 4 цифр. Попробуйте ещё раз:")
    elif action == "cw_wait_type":
        draft = dict(st.get("draft") or {})
        low_t = text.lower()
        if "visa" in low_t or "виза" in low_t:
            draft["payment_method"] = "visa"
            _card_wizard_ask_billing(chat_id, draft)
        elif "master" in low_t or "мастер" in low_t or low_t == "mc":
            draft["payment_method"] = "mastercard"
            _card_wizard_ask_billing(chat_id, draft)
        else:
            _tg_send(chat_id, "Нажмите кнопку <b>Visa</b> или <b>MasterCard</b> выше (или напишите <code>visa</code> / <code>mastercard</code>):")
    elif action == "cw_wait_billing_choice":
        _tg_send(chat_id, "Выберите один из вариантов кнопкой выше: автогенерация адреса с сайтов или ввод вручную.")
    elif action == "cw_wait_limit_action":
        _tg_send(chat_id, "Выберите действие при достижении лимита кнопкой выше (в неактив / удалить / безлимит).")
    elif action == "cw_limit_count":
        draft = dict(st.get("draft") or {})
        val_str = re.sub(r"\D", "", text)
        if val_str and int(val_str) > 0:
            draft["max_uses"] = int(val_str)
            draft["limit_configured"] = True
            _card_wizard_preview(chat_id, draft)
        else:
            _tg_send(chat_id, "⚠️ Введите целое число больше 0 (например: <code>1</code>, <code>3</code>, <code>5</code>):")
    elif action == "card_edit_limit_count":
        cid_card = st.get("card_id", "")
        lim_act = st.get("limit_action", "deactivate")
        val_str = re.sub(r"\D", "", text)
        if val_str and int(val_str) > 0:
            val = int(val_str)
            _waiting.pop(chat_id, None)
            cards = _load_cards()
            for c in cards:
                if c.get("id") == cid_card:
                    c["limit_action"] = lim_act
                    c["max_uses"] = val
                    break
            _save_cards(cards)
            _tg_send(chat_id, f"✅ Лимит карты <code>{cid_card}</code> обновлён: <b>{_limit_action_label(lim_act, val)}</b>.")
            _open_card_detail(chat_id, cid_card)
        else:
            _tg_send(chat_id, "⚠️ Введите целое число больше 0 (например: <code>1</code>, <code>3</code>, <code>5</code>):")
    elif action == "cw_confirm":
        _tg_send(chat_id, "Нажмите <b>«✅ Сохранить карту»</b> или выберите другое действие кнопкой выше.")
    elif action == "cw_billing_manual_input":
        draft = dict(st.get("draft") or {})
        if "|" in text:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) >= 6:
                draft["holder"] = parts[0]
                draft["address"] = parts[1]
                draft["address_two"] = "" if parts[2] == "-" else parts[2]
                draft["city"] = parts[3]
                draft["zip"] = parts[4]
                draft["phone"] = parts[5]
            elif len(parts) >= 5:
                draft["holder"] = parts[0]
                draft["address"] = parts[1]
                draft["address_two"] = ""
                draft["city"] = parts[2]
                draft["zip"] = parts[3]
                draft["phone"] = parts[4]
            else:
                _tg_send(
                    chat_id,
                    "⚠️ Укажите минимум 5 полей через <code>|</code>:\n"
                    "<code>Имя Фамилия | Адрес | Город | Индекс | Телефон</code>",
                )
                return
            draft["auto_billing"] = False
            draft["billing_source"] = "введено вручную"
            draft["billing_source_url"] = ""
            if draft.get("limit_configured"):
                _card_wizard_preview(chat_id, draft)
            else:
                _card_wizard_ask_limit(chat_id, draft)
        else:
            if len(text) < 2:
                _tg_send(chat_id, "⚠️ Введите Имя и Фамилию (например: <code>John Smit</code>):")
                return
            draft["holder"] = text
            _waiting[chat_id] = {"action": "cw_m_address", "draft": draft}
            _tg_send(chat_id, "🏠 Шаг 2/6 · Отправьте <b>Расчётный адрес</b> (улица, дом):", _make_kb([[("❌ Отмена", "src_cards")]]))
    elif action == "cw_m_address":
        draft = dict(st.get("draft") or {})
        draft["address"] = text
        _waiting[chat_id] = {"action": "cw_m_address2", "draft": draft}
        _tg_send(chat_id, "🏢 Шаг 3/6 · Отправьте <b>Расчётный адрес, строка 2</b> (или <code>-</code> чтобы пропустить):", _make_kb([[("❌ Отмена", "src_cards")]]))
    elif action == "cw_m_address2":
        draft = dict(st.get("draft") or {})
        draft["address_two"] = "" if text.strip() == "-" else text
        _waiting[chat_id] = {"action": "cw_m_city", "draft": draft}
        _tg_send(chat_id, "🌆 Шаг 4/6 · Отправьте <b>Город</b>:", _make_kb([[("❌ Отмена", "src_cards")]]))
    elif action == "cw_m_city":
        draft = dict(st.get("draft") or {})
        draft["city"] = text
        _waiting[chat_id] = {"action": "cw_m_zip", "draft": draft}
        _tg_send(chat_id, "📮 Шаг 5/6 · Отправьте <b>Почтовый индекс</b>:", _make_kb([[("❌ Отмена", "src_cards")]]))
    elif action == "cw_m_zip":
        draft = dict(st.get("draft") or {})
        draft["zip"] = text
        _waiting[chat_id] = {"action": "cw_m_phone", "draft": draft}
        _tg_send(chat_id, "📞 Шаг 6/6 · Отправьте <b>Телефон</b> (например: <code>+6587981783</code>):", _make_kb([[("❌ Отмена", "src_cards")]]))
    elif action == "cw_m_phone":
        draft = dict(st.get("draft") or {})
        draft["phone"] = text
        draft["auto_billing"] = False
        draft["billing_source"] = "введено вручную"
        draft["billing_source_url"] = ""
        if draft.get("limit_configured"):
            _card_wizard_preview(chat_id, draft)
        else:
            _card_wizard_ask_limit(chat_id, draft)
    elif action == "input_cards":
        def_cc = st.get("country", "ANY")
        _waiting.pop(chat_id, None)
        valid, invalid = parse_cards_text(text, default_country=def_cc)
        if valid:
            existing = _load_cards()
            combined, added_cnt = _merge_cards(existing, valid)
            _save_cards(combined)
            msg = f"✅ Добавлено <b>{added_cnt}</b> карт ({_country_display(def_cc)}). Всего в базе: <b>{len(combined)}</b>."
            if invalid:
                msg += f"\n⚠️ Пропущено строк с ошибкой формата: {len(invalid)}."
            _tg_send(chat_id, msg)
        else:
            _tg_send(
                chat_id,
                "⚠️ Валидных карт не найдено.\n"
                "Используйте формат <code>номер:мм/гг:cvv</code> (например: <code>4111222233334444:12/28:123</code>)."
            )
        _open_cards_menu(chat_id)
    elif action == "card_edit_note":
        cid_card = st.get("card_id", "")
        _waiting.pop(chat_id, None)
        cards = _load_cards()
        updated = False
        for c in cards:
            if c.get("id") == cid_card:
                if text == "-":
                    c["label"] = ""
                elif "|" in text:
                    parts = [p.strip() for p in text.split("|", 1)]
                    c["label"] = "" if parts[0] == "-" else parts[0]
                    lim_digits = re.sub(r"\D", "", parts[1])
                    if lim_digits:
                        c["max_uses"] = int(lim_digits)
                elif text.isdigit():
                    c["max_uses"] = int(text)
                else:
                    c["label"] = text
                updated = True
                break
        if updated:
            _save_cards(cards)
            _tg_send(chat_id, f"✅ Данные карты <code>{cid_card}</code> обновлены.")
        _open_card_detail(chat_id, cid_card)
    elif action == "lot_add_cards":
        lid = st.get("lot_id", "")
        def_cc = st.get("country", "ANY")
        _waiting.pop(chat_id, None)
        valid, invalid = parse_cards_text(text, default_country=def_cc)
        if valid:
            cfg = _load_config()
            lots = _get_lots_dict(cfg)
            if lid in lots:
                old_c = lots[lid].get("cards", [])
                comb, added_cnt = _merge_cards(old_c, valid)
                lots[lid]["cards"] = comb
                cfg["lots"] = lots
                _save_config(cfg)
                msg = f"✅ Добавлено <b>{added_cnt}</b> карт к лоту #{lid}. Всего у лота: <b>{len(comb)}</b>."
                if invalid:
                    msg += f"\n⚠️ Пропущено строк с ошибкой формата: {len(invalid)}."
                _tg_send(chat_id, msg)
        else:
            _tg_send(
                chat_id,
                "⚠️ Валидных карт не найдено.\n"
                "Используйте формат <code>номер:мм/гг:cvv</code>."
            )
        _open_lot_cards_menu(chat_id, lid)
    elif action == "games_input_cc":
        cc = re.sub(r'[^A-Za-z]', '', text).upper()
        if len(cc) == 2:
            _waiting.pop(chat_id, None)
            threading.Thread(target=_search_games_worker, args=(chat_id, cc, None, "src_games_menu", None), daemon=True).start()
        else:
            _tg_send(chat_id, "⚠️ Введите 2-буквенный код страны (например: <code>UA</code>, <code>KZ</code>, <code>JP</code>, <code>EU</code>):")
    elif action == "price_edit_val":
        cc = str(st.get("country", "US")).upper()
        info_base = REGION_CHEAP_PRICES.get(cc) or get_region_price_info(cc)
        if text.strip() in ("-", "reset", "сброс"):
            cfg = _load_config()
            custom = cfg.get("custom_region_prices")
            if isinstance(custom, dict) and cc in custom:
                custom.pop(cc, None)
                cfg["custom_region_prices"] = custom
                _save_config(cfg)
            _waiting.pop(chat_id, None)
            _tg_send(chat_id, f"🔄 Цена для <b>{info_base['name']} ({cc})</b> сброшена на стандартную: <b>{info_base['label']}</b>.")
            _open_games_menu(chat_id)
        else:
            tokens = [t.replace(",", ".") for t in re.split(r"[\s;|/]+", text.strip()) if t]
            try:
                target_val = float(tokens[0])
                if target_val <= 0:
                    raise ValueError("<=0")
                if len(tokens) >= 2:
                    maxp_val = max(1, int(round(float(tokens[1]))))
                else:
                    maxp_val = max(1, int(round(target_val * 3.0)))
                cfg = _load_config()
                custom = cfg.get("custom_region_prices")
                if not isinstance(custom, dict):
                    custom = {}
                cur_sym = info_base.get("symbol") or info_base.get("currency", "USD")
                lbl_num = f"{target_val:g}".replace(".", ",")
                custom[cc] = {
                    "target_price": target_val,
                    "maxprice": maxp_val,
                }
                cfg["custom_region_prices"] = custom
                _save_config(cfg)
                _waiting.pop(chat_id, None)
                _tg_send(
                    chat_id,
                    f"✅ <b>Цена обновлена для {info_base['name']} ({cc})!</b>\n\n"
                    f"• Новая цена: <b>{lbl_num} {cur_sym}</b>",
                )
                _open_games_menu(chat_id)
            except Exception:
                _tg_send(
                    chat_id,
                    "⚠️ Не удалось распознать число.\n"
                    "Отправьте например <code>20</code> или <code>0.75</code> (или <code>-</code> для сброса):",
                )
    elif action == "input_search_mult":
        raw_num = text.strip().lower().lstrip("x×").replace(",", ".")
        try:
            val = float(raw_num)
            if not (1.0 <= val <= 50.0):
                raise ValueError("out of range")
            val = round(val, 2)
            cfg = _load_config()
            cfg["search_max_price_multiplier"] = val
            _save_config(cfg)
            _waiting.pop(chat_id, None)
            _tg_send(chat_id, f"✅ Фиксация максимальной цены установлена: <b>×{val:g}</b> от цены региона.")
            _open_games_menu(chat_id)
        except Exception:
            _tg_send(chat_id, "⚠️ Введите число от <code>1</code> до <code>50</code> (например: <code>1.5</code>, <code>2</code>, <code>3.5</code>):")
    elif action == "input_workers":
        val_str = re.sub(r'\D', '', text)
        if val_str and int(val_str) > 0:
            val = min(50, max(1, int(val_str)))
            cfg = _load_config()
            cfg["max_workers"] = val
            _save_config(cfg)
            _waiting.pop(chat_id, None)
            _tg_send(chat_id, f"✅ Установлено одновременных смен: <b>{val}</b>.")
            _open_queue_menu(chat_id)
        else:
            _tg_send(chat_id, "⚠️ Введите целое число больше 0 (например: <code>5</code> или <code>10</code>):")
    elif action == "input_timeout":
        val_str = re.sub(r'[^\d.]', '', text)
        try:
            val = float(val_str)
            if 5.0 <= val <= 300.0:
                cfg = _load_config()
                cfg["request_timeout"] = val
                _save_config(cfg)
                _waiting.pop(chat_id, None)
                _tg_send(chat_id, f"✅ Установлен таймаут запросов: <b>{int(val)} сек</b>.")
                _open_queue_menu(chat_id)
            else:
                _tg_send(chat_id, "⚠️ Таймаут должен быть в диапазоне от 5 до 300 секунд.")
        except Exception:
            _tg_send(chat_id, "⚠️ Введите число секунд (например: <code>30</code> или <code>60</code>):")
    elif action == "edit_buyer_message":
        key = st.get("key", "")
        _waiting.pop(chat_id, None)
        if key in BUYER_MESSAGE_LABELS and text:
            cfg = _load_config()
            bm = cfg.get("buyer_messages") or {}
            bm[key] = text
            cfg["buyer_messages"] = bm
            _save_config(cfg)
            _tg_send(
                chat_id,
                f"✅ Шаблон <b>«{BUYER_MESSAGE_LABELS[key]}»</b> успешно обновлен!",
                _make_kb([[("💬 К списку сообщений", "src_cat_messages")]])
            )
        else:
            _tg_send(chat_id, "⚠️ Текст не может быть пустым.")
            _open_messages_menu(chat_id)
_buyer_sessions: dict[Any, dict[str, Any]] = {}
_active_sessions: list[dict[str, Any]] = []
_sessions_lock = threading.Lock()
_terminated_orders: set[str] = set()
_terminated_orders_lock = threading.Lock()

def _terminate_buyer_session(order_id: Any, reason: str = "", *, chat_id: Any = None) -> bool:
    if not order_id:
        return False
    oid = str(order_id).strip().lstrip('#')
    _cancel_qr_worker(oid, reason=f"terminate_{reason}")
    with _terminated_orders_lock:
        _terminated_orders.add(oid)

    terminated_any = False
    with _sessions_lock:
        to_del = [k for k, v in _buyer_sessions.items() if str(v.get("order_id") or "").strip().lstrip('#') == oid]
        matched_sessions = [s for s in _active_sessions if str(s.get("order_id") or "").strip().lstrip('#') == oid]
        for s in matched_sessions:
            s["canceled"] = True
            s["step"] = "finished"
            c_evt = s.get("cancel_qr_event")
            if c_evt and isinstance(c_evt, threading.Event):
                c_evt.set()
            g_evt = s.get("guard_event")
            if g_evt and isinstance(g_evt, threading.Event):
                g_evt.set()
            terminated_any = True

        for k in to_del:
            _buyer_sessions.pop(k, None)
        _active_sessions[:] = [s for s in _active_sessions if str(s.get("order_id") or "").strip().lstrip('#') != oid]

        if chat_id is not None:
            to_del_chat = [
                k for k, v in _buyer_sessions.items()
                if str(v.get("chat_id") or "") == str(chat_id) and str(v.get("order_id") or "").strip().lstrip('#') == oid
            ]
            for k in to_del_chat:
                _buyer_sessions.pop(k, None)

    _log_event("session_terminated", order_id=oid, reason=reason)
    return terminated_any

def _extract_session_keys(sess: dict[str, Any]) -> set[Any]:
    keys: set[Any] = set()
    cid = sess.get("chat_id")
    if cid is not None:
        keys.add(cid)
        keys.add(str(cid))
        nums = re.findall(r'\d+', str(cid))
        for num in nums:
            try:
                keys.add(int(num))
            except Exception:
                pass
            keys.add(str(num))
    bid = sess.get("buyer_id")
    if bid is not None:
        try:
            keys.add(int(bid))
        except Exception:
            pass
        keys.add(str(bid))
    bname = str(sess.get("buyer_username") or "").strip().lower()
    if bname and bname != '?':
        keys.add(f"user:{bname}")
    oid = str(sess.get("order_id") or "").strip()
    if oid:
        if oid.startswith('#'):
            oid = oid[1:]
        keys.add(f"order:{oid}")
        keys.add(f"order:#{oid}")
        keys.add(oid)
    return keys
def _register_buyer_session(sess: dict[str, Any], buyer_id: Any = None) -> None:
    if buyer_id is not None:
        sess["buyer_id"] = buyer_id
    oid = str(sess.get("order_id") or "").strip().lstrip('#')
    if oid:
        with _terminated_orders_lock:
            _terminated_orders.discard(oid)
    with _sessions_lock:
        raw_oid = str(sess.get("order_id") or "")
        _active_sessions[:] = [
            s for s in _active_sessions
            if str(s.get("order_id") or "") != raw_oid and s.get("step") not in ("finished", "refunded", "failed_closed")
        ]
        _active_sessions.append(sess)
        to_del = [k for k, v in _buyer_sessions.items() if str(v.get("order_id") or "") == raw_oid]
        for k in to_del:
            _buyer_sessions.pop(k, None)
        for k in _extract_session_keys(sess):
            _buyer_sessions[k] = sess
def _find_buyer_session(
    chat_id: Any = None,
    chat_name: Any = None,
    author: Any = None,
    author_id: Any = None,
    order_id: Any = None
) -> dict[str, Any] | None:
    with _sessions_lock:
        if order_id:
            target_oid = str(order_id).strip().lstrip('#')
            with _terminated_orders_lock:
                if target_oid in _terminated_orders:
                    return None
            for s in _active_sessions:
                if str(s.get("order_id") or "").strip().lstrip('#') == target_oid:
                    if s.get("step") not in ("finished", "refunded", "failed_closed"):
                        return s
        candidates: list[Any] = []
        if chat_id is not None:
            candidates.extend([chat_id, str(chat_id)])
            try:
                candidates.append(int(chat_id))
            except Exception:
                pass
            for num in re.findall(r'\d+', str(chat_id)):
                try:
                    candidates.append(int(num))
                except Exception:
                    pass
                candidates.append(str(num))
        if author_id is not None:
            candidates.extend([author_id, str(author_id)])
            try:
                candidates.append(int(author_id))
            except Exception:
                pass
        if chat_name:
            candidates.append(f"user:{str(chat_name).strip().lower()}")
        if author:
            candidates.append(f"user:{str(author).strip().lower()}")
        matching: list[dict[str, Any]] = []
        for s in _active_sessions:
            if s.get("step") in ("finished", "refunded", "failed_closed"):
                continue
            s_oid = str(s.get("order_id") or "").strip().lstrip('#')
            with _terminated_orders_lock:
                if s_oid in _terminated_orders:
                    continue
            s_keys = _extract_session_keys(s)
            if any(cand in s_keys for cand in candidates):
                matching.append(s)
        if not matching:
            return None
        if len(matching) == 1:
            return matching[0]
        input_waiting = [s for s in matching if s.get("step") in (
            "waiting_choice", "waiting_qr_ready", "waiting_qr", "waiting_qr_timeout",
            "waiting_qr_or_creds", "waiting_guard", "waiting_push", "waiting_password", "waiting_login", "waiting_credentials"
        )]
        if input_waiting:
            return input_waiting[0]
        return matching[-1]
def _is_valid_steam_login(s: str) -> bool:
    val = str(s or '').strip()
    if not (2 <= len(val) <= 64):
        return False
    if any(c in val for c in (' ', '\t', '\n', '\r')):
        return False
    try:
        val.encode('ascii')
    except UnicodeEncodeError:
        return False
    if any(c in val for c in ('@', '!', '?', ',', ';', '/', '\\', '|', '<', '>', '"', "'", '`', '(', ')', '[', ']', '{', '}', '#', '$', '%', '^', '&', '*', '=')):
        return False
    return True
def _extract_creds_from_text(text: str) -> tuple[str, str] | None:
    txt = (text or "").strip()
    if not txt:
        return None
    if ":" in txt:
        parts = txt.split(":", 1)
        p0, p1 = parts[0].strip(), parts[1].strip()
        if _is_valid_steam_login(p0) and p1:
            return (p0, p1)
    if "\n" in txt:
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        if len(lines) >= 2 and _is_valid_steam_login(lines[0]) and lines[1]:
            return (lines[0], lines[1])
    return None

_CYRILLIC_TO_LATIN_HOMOGLYPHS = {
    'А': 'A', 'а': 'A',
    'В': 'B', 'в': 'B',
    'С': 'C', 'с': 'C',
    'Е': 'E', 'е': 'E',
    'Н': 'H', 'н': 'H',
    'К': 'K', 'к': 'K',
    'М': 'M', 'м': 'M',
    'О': 'O', 'о': 'O',
    'Р': 'P', 'р': 'P',
    'Т': 'T', 'т': 'T',
    'Х': 'X', 'х': 'X',
    'У': 'Y', 'у': 'Y',
}

def _normalize_guard_string(s: str) -> str:
    return ''.join(_CYRILLIC_TO_LATIN_HOMOGLYPHS.get(c, c) for c in (s or ""))

def _extract_guard_code(text: str) -> str | None:
    txt = (text or "").strip()
    if not txt:
        return None
    clean_pure = re.sub(r'[^A-Za-z0-9]', '', txt).upper()
    if len(clean_pure) == 5:
        return clean_pure
    norm_txt = _normalize_guard_string(txt)
    clean_norm = re.sub(r'[^A-Za-z0-9]', '', norm_txt).upper()
    if len(clean_norm) == 5:
        return clean_norm
    ignore_tokens = {
        "STEAM", "GUARD", "ENTER", "HELLO", "CHECK", "LOGIN", "RESET", "AGAIN",
        "READY", "START", "ADMIN", "TOKEN", "ORDER", "EMAIL", "PHONE", "CLICK"
    }
    tokens = re.findall(r'\b([A-Za-z0-9]{5})\b', norm_txt)
    valid_tokens = [t.upper() for t in tokens if t.upper() not in ignore_tokens]
    if len(valid_tokens) == 1:
        return valid_tokens[0]
    kw_match = re.search(r'(?:код(?:ик)?|code|guard)[\s:=#\-]+([A-Za-z0-9]{5})\b', norm_txt, re.IGNORECASE)
    if kw_match:
        return kw_match.group(1).upper()
    return None
def _clean_funpay_text(text: str) -> str:
    s = re.sub(r'<[^>]+>', '', text)
    s = html.unescape(s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()
def _send_buyer_fp_msg(cardinal, chat_id, text: str, buyer_username: str | None = None) -> bool:
    card = cardinal or cardinal_instance
    acc = getattr(card, 'account', None) if card else None
    if not acc:
        logger.error(f"[{NAME}] send_message: аккаунт Cardinal недоступен")
        return False
    cid = chat_id
    bname = buyer_username if (buyer_username and buyer_username != "?") else None
    if (cid is None or str(cid).strip() in ("", "?")) and bname:
        try:
            chat = acc.get_chat_by_name(bname, True)
            if chat:
                cid = int(chat.id)
        except Exception as e:
            logger.warning(f"[{NAME}] get_chat_by_name({bname}): {e}")
    if cid is None:
        logger.error(f"[{NAME}] Нет chat_id для отправки сообщения покупателю {buyer_username}")
        return False
    target_cids = [cid]
    if isinstance(cid, str) and cid.isdigit():
        target_cids = [int(cid), cid]
    clean_text = _clean_funpay_text(text)
    for attempt in range(3):
        for target in target_cids:
            try:
                if card and hasattr(card, "send_message"):
                    res = card.send_message(target, clean_text, chat_name=bname, watermark=False)
                    if res is not None:
                        return True
                else:
                    acc.send_message(target, clean_text, chat_name=bname)
                    return True
            except Exception as e:
                logger.warning(f"[{NAME}] Ошибка отправки сообщения в FunPay (target={target}, попытка {attempt + 1}): {e}")
                time.sleep(0.5)
        if attempt == 0 and bname:
            try:
                chat = acc.get_chat_by_name(bname, True)
                if chat and getattr(chat, 'id', None):
                    new_cid = int(chat.id)
                    if new_cid not in target_cids:
                        target_cids.insert(0, new_cid)
            except Exception:
                pass
        time.sleep(1)
    return False
def _try_refund(cardinal, order_id: str | int) -> bool:
    acc = getattr(cardinal, 'account', None) if cardinal else None
    if not acc:
        acc = getattr(cardinal_instance, 'account', None) if cardinal_instance else None
    if not acc:
        logger.error(f"[{NAME}] Не удалось выполнить возврат #{order_id}: аккаунт Cardinal недоступен")
        return False
    try:
        oid = str(order_id).strip()
        if oid.startswith('#'):
            oid = oid[1:]
        acc.refund(oid)
        logger.info(f"[{NAME}] Возврат средств по заказу #{oid} успешно выполнен")
        return True
    except Exception as e:
        logger.error(f"[{NAME}] Ошибка возврата средств #{order_id}: {e}")
        return False
def _set_funpay_lot_active(cardinal, lot_id: str | int, active: bool) -> bool:
    acc = getattr(cardinal, 'account', None) if cardinal else None
    if not acc:
        acc = getattr(cardinal_instance, 'account', None) if cardinal_instance else None
    if not acc:
        return False
    try:
        lf = acc.get_lot_fields(int(lot_id))
        if getattr(lf, "active", None) == active:
            return True
        lf.active = bool(active)
        renew = getattr(lf, "renew_fields", None)
        if callable(renew):
            renewed = renew()
            if renewed is not None:
                lf = renewed
        acc.save_lot(lf)
        logger.info(f"[{NAME}] Статус лота #{lot_id} на FunPay изменён: active={active}")
        try:
            extracted_title = _extract_lot_fields_title(lf)
            if extracted_title:
                cfg_t = _load_config()
                lots_t = _get_lots_dict(cfg_t)
                lid_s = str(lot_id).strip().lstrip('#')
                if lid_s in lots_t:
                    cur_t = str(lots_t[lid_s].get("title") or "").strip()
                    if not cur_t or cur_t.lower().startswith("лот #") or cur_t.lower().startswith("лот "):
                        lots_t[lid_s]["title"] = extracted_title
                        cfg_t["lots"] = lots_t
                        _save_config(cfg_t)
        except Exception:
            pass
        try:
            c_obj = cardinal or cardinal_instance
            if c_obj and callable(getattr(c_obj, "update_lots_and_categories", None)):
                threading.Thread(target=c_obj.update_lots_and_categories, daemon=True).start()
        except Exception:
            pass
        return True
    except Exception as e:
        logger.warning(f"[{NAME}] Ошибка изменения активности лота #{lot_id} на FunPay: {e}")
        return False
def _deactivate_funpay_lot(cardinal, lot_id: str | int) -> bool:
    return _set_funpay_lot_active(cardinal, lot_id, False)

def _delete_funpay_lot(cardinal, lot_id: str | int) -> bool:
    acc = getattr(cardinal, 'account', None) if cardinal else None
    if not acc:
        acc = getattr(cardinal_instance, 'account', None) if cardinal_instance else None
    if not acc:
        return False
    try:
        lid_int = int(str(lot_id).strip().lstrip('#'))
        if hasattr(acc, "delete_lot"):
            acc.delete_lot(lid_int)
        else:
            try:
                from FunPayAPI.types import LotFields
                acc.save_lot(LotFields(lid_int, {"csrf_token": getattr(acc, "csrf_token", ""), "offer_id": lid_int, "deleted": "1"}))
            except Exception:
                acc.method("post", "https://funpay.com/lots/offerEdit", {"csrf_token": getattr(acc, "csrf_token", ""), "offer_id": lid_int, "deleted": "1"})
        logger.info(f"[{NAME}] Лот #{lot_id} удалён с FunPay")
        return True
    except Exception as e:
        logger.warning(f"[{NAME}] Ошибка удаления лота #{lot_id} с FunPay: {e}")
        return False
PROCESSED_ORDERS_FILE = os.path.join(STORAGE_DIR, "processed_orders.json")
_processed_orders_list: list[str] = []
_processed_orders: set[str] = set()
_processed_orders_lock = threading.Lock()

def _load_processed_orders() -> None:
    global _processed_orders_list, _processed_orders
    with _processed_orders_lock:
        if os.path.exists(PROCESSED_ORDERS_FILE):
            try:
                with open(PROCESSED_ORDERS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    _processed_orders_list = [str(x).strip().lstrip('#') for x in data[-2000:] if x]
                    _processed_orders = set(_processed_orders_list)
            except Exception as e:
                logger.warning(f"[{NAME}] Ошибка чтения {PROCESSED_ORDERS_FILE}: {e}")
def _mark_order_processed(oid: str) -> None:
    oid = str(oid).strip().lstrip('#')
    if not oid:
        return
    with _processed_orders_lock:
        if oid not in _processed_orders:
            _processed_orders.add(oid)
            _processed_orders_list.append(oid)
            if len(_processed_orders_list) > 2000:
                old = _processed_orders_list.pop(0)
                _processed_orders.discard(old)
            try:
                with open(PROCESSED_ORDERS_FILE, "w", encoding="utf-8") as f:
                    json.dump(_processed_orders_list, f, indent=2)
            except Exception as e:
                logger.error(f"[{NAME}] Ошибка записи {PROCESSED_ORDERS_FILE}: {e}")
def _is_order_processed(oid: str) -> bool:
    oid = str(oid).strip().lstrip('#')
    with _processed_orders_lock:
        return oid in _processed_orders
ORDER_PAID_RE = re.compile(r'оплатил(?:а)?\s+заказ\s*#([A-Za-z0-9]+)', re.IGNORECASE)
_REFUND_RE = re.compile(r'(?:вернул(?:а)?\s+деньги|возврат\s+средств|отменен[оа]?|отменён[оа]?).*?#([A-Za-z0-9]+)', re.IGNORECASE)
_CONFIRM_RE = re.compile(r'(?:подтвердил(?:а)?\s+.*?(?:заказ|завершение|выполнение).*?#([A-Za-z0-9]+)|заказ\s*#([A-Za-z0-9]+)\s*(?:подтвержден|подтверждён|закрыт|завершен|завершён))', re.IGNORECASE)

_TOPUP_MARKERS = [
    "пополнение", "пополнить", "баланс", "пополнения", "по логину",
    "steam wallet", "кошелек steam", "кошелёк steam", "код кошелька",
    "пополнение steam", "top-up", "topup"
]
_RC_EXPLICIT_MARKERS = [
    "смена региона", "смену региона", "сменить регион",
    "поменять регион", "перевод региона", "перевести регион",
    "перенос региона", "change region", "region change"
]

def _normalize_title(t: str) -> str:
    if not t:
        return ""
    s = str(t).strip().casefold()
    s = re.sub(r'\s+', ' ', s)
    return s.strip(" \t\r\n.,;:|!?-–—")

def _extract_lot_fields_title(lf: Any) -> str:
    if not lf:
        return ""
    for attr in ("title_ru", "title_en", "description", "title"):
        val = getattr(lf, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    fields = getattr(lf, "fields", None)
    if isinstance(fields, dict):
        for key in ("fields[summary][ru]", "fields[summary][en]"):
            val = fields.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""

def _lot_title_matches_order(title_norm: str, order_desc_candidates: list[str], order_text: str = "") -> bool:
    if not title_norm or len(title_norm) < 3:
        return False
    for odc in order_desc_candidates:
        odc_norm = _normalize_title(odc)
        if not odc_norm:
            continue
        if odc_norm == title_norm:
            return True
        if odc_norm.startswith(title_norm + ",") or odc_norm.startswith(title_norm + " ,"):
            return True
        if odc_norm.endswith(", " + title_norm) or odc_norm.endswith("," + title_norm):
            return True
        parts = [_normalize_title(p) for p in odc.split(",") if p.strip()]
        if title_norm in parts:
            return True
    if order_text and len(title_norm) >= 4:
        txt_norm = _normalize_title(order_text)
        if re.search(r'(?:^|[\.,\|;\n\r])\s*' + re.escape(title_norm) + r'\s*(?:$|[\.,\|;\n\r])', txt_norm):
            return True
    return False

def _get_seller_profile_lots(cardinal, force_subcat_fetch: bool = False) -> list[Any]:
    if not cardinal:
        return []
    res: list[Any] = []
    seen: set[str] = set()
    for p_obj in (getattr(cardinal, 'profile', None), getattr(cardinal, 'curr_profile', None)):
        if p_obj:
            if hasattr(p_obj, 'get_sorted_lots'):
                try:
                    sl_dict = p_obj.get_sorted_lots(1)
                    if isinstance(sl_dict, dict):
                        for item in sl_dict.values():
                            iid = str(getattr(item, 'id', '') or '')
                            if iid and iid not in seen:
                                seen.add(iid)
                                res.append(item)
                except Exception:
                    pass
            if hasattr(p_obj, 'get_lots'):
                try:
                    gl = p_obj.get_lots() or []
                    for item in gl:
                        iid = str(getattr(item, 'id', '') or '')
                        if iid and iid not in seen:
                            seen.add(iid)
                            res.append(item)
                except Exception:
                    pass
    if (force_subcat_fetch or not res) and getattr(cardinal, 'account', None):
        try:
            gl = cardinal.account.get_my_subcategory_lots(2044) or []
            for item in gl:
                iid = str(getattr(item, 'id', '') or '')
                if iid and iid not in seen:
                    seen.add(iid)
                    res.append(item)
        except Exception:
            pass
    return res

def _find_lot_for_order(cardinal, order, event=None, order_text: str = "") -> tuple[str | None, dict[str, Any] | None, str]:
    cfg = _load_config()
    lots = _get_lots_dict(cfg)
    if not lots:
        return (None, None, "no_configured_lots")

    order_id_clean = str(getattr(order, 'id', '') or getattr(event, 'order_id', '') or getattr(event, 'id', '') or '').lstrip('#').strip()
    subcat = getattr(order, 'subcategory', None)
    subcat_id = getattr(subcat, 'id', None) or getattr(order, 'subcategory_id', None)
    subcat_id_clean = str(subcat_id or '2044')

    objects = [x for x in (order, event) if x is not None]
    for parent in list(objects):
        for nested_name in ('lot', 'offer', 'lot_shortcut', 'offer_shortcut', 'node', 'fields', 'data'):
            nested = parent.get(nested_name) if isinstance(parent, dict) else getattr(parent, nested_name, None)
            if nested is not None and all(nested is not obj for obj in objects):
                objects.append(nested)

    candidate_lot_ids: list[str] = []
    seen_candidates: set[str] = set()

    def _add_cand(val: Any) -> None:
        if val is None or isinstance(val, bool):
            return
        try:
            s = str(val).strip()
            if s.isdigit() and int(s) > 0:
                if s != "2044" and s != subcat_id_clean and s != order_id_clean and s not in seen_candidates:
                    seen_candidates.add(s)
                    candidate_lot_ids.append(s)
        except Exception:
            pass

    for obj in objects:
        for name in ('lot_id', 'offer_id'):
            v = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
            _add_cand(v)
        if obj is not order and obj is not event:
            v = obj.get('id') if isinstance(obj, dict) else getattr(obj, 'id', None)
            _add_cand(v)

    for obj in (order, event):
        html_val = obj.get('html') if isinstance(obj, dict) else getattr(obj, 'html', None)
        if isinstance(html_val, str) and html_val:
            for m in re.finditer(r'(?:offer\?id=|data-offer=["\']|data-lot-id=["\'])(\d+)', html_val):
                _add_cand(m.group(1))

    if candidate_lot_ids:
        for cand_id in candidate_lot_ids:
            if cand_id in lots:
                lcfg = lots[cand_id]
                if not lcfg.get("enabled", True):
                    return (None, None, f"lot_disabled_{cand_id}")
                return (cand_id, lcfg, "direct_candidate_match")

        return (None, None, f"different_lot_id_ignored_{candidate_lot_ids[0]}")

    texts: list[str] = []
    for s in [
        getattr(order, 'short_description', None),
        getattr(order, 'description', None),
        getattr(order, 'title', None),
        getattr(order, 'full_description', None),
        getattr(order, 'subcategory_name', None),
        order_text,
    ]:
        txt = str(s or '').strip()
        if txt and txt not in texts:
            texts.append(txt)
    combined_texts = " ".join(texts).lower()

    is_topup = any(m in combined_texts for m in _TOPUP_MARKERS) and not any(m in combined_texts for m in _RC_EXPLICIT_MARKERS)
    if is_topup:
        return (None, None, "topup_order_ignored")

    subcat_name = str(getattr(subcat, 'name', '') or getattr(order, 'subcategory_name', '') or '').lower()

    is_rc_cat = False
    if subcat_id is not None and str(subcat_id) == "2044":
        is_rc_cat = True
    elif any(p in subcat_name for p in ("смена региона", "region change")):
        is_rc_cat = True

    if subcat_id is not None and str(subcat_id) != "2044" and not is_rc_cat:
        return (None, None, "different_category_id_ignored")
    if subcat_name and not is_rc_cat:
        if any(other in subcat_name for other in ("пополнение", "баланс", "аккаунт", "ключ", "гифт", "gift", "wallet", "предмет", "валют")):
            return (None, None, "different_category_name_ignored")

    if not is_rc_cat and not any(m in combined_texts for m in _RC_EXPLICIT_MARKERS):
        return (None, None, "not_region_change_order")

    order_desc_candidates: list[str] = []
    for s in [
        getattr(order, 'short_description', None),
        getattr(order, 'description', None),
        getattr(order, 'title', None),
    ]:
        if s:
            sc = str(s).strip()
            if sc and sc not in order_desc_candidates:
                order_desc_candidates.append(sc)

    def _match_from_seller_lots(seller_lots_list: list[Any]) -> str | None:
        sorted_lots = sorted(
            seller_lots_list,
            key=lambda l: len(str(getattr(l, 'description', None) or getattr(l, 'title', None) or '')),
            reverse=True,
        )
        for s_lot in sorted_lots:
            slid = str(getattr(s_lot, 'id', '') or getattr(s_lot, 'offer_id', '') or '').strip()
            sdesc = str(getattr(s_lot, 'description', None) or getattr(s_lot, 'title', None) or '').strip()
            if not slid or not sdesc:
                continue
            sdesc_norm = _normalize_title(sdesc)
            if _lot_title_matches_order(sdesc_norm, order_desc_candidates, order_text):
                return slid
        return None

    seller_lots = _get_seller_profile_lots(cardinal, force_subcat_fetch=False)
    matched_seller_lot_id = _match_from_seller_lots(seller_lots)
    if not matched_seller_lot_id:
        seller_lots_fresh = _get_seller_profile_lots(cardinal, force_subcat_fetch=True)
        if len(seller_lots_fresh) > len(seller_lots):
            matched_seller_lot_id = _match_from_seller_lots(seller_lots_fresh)

    if matched_seller_lot_id:
        if matched_seller_lot_id in lots:
            lcfg = lots[matched_seller_lot_id]
            if not lcfg.get("enabled", True):
                return (None, None, f"lot_disabled_{matched_seller_lot_id}")
            return (matched_seller_lot_id, lcfg, "seller_lot_match")
        else:
            return (None, None, f"other_seller_lot_ignored_{matched_seller_lot_id}")

    cfg_titles_updated = False
    acc_obj = getattr(cardinal, 'account', None) if cardinal else None
    for lid, lcfg in lots.items():
        ltitle = str(lcfg.get("title") or '').strip()
        is_placeholder = (not ltitle) or ltitle.lower().startswith("лот ") or ltitle.lower().startswith("лот #")
        if is_placeholder and acc_obj and str(lid).isdigit():
            try:
                lf = acc_obj.get_lot_fields(int(lid))
                fetched_title = _extract_lot_fields_title(lf)
                if fetched_title:
                    ltitle = fetched_title
                    lcfg["title"] = fetched_title
                    cfg_titles_updated = True
            except Exception as e:
                logger.debug(f"[{NAME}] get_lot_fields({lid}) during order match failed: {e}")
        if not ltitle or ltitle.lower().startswith("лот ") or ltitle.lower().startswith("лот #"):
            continue
        ltitle_norm = _normalize_title(ltitle)
        if len(ltitle_norm) < 3:
            continue

        if _lot_title_matches_order(ltitle_norm, order_desc_candidates, order_text):
            if cfg_titles_updated:
                cfg["lots"] = lots
                _save_config(cfg)
            if not lcfg.get("enabled", True):
                return (None, None, f"lot_disabled_{lid}")
            return (lid, lcfg, "config_title_strict_match")

    if cfg_titles_updated:
        cfg["lots"] = lots
        _save_config(cfg)

    return (None, None, "not_configured_plugin_lot")

def _process_incoming_order(cardinal, order, event=None, order_text: str = ""):
    global cardinal_instance
    if cardinal_instance is None:
        cardinal_instance = cardinal
    cfg = _load_config()
    if not cfg.get("plugin_enabled", True):
        return
    oid = str(getattr(order, 'id', '') or getattr(event, 'order_id', '') or getattr(event, 'id', '') or '').strip()
    if oid.startswith('#'):
        oid = oid[1:]
    if not oid:
        return
    if _is_order_processed(oid):
        return
    buyer_name = str(
        getattr(order, 'buyer_username', None)
        or getattr(event, 'buyer_username', None)
        or getattr(event, 'user_name', None)
        or '?'
    ).strip()
    buyer_id = (
        getattr(order, 'buyer_id', None)
        or getattr(event, 'buyer_id', None)
        or getattr(event, 'user_id', None)
    )
    desc_sample = str(getattr(order, 'description', None) or order_text or '')[:100]
    _log_event("order_received", order_id=oid, buyer=buyer_name, desc=desc_sample)
    lot_id, lot_cfg, match_reason = _find_lot_for_order(cardinal, order, event, order_text)
    if not lot_id or not lot_cfg:
        _log_event("order_miss", level=logging.INFO, order_id=oid, reason=match_reason, configured_lots=list(_get_lots_dict(cfg).keys()))
        return
    country = lot_cfg.get("country", "KZ")
    _log_event("order_matched", order_id=oid, lot_id=lot_id, method=match_reason, country=country)
    if not lot_cfg.get("enabled", False):
        _log_event("lot_disabled", level=logging.WARNING, order_id=oid, lot_id=lot_id)
        return
    chat_id = getattr(order, 'chat_id', None) or getattr(event, 'chat_id', None)
    if not chat_id and getattr(cardinal, 'account', None):
        try:
            full_ord = cardinal.account.get_order(oid)
            chat_id = getattr(full_ord, 'chat_id', None)
            if not buyer_name or buyer_name == '?':
                buyer_name = str(getattr(full_ord, 'buyer_username', '?') or '?')
            if not buyer_id:
                buyer_id = getattr(full_ord, 'buyer_id', None)
        except Exception as e:
            logger.warning(f"[{NAME}] get_order({oid}) fallback: {e}")
    if (not chat_id or not str(chat_id).isdigit()) and buyer_name and buyer_name != '?' and getattr(cardinal, 'account', None):
        try:
            chat = cardinal.account.get_chat_by_name(buyer_name, True)
            if chat and getattr(chat, 'id', None):
                chat_id = chat.id
        except Exception as e:
            logger.debug(f"[{NAME}] get_chat_by_name({buyer_name}) fallback: {e}")
    if not chat_id:
        _log_event("order_no_chat_id", level=logging.WARNING, order_id=oid, lot_id=lot_id)
        return
    country_name = _country_display(country)
    proxies = list(lot_cfg.get("proxies") or []) or _get_proxies_for_country(country, include_any=True)
    if not proxies:
        _mark_order_processed(oid)
        _log_event("order_rejected", level=logging.WARNING, order_id=oid, lot_id=lot_id, reason="no_proxies")
        if cfg.get("auto_deactivate_lots", True):
            lot_cfg["enabled"] = False
            cfg["lots"][str(lot_id)] = lot_cfg
            _save_config(cfg)
            _deactivate_funpay_lot(cardinal, lot_id)
            _log_event("lot_auto_deactivated", level=logging.WARNING, lot_id=lot_id, reason="proxy_pool_empty")
        refund_enabled = should_refund(FailureReason.PROXY_EXHAUSTED, order_id=oid)
        refund_ok = False
        if refund_enabled:
            refund_ok = _try_refund(cardinal, oid)
            _log_event("refund_result", order_id=oid, reason=FailureReason.PROXY_EXHAUSTED, success=refund_ok)
        if refund_ok:
            ref_status_str = "✅ выполнен"
        elif not refund_enabled:
            ref_status_str = "❌ отключён в настройках"
        else:
            ref_status_str = "❌ сбой выполнения автовозврата"
        msg_key = "no_proxies_refund" if refund_ok else "no_proxies_no_refund"
        _send_buyer_fp_msg(cardinal, chat_id, _render_buyer_msg(msg_key, order_id=oid, country_name=country_name), buyer_username=buyer_name)
        _log_event("order_refund", level=logging.WARNING, order_id=oid, reason="no_proxies", refund_ok=refund_ok)
        _notify_tg(
            f"🚨 <b>ОШИБКА · Нет прокси</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Заказ:</b> <code>#{oid}</code>\n"
            f"👤 <b>Покупатель:</b> {html.escape(str(buyer_name or '?'))}\n"
            f"🌍 <b>Регион:</b> {country_name} (<code>{country}</code>)\n"
            f"📋 <b>Причина:</b> у лота и в общей базе нет рабочих прокси\n"
            f"💸 <b>Автовозврат:</b> {ref_status_str}",
            ntype="error",
        )
        return
    qr_mode = _get_qr_mode(cfg)
    auto_pay_lbl = "💳 Автооплата картой" if cfg.get("auto_checkout_card", True) else "🛒 Только корзина"
    avail_card, _ = _select_card_for_order(lot_cfg, country)
    card_avail_lbl = f"<code>{_mask_card_number(avail_card.get('number', ''))}</code>" if avail_card else "⚠️ нет в базе"
    sess = {
        "order_id": oid,
        "buyer_username": buyer_name,
        "buyer_id": buyer_id,
        "lot_id": str(lot_id),
        "lot_cfg": lot_cfg,
        "login_mode": qr_mode,
        "cancel_qr_event": threading.Event(),
        "chat_id": chat_id,
        "country": country,
        "proxies": proxies,
        "created_at": time.time(),
    }

    if qr_mode == "choice":
        sess["step"] = "waiting_choice"
        _register_buyer_session(sess, buyer_id=buyer_id)
        _mark_order_processed(oid)
        msg = _render_buyer_msg("welcome_menu", country_name=country_name, order_id=oid, buyer_username=buyer_name)
        if not msg:
            msg = _render_buyer_msg("welcome_qr_choice", country_name=country_name, order_id=oid, buyer_username=buyer_name)
        _send_buyer_fp_msg(cardinal, chat_id, msg, buyer_username=buyer_name)
        _log_event("order_welcome_menu_sent", order_id=oid, chat_id=chat_id, mode="choice")
        _notify_tg(
            f"🛒 <b>НОВЫЙ ЗАКАЗ <code>#{oid}</code></b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Покупатель:</b> {html.escape(str(buyer_name))}\n"
            f"🏷 <b>Лот:</b> #{lot_id} · {country_name}\n"
            f"⚙️ <b>Режим:</b> {auto_pay_lbl} (карта: {card_avail_lbl})\n"
            f"⏳ <b>Статус:</b> ожидаем выбор входа (1 — логин, 2 — QR)",
            ntype="order",
        )
        return
    elif qr_mode == "qr_only":
        sess["step"] = "waiting_qr_ready"
        _register_buyer_session(sess, buyer_id=buyer_id)
        _mark_order_processed(oid)
        msg = _render_buyer_msg("qr_ready_prompt", country_name=country_name, order_id=oid, buyer_username=buyer_name)
        if not msg:
            msg = _render_buyer_msg("welcome_qr_only", country_name=country_name, order_id=oid, buyer_username=buyer_name)
        _send_buyer_fp_msg(cardinal, chat_id, msg, buyer_username=buyer_name)
        _log_event("order_qr_ready_prompt_sent", order_id=oid, chat_id=chat_id, mode="qr_only")
        _notify_tg(
            f"🛒 <b>НОВЫЙ ЗАКАЗ <code>#{oid}</code></b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Покупатель:</b> {html.escape(str(buyer_name))}\n"
            f"🏷 <b>Лот:</b> #{lot_id} · {country_name}\n"
            f"⚙️ <b>Режим:</b> {auto_pay_lbl} (карта: {card_avail_lbl})\n"
            f"⏳ <b>Статус:</b> ожидаем готовность к QR («+»)",
            ntype="order",
        )
        return

    sess["step"] = "waiting_login"
    sess["login_wait_time"] = time.time()
    _register_buyer_session(sess, buyer_id=buyer_id)
    _mark_order_processed(oid)
    welcome_text = _render_buyer_msg(
        "welcome",
        country_name=country_name,
        order_id=oid,
        buyer_username=buyer_name,
        login=""
    )
    sent_ok = _send_buyer_fp_msg(cardinal, chat_id, welcome_text, buyer_username=buyer_name)
    if sent_ok:
        _log_event("order_welcome_sent", order_id=oid, chat_id=chat_id, country=country)
    else:
        _log_event("order_welcome_error", level=logging.ERROR, order_id=oid, chat_id=chat_id)
    _notify_tg(
        f"🛒 <b>НОВЫЙ ЗАКАЗ <code>#{oid}</code></b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Покупатель:</b> {html.escape(str(buyer_name))}\n"
        f"🏷 <b>Лот:</b> #{lot_id} · {country_name}\n"
        f"⚙️ <b>Режим:</b> {auto_pay_lbl} (карта: {card_avail_lbl})\n"
        f"⏳ <b>Статус:</b> ожидаем логин Steam (шаг 1/2)",
        ntype="order",
    )
def handle_new_order(cardinal, event, *args):
    order = getattr(event, 'order', None) or event
    oid = str(getattr(order, 'id', '') or getattr(event, 'order_id', '') or getattr(event, 'id', '') or '').strip().lstrip('#')
    key = f"incoming:{oid}" if oid else f"incoming:event:{id(event)}"
    serial_key = _event_chat_key(event, order)
    if not _schedule_event_job(key, _run_serialized_event, serial_key, _process_incoming_order, cardinal, order, event):
        _log_event("order_event_busy", level=logging.DEBUG, order_id=oid or "?")
_recent_processed_msgs: dict[tuple[str, str], float] = {}
_processed_msgs_lock = threading.Lock()

def _handle_new_message_impl(cardinal, event, *args):
    global cardinal_instance
    if cardinal_instance is None:
        cardinal_instance = cardinal
    cfg = _load_config()
    if not cfg.get("plugin_enabled", True):
        return
    chat_obj = getattr(event, 'chat', None)
    message_obj = getattr(event, 'message', None)
    my_id = getattr(getattr(cardinal, 'account', None), 'id', None)
    if chat_obj is not None:
        if getattr(chat_obj, 'last_by_bot', False):
            return
        chat_id = getattr(chat_obj, 'id', None)
        chat_name = getattr(chat_obj, 'name', None)
        author = chat_name
        author_id = None
        msg_text = str(getattr(chat_obj, 'last_message_text', '') or '').strip()
    elif message_obj is not None:
        if getattr(message_obj, 'by_bot', False):
            return
        author_id = getattr(message_obj, 'author_id', None)
        if my_id is not None and author_id is not None and str(author_id) == str(my_id):
            return
        chat_id = getattr(message_obj, 'chat_id', None)
        chat_name = getattr(message_obj, 'chat_name', None) or getattr(event, 'chat_name', None)
        author = getattr(message_obj, 'author', None) or getattr(message_obj, 'author_name', None)
        msg_text = str(getattr(message_obj, 'text', '') or getattr(message_obj, 'content', '') or '').strip()
    else:
        if getattr(event, 'by_bot', False) or getattr(event, 'last_by_bot', False):
            return
        author_id = getattr(event, 'author_id', None)
        if my_id is not None and author_id is not None and str(author_id) == str(my_id):
            return
        chat_id = getattr(event, 'chat_id', None) or getattr(event, 'id', None)
        chat_name = getattr(event, 'chat_name', None) or getattr(event, 'name', None)
        author = getattr(event, 'author', None) or getattr(event, 'author_name', None)
        msg_text = str(getattr(event, 'text', '') or getattr(event, 'last_message_text', '') or getattr(event, 'content', '') or '').strip()
    if not msg_text:
        return
    msg_id = (
        getattr(message_obj, 'id', None)
        or getattr(chat_obj, 'node_msg_id', None)
        or getattr(chat_obj, 'user_msg_id', None)
    )
    dedup_key = (str(chat_id or '?'), str(msg_id) if msg_id else msg_text)
    now_ts = time.time()
    with _processed_msgs_lock:
        old_k = [k for k, t in _recent_processed_msgs.items() if (now_ts - t) > 60]
        for k in old_k:
            _recent_processed_msgs.pop(k, None)
        if dedup_key in _recent_processed_msgs and (now_ts - _recent_processed_msgs[dedup_key]) < 10.0:
            return
        _recent_processed_msgs[dedup_key] = now_ts
    author_str = str(author or '').strip().lower()
    chat_name_str = str(chat_name or '').strip().lower()
    msg_low = msg_text.lower()

    m_ref = _REFUND_RE.search(msg_text)
    if m_ref:
        ref_oid = m_ref.group(1).strip()
        logger.info(f"[{NAME}] Обнаружено сообщение о возврате средств по заказу #{ref_oid}")
        _terminate_buyer_session(ref_oid, reason="chat_refund_notice", chat_id=chat_id)
        return

    m_conf = _CONFIRM_RE.search(msg_text)
    if m_conf:
        conf_oid = next((g for g in m_conf.groups() if g), "").strip()
        if conf_oid:
            logger.info(f"[{NAME}] Обнаружено сообщение о подтверждении/закрытии заказа #{conf_oid}")
            _terminate_buyer_session(conf_oid, reason="chat_confirm_notice", chat_id=chat_id)
            return

    is_system_msg = (
        author_str in ('funpay', 'system', 'фанпей')
        or chat_name_str in ('funpay', 'system')
        or msg_text.startswith('FunPay:')
        or 'оплатил заказ #' in msg_low
        or 'оплатила заказ #' in msg_low
        or 'подтвердить выполнение заказа' in msg_low
        or 'вернул деньги' in msg_low
        or 'возврат средств' in msg_low
        or 'служба поддержки funpay' in msg_low
    )
    m = ORDER_PAID_RE.search(msg_text)
    if m:
        if any(w in msg_low for w in _TOPUP_MARKERS) and not any(w in msg_low for w in _RC_EXPLICIT_MARKERS):
            _log_event("order_msg_ignored_topup", text=msg_text[:120])
            return
        oid = m.group(1).strip()
        with _processed_orders_lock:
            already = oid in _processed_orders
        if not already:
            _log_event("order_msg_fallback", order_id=oid, text=msg_text[:120])
            def _fallback_order_job():
                full_ord = None
                if getattr(cardinal, 'account', None):
                    try:
                        full_ord = cardinal.account.get_order(oid)
                    except Exception as e:
                        logger.warning(f"[{NAME}] Fallback get_order({oid}): {e}")
                order_obj = full_ord or SimpleNamespace(id=oid, chat_id=chat_id, description=msg_text, buyer_username=chat_name or author)
                _process_incoming_order(cardinal, order_obj, event=event, order_text=msg_text)
            _schedule_event_job(f"incoming:{oid}", _run_serialized_event, _event_chat_key(event), _fallback_order_job)
        return
    if is_system_msg:
        sess_chk = _find_buyer_session(chat_id=chat_id, chat_name=chat_name, author=author, author_id=author_id)
        if sess_chk:
            chk_oid = str(sess_chk.get("order_id", "")).strip().lstrip('#')
            if any(w in msg_low for w in ('вернул', 'возврат', 'подтвердил', 'закрыт', 'отменен', 'отменён')):
                _terminate_buyer_session(chk_oid, reason="chat_system_msg_closure", chat_id=chat_id)
        return
    sess = _find_buyer_session(
        chat_id=chat_id,
        chat_name=chat_name,
        author=author,
        author_id=author_id
    )
    if not sess:
        return
    oid = str(sess.get("order_id", "")).strip().lstrip('#')
    with _terminated_orders_lock:
        if oid in _terminated_orders:
            _terminate_buyer_session(oid, reason="terminated_order_filter", chat_id=chat_id)
            return

    if chat_id is not None:
        sess["chat_id"] = chat_id
    if author_id is not None and not sess.get("buyer_id"):
        sess["buyer_id"] = author_id
    country_name = _country_display(sess.get("country", "KZ"))
    step = sess.get("step")
    raw_txt = msg_text.strip()
    raw_low = raw_txt.lower()

    if raw_low in ("заново", "-", "отмена", "start", "/start", "назад", "reset", "сначала"):
        sess["canceled"] = True
        _cancel_qr_worker(oid, reason="user_session_reset")
        cancel_evt = sess.get("cancel_qr_event")
        if cancel_evt and isinstance(cancel_evt, threading.Event):
            cancel_evt.set()
        guard_evt = sess.get("guard_event")
        if guard_evt and isinstance(guard_evt, threading.Event):
            guard_evt.set()
        sess.pop("canceled", None)
        sess["cancel_qr_event"] = _get_or_create_qr_cancel_event(oid)
        sess.pop("login", None)
        sess.pop("password", None)
        sess.pop("guard_code", None)
        login_mode = sess.get("login_mode", "choice")
        if login_mode == "choice":
            sess["step"] = "waiting_choice"
            _register_buyer_session(sess)
            msg = _render_buyer_msg("welcome_menu", country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username"))
            if not msg:
                msg = _render_buyer_msg("welcome_qr_choice", country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username"))
            _send_buyer_fp_msg(cardinal, chat_id, f"🔄 Сессия сброшена.\n\n{msg}", buyer_username=sess.get("buyer_username"))
            _log_event("order_session_reset", order_id=oid, mode="choice")
            return
        elif login_mode == "qr_only":
            sess["step"] = "waiting_qr_ready"
            _register_buyer_session(sess)
            msg = _render_buyer_msg("qr_ready_prompt", country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username"))
            if not msg:
                msg = _render_buyer_msg("welcome_qr_only", country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username"))
            _send_buyer_fp_msg(cardinal, chat_id, f"🔄 Сессия сброшена.\n\n{msg}", buyer_username=sess.get("buyer_username"))
            _log_event("order_session_reset", order_id=oid, mode="qr_only")
            return
        else:
            sess["step"] = "waiting_login"
            sess["login_wait_time"] = time.time()
            sess.pop("reminded_login", None)
            sess.pop("reminded_login_time", None)
            _register_buyer_session(sess)
            msg = _render_buyer_msg("welcome", country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username"))
            _send_buyer_fp_msg(cardinal, chat_id, f"🔄 Сессия сброшена.\n\n{msg}", buyer_username=sess.get("buyer_username"))
            _log_event("order_session_reset", order_id=oid, mode="login")
            return

    if step == "waiting_choice":
        creds = _extract_creds_from_text(msg_text)
        if creds:
            _cancel_qr_worker(oid, reason="creds_from_choice_menu")
            sess["login"], sess["password"] = creds
            sess["step"] = "processing"
            _register_buyer_session(sess)
            ack_msg = _render_buyer_msg("data_received", login=creds[0], country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username", ""))
            _send_buyer_fp_msg(cardinal, chat_id, ack_msg, buyer_username=sess.get("buyer_username"))
            _log_event("order_creds_received_from_menu", order_id=oid, login=creds[0])
            _notify_tg(
                f"🚀 <b>ЗАКАЗ <code>#{oid}</code> В РАБОТЕ · Логин/пароль</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>Покупатель:</b> {html.escape(str(sess.get('buyer_username', '?')))}\n"
                f"🎮 <b>Steam:</b> <code>{html.escape(str(creds[0]))}</code>\n"
                f"🌍 <b>Регион:</b> {country_name}",
                ntype="order",
            )
            _schedule_order_worker(cardinal, sess, "login")
            return

        if raw_low in ("1", "1️⃣", "логин", "login", "пароль", "pass", "данные"):
            _cancel_qr_worker(oid, reason="choice_login_selected")
            sess["step"] = "waiting_login"
            sess["login_wait_time"] = time.time()
            sess.pop("reminded_login", None)
            sess.pop("reminded_login_time", None)
            _register_buyer_session(sess)
            w_msg = _render_buyer_msg("welcome", country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username", ""))
            _send_buyer_fp_msg(cardinal, chat_id, w_msg, buyer_username=sess.get("buyer_username"))
            _log_event("order_choice_login_selected", order_id=oid)
            return

        if raw_low in ("2", "2️⃣", "qr", "кьюар", "кьюар код", "код", "qr код"):
            sess["step"] = "waiting_qr_ready"
            _register_buyer_session(sess)
            q_msg = _render_buyer_msg("qr_ready_prompt", country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username", ""))
            if not q_msg:
                q_msg = _render_buyer_msg("welcome_qr_only", country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username", ""))
            _send_buyer_fp_msg(cardinal, chat_id, q_msg, buyer_username=sess.get("buyer_username"))
            _log_event("order_choice_qr_selected", order_id=oid)
            return

        if raw_low in ("+", "++", "готов", "го", "давай", "сканирую"):
            _cancel_qr_worker(oid, reason="menu_qr_start")
            cancel_evt = _get_or_create_qr_cancel_event(oid)
            sess["cancel_qr_event"] = cancel_evt
            sess["step"] = "waiting_qr"
            _register_buyer_session(sess)
            _send_buyer_fp_msg(cardinal, chat_id, "🔄 Генерируем QR-код для входа...", buyer_username=sess.get("buyer_username"))
            _log_event("order_qr_started_from_menu_plus", order_id=oid)
            _schedule_order_worker(cardinal, sess, "qr")
            return

        if _is_valid_steam_login(raw_txt) and len(raw_txt) >= 3:
            _cancel_qr_worker(oid, reason="login_from_choice_menu")
            sess["login"] = raw_txt
            sess["step"] = "waiting_password"
            sess["login_time"] = time.time()
            _register_buyer_session(sess)
            ask_pwd = _render_buyer_msg("ask_password", login=raw_txt, country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username", ""))
            _send_buyer_fp_msg(cardinal, chat_id, ask_pwd, buyer_username=sess.get("buyer_username"))
            _log_event("order_login_received_from_menu", order_id=oid, login=raw_txt)
            return

        hint = (
            "Пожалуйста, выберите способ авторизации:\n\n"
            "1️⃣ По Логину и Паролю — напишите цифру 1\n"
            "2️⃣ По QR-коду — напишите цифру 2\n\n"
            "ℹ️ Если что-то пошло не так — напишите «заново» или «-», чтобы сбросить выбор."
        )
        _send_buyer_fp_msg(cardinal, chat_id, hint, buyer_username=sess.get("buyer_username"))
        return

    elif step == "waiting_qr_ready":
        creds = _extract_creds_from_text(msg_text)
        if creds:
            _cancel_qr_worker(oid, reason="creds_from_qr_ready")
            sess["login"], sess["password"] = creds
            sess["step"] = "processing"
            _register_buyer_session(sess)
            ack_msg = _render_buyer_msg("data_received", login=creds[0], country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username", ""))
            _send_buyer_fp_msg(cardinal, chat_id, ack_msg, buyer_username=sess.get("buyer_username"))
            _log_event("order_creds_received_from_qr_ready", order_id=oid, login=creds[0])
            _notify_tg(
                f"🚀 <b>ЗАКАЗ <code>#{oid}</code> В РАБОТЕ · Логин/пароль</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>Покупатель:</b> {html.escape(str(sess.get('buyer_username', '?')))}\n"
                f"🎮 <b>Steam:</b> <code>{html.escape(str(creds[0]))}</code>\n"
                f"🌍 <b>Регион:</b> {country_name}",
                ntype="order",
            )
            _schedule_order_worker(cardinal, sess, "login")
            return

        if raw_low in ("+", "++", "готов", "го", "давай", "сканирую", "2", "2️⃣", "qr", "кьюар"):
            _cancel_qr_worker(oid, reason="qr_ready_start")
            cancel_evt = _get_or_create_qr_cancel_event(oid)
            sess["cancel_qr_event"] = cancel_evt
            sess["step"] = "waiting_qr"
            _register_buyer_session(sess)
            _send_buyer_fp_msg(cardinal, chat_id, "🔄 Генерируем QR-код для входа...", buyer_username=sess.get("buyer_username"))
            _log_event("order_qr_started_after_ready", order_id=oid)
            _schedule_order_worker(cardinal, sess, "qr")
            return

        if raw_low in ("1", "1️⃣", "логин", "пароль"):
            _cancel_qr_worker(oid, reason="switched_from_qr_ready_to_login")
            sess["step"] = "waiting_login"
            sess["login_wait_time"] = time.time()
            sess.pop("reminded_login", None)
            sess.pop("reminded_login_time", None)
            _register_buyer_session(sess)
            w_msg = _render_buyer_msg("welcome", country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username", ""))
            _send_buyer_fp_msg(cardinal, chat_id, w_msg, buyer_username=sess.get("buyer_username"))
            _log_event("order_switched_from_qr_ready_to_login", order_id=oid)
            return

        if _is_valid_steam_login(raw_txt) and len(raw_txt) >= 3:
            _cancel_qr_worker(oid, reason="login_from_qr_ready")
            sess["login"] = raw_txt
            sess["step"] = "waiting_password"
            sess["login_time"] = time.time()
            _register_buyer_session(sess)
            ask_pwd = _render_buyer_msg("ask_password", login=raw_txt, country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username", ""))
            _send_buyer_fp_msg(cardinal, chat_id, ask_pwd, buyer_username=sess.get("buyer_username"))
            _log_event("order_login_received_from_qr_ready", order_id=oid, login=raw_txt)
            return

        hint = (
            "📷 Ожидаем вашей готовности к сканированию QR-кода.\n\n"
            "Как только откроете мобильное приложение Steam (Steam Guard → Сканер) — отправьте «+» (плюс) в этот чат для получения QR-кода.\n"
            "(Или отправьте «1», чтобы войти по логину и паролю)"
        )
        _send_buyer_fp_msg(cardinal, chat_id, hint, buyer_username=sess.get("buyer_username"))
        return

    elif step in ("waiting_qr", "waiting_qr_timeout", "waiting_qr_or_creds"):
        if raw_low in ("+", "++", "2", "2️⃣", "qr", "кьюар", "код", "обновить qr", "новый qr", "еще qr", "ещё qr", "готов", "давай"):
            _cancel_qr_worker(oid, reason="new_qr_requested")
            cancel_evt = _get_or_create_qr_cancel_event(oid)
            sess["cancel_qr_event"] = cancel_evt
            sess["step"] = "waiting_qr"
            _register_buyer_session(sess)
            _send_buyer_fp_msg(cardinal, chat_id, "🔄 Генерируем новый QR-код для входа...", buyer_username=sess.get("buyer_username"))
            _log_event("order_qr_requested_new", order_id=oid)
            _schedule_order_worker(cardinal, sess, "qr")
            return

        creds = _extract_creds_from_text(msg_text)
        if creds:
            _cancel_qr_worker(oid, reason="creds_from_qr")
            sess["login"], sess["password"] = creds
            sess["step"] = "processing"
            _register_buyer_session(sess)
            ack_msg = _render_buyer_msg(
                "data_received",
                login=creds[0],
                country_name=country_name,
                order_id=oid,
                buyer_username=sess.get("buyer_username", "")
            )
            _send_buyer_fp_msg(cardinal, chat_id, ack_msg, buyer_username=sess.get("buyer_username"))
            _log_event("order_creds_received_from_qr", order_id=oid, login=creds[0])
            _notify_tg(
                f"🚀 <b>ЗАКАЗ <code>#{oid}</code> В РАБОТЕ · Логин/пароль</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>Покупатель:</b> {html.escape(str(sess.get('buyer_username', '?')))}\n"
                f"🎮 <b>Steam:</b> <code>{html.escape(str(creds[0]))}</code>\n"
                f"🌍 <b>Регион:</b> {country_name}",
                ntype="order",
            )
            _schedule_order_worker(cardinal, sess, "login")
            return

        if raw_low in ("1", "1️⃣", "логин", "пароль"):
            _cancel_qr_worker(oid, reason="switched_from_qr_to_login")
            sess["step"] = "waiting_login"
            sess["login_wait_time"] = time.time()
            sess.pop("reminded_login", None)
            sess.pop("reminded_login_time", None)
            _register_buyer_session(sess)
            w_msg = _render_buyer_msg("welcome", country_name=country_name, order_id=oid, buyer_username=sess.get("buyer_username", ""))
            _send_buyer_fp_msg(cardinal, chat_id, w_msg, buyer_username=sess.get("buyer_username"))
            _log_event("order_switched_from_qr_to_login", order_id=oid)
            return

        login_val = raw_txt
        if _is_valid_steam_login(login_val) and len(login_val) >= 3 and raw_low not in ("qr", "help"):
            _cancel_qr_worker(oid, reason="login_from_qr")
            sess["login"] = login_val
            sess["step"] = "waiting_password"
            sess["login_time"] = time.time()
            _register_buyer_session(sess)
            ask_pwd = _render_buyer_msg(
                "ask_password",
                login=login_val,
                country_name=country_name,
                order_id=oid,
                buyer_username=sess.get("buyer_username", "")
            )
            _send_buyer_fp_msg(cardinal, chat_id, ask_pwd, buyer_username=sess.get("buyer_username"))
            _log_event("order_login_received_from_qr", order_id=oid, login=login_val)
            return

        if step == "waiting_qr":
            hint_msg = (
                "📱 Бот ожидает подтверждения входа через QR-код в мобильном приложении Steam.\n\n"
                "• Чтобы получить новый QR-код, отправьте: «+» или «qr»\n"
                "• Чтобы войти по логину и паролю, отправьте «1» (или данные «логин:пароль»)\n"
                "• Напишите «заново» или «-», чтобы начать сначала"
            )
        else:
            hint_msg = (
                "⏳ Время действия QR-кода истекло.\n\n"
                "• Чтобы получить новый QR-код, отправьте «+» или «2»\n"
                "• Чтобы войти по логину и паролю, отправьте «1» (или данные «логин:пароль»)\n"
                "• Напишите «заново» или «-», чтобы начать сначала"
            )
        _send_buyer_fp_msg(cardinal, chat_id, hint_msg, buyer_username=sess.get("buyer_username"))
        return

    elif step in ("waiting_login", "waiting_credentials"):
        creds = _extract_creds_from_text(msg_text)
        if creds:
            sess["login"], sess["password"] = creds
            sess["step"] = "processing"
            _register_buyer_session(sess)
            ack_msg = _render_buyer_msg(
                "data_received",
                login=creds[0],
                country_name=country_name,
                order_id=oid,
                buyer_username=sess.get("buyer_username", "")
            )
            _send_buyer_fp_msg(cardinal, chat_id, ack_msg, buyer_username=sess.get("buyer_username"))
            _log_event("order_creds_received", order_id=oid, login=creds[0])
            _notify_tg(
                f"🚀 <b>ЗАКАЗ <code>#{oid}</code> В РАБОТЕ · Логин/пароль</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>Покупатель:</b> {html.escape(str(sess.get('buyer_username', '?')))}\n"
                f"🎮 <b>Steam:</b> <code>{html.escape(str(creds[0]))}</code>\n"
                f"🌍 <b>Регион:</b> {country_name}",
                ntype="order",
            )
            _schedule_order_worker(cardinal, sess, "login")
        else:
            login_val = msg_text.strip()
            if not _is_valid_steam_login(login_val):
                _log_event("order_invalid_login_format", level=logging.INFO, order_id=oid, text=login_val[:30])
                warn_msg = (
                    "⚠️ <b>Некорректный логин Steam!</b>\n\n"
                    "Логин должен содержать только латинские буквы, цифры и символы без пробелов (например: <code>my_login</code>).\n"
                    "Пожалуйста, отправьте правильный логин (или сразу <code>логин:пароль</code>):"
                )
                _send_buyer_fp_msg(cardinal, chat_id, warn_msg, buyer_username=sess.get("buyer_username"))
                return
            sess["login"] = login_val
            sess["step"] = "waiting_password"
            sess["login_time"] = time.time()
            _register_buyer_session(sess)
            ask_pwd = _render_buyer_msg(
                "ask_password",
                login=login_val,
                country_name=country_name,
                order_id=oid,
                buyer_username=sess.get("buyer_username", "")
            )
            _send_buyer_fp_msg(cardinal, chat_id, ask_pwd, buyer_username=sess.get("buyer_username"))
            _log_event("order_login_received", order_id=oid, login=login_val)
    elif step == "waiting_password":
        pwd_val = msg_text.strip()
        curr_login = sess.get("login", "")
        if curr_login and pwd_val.strip().lower() == curr_login.strip().lower():
            _log_event("order_ignoring_repeated_login", level=logging.DEBUG, order_id=oid, login=curr_login)
            return
        if curr_login and pwd_val.startswith(f"{curr_login}:"):
            sess["password"] = pwd_val[len(curr_login) + 1:].strip()
        elif ":" in pwd_val:
            parts = pwd_val.split(":", 1)
            p0, p1 = parts[0].strip(), parts[1].strip()
            if _is_valid_steam_login(p0) and p1:
                sess["login"], sess["password"] = p0, p1
            else:
                sess["password"] = pwd_val
        elif "\n" in pwd_val:
            lines = [l.strip() for l in pwd_val.splitlines() if l.strip()]
            if len(lines) >= 2 and _is_valid_steam_login(lines[0]) and lines[1]:
                sess["login"], sess["password"] = lines[0], lines[1]
            else:
                sess["password"] = pwd_val
        else:
            sess["password"] = pwd_val
        if not sess.get("password"):
            return
        login_val = sess.get("login", "")
        sess["step"] = "processing"
        _register_buyer_session(sess)
        ack_msg = _render_buyer_msg(
            "data_received",
            login=login_val,
            country_name=country_name,
            order_id=oid,
            buyer_username=sess.get("buyer_username", "")
        )
        _send_buyer_fp_msg(cardinal, chat_id, ack_msg, buyer_username=sess.get("buyer_username"))
        _log_event("order_password_received", order_id=oid, login=login_val)
        _notify_tg(
            f"🚀 <b>ЗАКАЗ <code>#{oid}</code> В РАБОТЕ · Логин/пароль</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Покупатель:</b> {html.escape(str(sess.get('buyer_username', '?')))}\n"
            f"🎮 <b>Steam:</b> <code>{html.escape(str(login_val))}</code>\n"
            f"🌍 <b>Регион:</b> {country_name}",
            ntype="order",
        )
        _schedule_order_worker(cardinal, sess, "login")
    elif step == "waiting_push":
        code = _extract_guard_code(msg_text)
        if code:
            sess["guard_code"] = code
            _register_buyer_session(sess)
            _send_buyer_fp_msg(
                cardinal,
                chat_id,
                "✅ Код Steam Guard получен! Проверяем его...",
                buyer_username=sess.get("buyer_username"),
            )
            _log_event("order_guard_push_code_entered", order_id=oid, code="***")
        else:
            push_hint = _render_buyer_msg(
                "guard_push",
                login=sess.get("login", ""),
                order_id=oid,
                buyer_username=sess.get("buyer_username", ""),
                country_name=country_name,
            )
            _send_buyer_fp_msg(cardinal, chat_id, push_hint, buyer_username=sess.get("buyer_username"))
    elif step == "waiting_guard":
        guard_event = sess.get("guard_event")
        code = _extract_guard_code(msg_text)
        if code:
            sess["guard_code"] = code
            _log_event("order_guard_entered", order_id=oid, code="***")
            ack_guard = _render_buyer_msg(
                "guard_received",
                order_id=oid,
                country_name=country_name,
                login=sess.get("login", ""),
                buyer_username=sess.get("buyer_username", "")
            )
            _send_buyer_fp_msg(cardinal, chat_id, ack_guard, buyer_username=sess.get("buyer_username"))
            if guard_event and isinstance(guard_event, threading.Event):
                guard_event.set()
        else:
            hint_msg = (
                f"⚠️ Пожалуйста, введите 5-значный проверочный код Steam Guard (например: 2XY8Z).\n\n"
                f"Вы отправили «{msg_text.strip()[:25]}», в котором не найден 5-значный проверочный код. Бот продолжает ожидать корректный код Steam Guard."
            )
            _send_buyer_fp_msg(cardinal, chat_id, hint_msg, buyer_username=sess.get("buyer_username"))
            _log_event("order_guard_non_code_msg", level=logging.INFO, order_id=oid, text=msg_text.strip()[:25])
    elif step == "paused_silent":
        txt = msg_text.strip()
        creds = _extract_creds_from_text(txt)
        code = _extract_guard_code(txt)
        if creds:
            sess["login"], sess["password"] = creds
            sess["step"] = "processing"
            _register_buyer_session(sess)
            _send_buyer_fp_msg(cardinal, chat_id, f"✅ Данные приняты! Возобновляем выполнение заказа #{oid}...", buyer_username=sess.get("buyer_username"))
            _schedule_order_worker(cardinal, sess, "login")
        elif code:
            sess["guard_code"] = code
            sess["step"] = "processing"
            _register_buyer_session(sess)
            _send_buyer_fp_msg(cardinal, chat_id, f"✅ Код Steam Guard принят! Возобновляем выполнение заказа #{oid}...", buyer_username=sess.get("buyer_username"))
            _schedule_order_worker(cardinal, sess, "login")
        elif not sess.get("login") and _is_valid_steam_login(txt):
            sess["login"] = txt
            sess["step"] = "waiting_password"
            sess["login_time"] = time.time()
            _register_buyer_session(sess)
            ask_pwd = _render_buyer_msg(
                "ask_password",
                login=txt,
                country_name=country_name,
                order_id=oid,
                buyer_username=sess.get("buyer_username", "")
            )
            _send_buyer_fp_msg(cardinal, chat_id, ask_pwd, buyer_username=sess.get("buyer_username"))
        elif sess.get("login") and not sess.get("password") and txt:
            sess["password"] = txt
            sess["step"] = "processing"
            _register_buyer_session(sess)
            _send_buyer_fp_msg(cardinal, chat_id, f"✅ Пароль принят! Возобновляем выполнение заказа #{oid}...", buyer_username=sess.get("buyer_username"))
            _schedule_order_worker(cardinal, sess, "login")
        elif sess.get("login") and sess.get("password"):
            hint_msg = (
                f"⚠️ Заказ #{oid} ожидает ввода проверочного кода Steam Guard.\n\n"
                f"Пожалуйста, отправьте 5-значный проверочный код (например: 2XY8Z) или новые данные в формате «логин:пароль»."
            )
            _send_buyer_fp_msg(cardinal, chat_id, hint_msg, buyer_username=sess.get("buyer_username"))
        else:
            hint_msg = (
                f"⚠️ Заказ #{oid} ожидает ввода данных для входа в Steam.\n\n"
                f"Пожалуйста, отправьте логин и пароль в формате «логин:пароль» (или отдельно логин)."
            )
            _send_buyer_fp_msg(cardinal, chat_id, hint_msg, buyer_username=sess.get("buyer_username"))

def handle_new_message(cardinal, event, *args):
    serial_key = _event_chat_key(event)
    event_id = _message_event_identity(event)
    job_key = f"message:{serial_key}:{event_id}"
    if not _schedule_event_job(job_key, _run_serialized_event, serial_key, _handle_new_message_impl, cardinal, event, *args):
        _log_event("message_event_busy", level=logging.DEBUG, chat=serial_key, event_id=event_id)

def classify_failure_reason(res: Optional[AccountRegionResult], proxy_pool: Optional[ProxyPool] = None, err_text: str = "") -> str:
    if res and getattr(res, "failure_reason", None):
        return res.failure_reason
    err_low = (str(res.error or "") if res else str(err_text)).lower()
    if res and res.status == RegionResult.FAIL_NO_CARDS:
        return FailureReason.NO_CARDS
    if res and res.status == RegionResult.FAIL_CARD_DECLINED:
        return FailureReason.CARD_DECLINED
    if res and res.status == RegionResult.FAIL_REGION_COOLDOWN:
        return FailureReason.REGION_COOLDOWN
    if res and res.status == RegionResult.FAIL_CHECKOUT:
        return FailureReason.CHECKOUT_FAILED
    if res and res.status == RegionResult.FAIL_NO_CHEAP_GAME:
        return FailureReason.NO_CHEAP_GAME
    if res and res.status == RegionResult.FAIL_CART_ADD:
        return FailureReason.CART_ADD_FAILED
    if res and res.status == RegionResult.FAIL_VERIFY_REGION:
        return FailureReason.VERIFY_FAILED
    if res and res.status == RegionResult.FAIL_WRONG_PASS:
        return FailureReason.WRONG_CREDENTIALS
    if res and res.status == RegionResult.FAIL_WRONG_GUARD:
        return FailureReason.WRONG_GUARD
    if any(kw in err_low for kw in ("3 months", "90 days", "3 месяца", "90 дней", "recently changed")):
        return FailureReason.REGION_COOLDOWN
    if any(kw in err_low for kw in ("declined", "insufficient", "отклонен", "недостаточно средств")):
        return FailureReason.CARD_DECLINED
    if (res and res.status == RegionResult.FAIL_RATE_LIMIT) or "ratelimit" in err_low or "429" in err_low or "code': 84" in err_low:
        return FailureReason.STEAM_RATE_LIMIT
    if (res and res.status == RegionResult.FAIL_TEMP_ERROR) or any(kw in err_low for kw in ("500", "502", "503", "504", "temporary unavailable", "server disconnected")):
        return FailureReason.STEAM_TEMP_ERROR
    if (proxy_pool is not None and len(proxy_pool) == 0) or "закончились рабочие прокси" in err_low or "нет рабочего прокси" in err_low or "отсутствуют рабочие прокси" in err_low:
        return FailureReason.PROXY_EXHAUSTED
    if "timeout" in err_low and ("qr" in err_low or (res and getattr(res, "login", "") == "(qr)")):
        return FailureReason.QR_TIMEOUT
    return FailureReason.UNKNOWN

def finish_failed_order(
    cardinal,
    sess: dict[str, Any],
    effective_reason: str,
    error_text: str = "",
    mode: str = "login",
    proxy_pool: Optional[ProxyPool] = None,
    refund_enabled: Optional[bool] = None,
    res: Optional[AccountRegionResult] = None,
) -> None:
    oid = str(sess.get("order_id", "")).strip().lstrip('#')
    login = str((res.login if res and res.login else None) or sess.get("login") or "").strip()
    chat_id = sess.get("chat_id")
    buyer_user = sess.get("buyer_username", "")
    country = sess.get("country", "KZ")
    country_name = _country_display(country)

    _log_event("order_failure_classified", order_id=oid, reason=effective_reason, mode=mode)

    if refund_enabled is None:
        refund_enabled = should_refund(effective_reason, order_id=oid)

    refund_ok = False
    if refund_enabled:
        refund_ok = _try_refund(cardinal, oid)
        _log_event("refund_result", order_id=oid, reason=effective_reason, success=refund_ok)
        ref_status_str = "✅ выполнен" if refund_ok else "❌ сбой выполнения"
    else:
        ref_status_str = "➖ отключён в настройках"

    st = _load_stats()
    st["failed"] = st.get("failed", 0) + 1
    _save_stats(st)
    sess["step"] = "failed"

    fail_reason = error_text.strip() or f"Ошибка смены региона Steam на {country_name}"
    if effective_reason == FailureReason.PROXY_EXHAUSTED:
        fail_msg = _render_buyer_msg("no_proxies_refund" if refund_ok else "no_proxies_no_refund", order_id=oid, country_name=country_name)
    elif effective_reason == FailureReason.REGION_COOLDOWN:
        fail_msg = _render_buyer_msg("region_cooldown_refund" if refund_ok else "region_cooldown_no_refund", order_id=oid, country_name=country_name, login=login or "Steam")
    elif effective_reason == FailureReason.NO_CARDS:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason=f"Временно нет доступной карты для региона {country_name}", login=login, buyer_username=buyer_user)
    elif effective_reason == FailureReason.CARD_DECLINED:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason="Платёж картой был отклонён банком или Steam", login=login, buyer_username=buyer_user)
    elif effective_reason == FailureReason.CHECKOUT_FAILED:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason="Не удалось завершить оплату в Steam", login=login, buyer_username=buyer_user)
    elif effective_reason == FailureReason.NO_CHEAP_GAME:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason=f"Не удалось найти дешёвую игру для региона {country_name}", login=login, buyer_username=buyer_user)
    elif effective_reason == FailureReason.CART_ADD_FAILED:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason="Не удалось добавить игру в корзину Steam", login=login, buyer_username=buyer_user)
    elif effective_reason == FailureReason.STEAM_TEMP_ERROR:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason="Временная ошибка серверов Steam", login=login, buyer_username=buyer_user)
    elif effective_reason == FailureReason.STEAM_RATE_LIMIT:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason="Временное ограничение запросов Steam (Rate Limit)", login=login, buyer_username=buyer_user)
    elif effective_reason == FailureReason.VERIFY_FAILED:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason="Смена региона не подтверждена страницами Steam", login=login, buyer_username=buyer_user)
    elif effective_reason == FailureReason.USER_TIMEOUT:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason="Время ожидания ответа покупателя истекло", login=login, buyer_username=buyer_user)
    elif effective_reason == FailureReason.WRONG_CREDENTIALS:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason="Неверный логин или пароль Steam", login=login, buyer_username=buyer_user)
    elif effective_reason == FailureReason.WRONG_GUARD:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason="Неверный или просроченный код Steam Guard", login=login, buyer_username=buyer_user)
    else:
        fail_msg = _render_buyer_msg("error_refund" if refund_ok else "error_no_refund", order_id=oid, reason=fail_reason, login=login, buyer_username=buyer_user)

    _send_buyer_fp_msg(cardinal, chat_id, fail_msg, buyer_username=buyer_user)
    _log_event("order_result", level=logging.WARNING, order_id=oid, login=login, refund_ok=refund_ok, reason=effective_reason, detail=fail_reason, mode=mode)

    mode_badge = " · QR" if mode == "qr" else ""
    reason_title = REFUND_REASON_LABELS.get(effective_reason, "❓ Ошибка выполнения")
    tg_lines = [
        f"⚠️ <b>СБОЙ ЗАКАЗА <code>#{oid}</code>{mode_badge}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📋 <b>Причина:</b> {reason_title}",
        f"👤 <b>Покупатель:</b> {html.escape(str(buyer_user or '?'))}",
        f"🎮 <b>Steam:</b> <code>{html.escape(login or 'не указан')}</code>",
        f"🌍 <b>Регион:</b> {country_name} (<code>{country}</code>)",
    ]
    if effective_reason == FailureReason.PROXY_EXHAUSTED:
        tested_cnt = proxy_pool.total_configured if proxy_pool is not None else len(sess.get("proxies") or [])
        tg_lines.append(f"🌐 <b>Прокси:</b> проверено {tested_cnt}, доступных 0")
    if res and res.cart_game:
        g_title = html.escape(str(res.cart_game.get("title") or "Игра"))
        g_price = html.escape(str(res.cart_subtotal or res.cart_game.get("price_formatted") or ""))
        tg_lines.append(f"🛒 <b>Товар:</b> {g_title}" + (f" ({g_price})" if g_price else ""))
    if res and res.matched_card:
        c = res.matched_card
        tg_lines.append(
            f"💳 <b>Карта:</b> <code>{_mask_card_number(c.get('number', ''))}</code> "
            f"(ID: <code>{c.get('id', '')}</code>)"
        )
    if res and res.transid:
        tg_lines.append(f"🧾 <b>TransID:</b> <code>{html.escape(str(res.transid))}</code>")
    tg_lines.append(f"💬 <b>Детали:</b> {html.escape(fail_reason[:300])}")
    tg_lines.append(f"💸 <b>Автовозврат:</b> {ref_status_str}")

    _notify_tg("\n".join(tg_lines), ntype="error")

    _cancel_qr_worker(oid, reason=f"finished_failed_{effective_reason}")
    _terminate_buyer_session(oid, reason=f"finish_failed_order_{effective_reason}", chat_id=chat_id)

def _extract_card_brand_and_last4(card: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(card, dict):
        return "Карта", "****"
    digits = re.sub(r"\D", "", str(card.get("number") or ""))
    last4 = digits[-4:] if len(digits) >= 4 else "****"
    pm = str(card.get("payment_method") or "").strip().lower()
    if not pm and digits:
        pm = _detect_card_payment_method(digits)
    brand_map = {
        "mastercard": "Mastercard",
        "visa": "Visa",
        "amex": "Amex",
        "jcb": "JCB",
        "discover": "Discover",
        "mir": "MIR",
    }
    brand = brand_map.get(pm, pm.capitalize() if pm else "Карта")
    return brand, last4

def _handle_order_success_or_cart(cardinal, sess: dict[str, Any], res: AccountRegionResult, mode: str = "login") -> None:
    oid = str(sess.get("order_id", "")).strip().lstrip("#")
    login = str(res.login or sess.get("login") or "").strip()
    chat_id = sess.get("chat_id")
    buyer_user = sess.get("buyer_username", "")
    country = sess.get("country", "KZ")
    country_name = _country_display(country)
    pinfo = get_region_price_info(country)
    cfg = _load_config()
    mode_badge = " · QR" if mode == "qr" else ""

    st = _load_stats()
    st["total_operations"] = st.get("total_operations", 0) + 1
    st["last_operation"] = f"{time.strftime('%d.%m.%Y %H:%M:%S')} (Заказ #{oid})"

    src_labels = {
        "lot": "карта лота",
        "global_country": "общая база (регион)",
        "global_any": "общая база (ANY)",
    }

    if res.status == RegionResult.CART_READY:
        st["carts_prepared"] = st.get("carts_prepared", 0) + 1
        _save_stats(st)
        sess["step"] = "finished"
        game = res.cart_game or {}
        g_title = str(game.get("title") or "Дешёвая игра")
        g_price = str(res.cart_subtotal or game.get("price_formatted") or pinfo["label"])
        g_appid = game.get("appid") or "—"
        g_subid = game.get("packageid") or game.get("bundleid") or "—"
        g_url = str(game.get("url") or URL_STORE)
        verified_str = "✅ подтверждена" if res.cart_verified else "⚠️ проверьте вручную"

        card = res.matched_card
        if card:
            src_txt = src_labels.get(res.card_source or "", "база карт")
            card_exp = f"{card.get('exp_month', '??')}/{card.get('exp_year', '????')}"
            card_line = f"<code>{_mask_card_number(card.get('number', ''))}</code> ({card_exp} · {src_txt})"
        else:
            card_line = f"⚠️ не найдена для <code>{country}</code>"

        if cfg.get("notify_buyer_on_cart_ready", True):
            cart_buyer_msg = _render_buyer_msg(
                "cart_ready",
                country_name=country_name,
                order_id=oid,
                login=login,
                buyer_username=buyer_user,
                game_title=g_title,
                game_price=g_price,
                game_url=g_url,
            )
            _send_buyer_fp_msg(cardinal, chat_id, cart_buyer_msg, buyer_username=buyer_user)

        _log_event(
            "order_cart_ready",
            order_id=oid,
            login=login,
            country=country,
            appid=g_appid,
            subid=g_subid,
            price=g_price,
            verified=res.cart_verified,
            method=res.cart_method,
            card_found=bool(card),
            mode=mode,
        )
        _notify_tg(
            f"🛒 <b>КОРЗИНА ГОТОВА · Заказ <code>#{oid}</code>{mode_badge}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Покупатель:</b> {html.escape(str(buyer_user or '?'))}\n"
            f"🎮 <b>Steam:</b> <code>{html.escape(login)}</code>\n"
            f"🌍 <b>Регион:</b> {country_name} (<code>{country}</code> · {pinfo['currency']})\n"
            f"🕹 <b>Игра:</b> <a href=\"{g_url}\">{html.escape(g_title)}</a> — <b>{html.escape(g_price)}</b>\n"
            f"🔎 <b>ID:</b> <code>AppID {g_appid}</code> · <code>SubID {g_subid}</code> ({verified_str})\n"
            f"💳 <b>Карта:</b> {card_line}\n"
            f"🔗 <a href=\"{URL_CART}\">Открыть корзину Steam</a>\n"
            f"<i>Автооплата картой отключена — завершите покупку вручную.</i>",
            ntype="success",
        )
        _cancel_qr_worker(oid, reason=f"order_cart_ready_{mode}")
        _terminate_buyer_session(oid, reason=f"order_cart_ready_{mode}", chat_id=chat_id)
        return

    if res.status == RegionResult.ALREADY:
        st["success"] = st.get("success", 0) + 1
        _save_stats(st)
        sess["step"] = "finished"
        already_msg = _render_buyer_msg(
            "already_region",
            country_name=country_name,
            order_id=oid,
            login=login,
            buyer_username=buyer_user,
        )
        _send_buyer_fp_msg(cardinal, chat_id, already_msg, buyer_username=buyer_user)
        _log_event("order_already_region", order_id=oid, login=login, country=country, mode=mode)
        _notify_tg(
            f"ℹ️ <b>РЕГИОН УЖЕ УСТАНОВЛЕН · Заказ <code>#{oid}</code>{mode_badge}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Покупатель:</b> {html.escape(str(buyer_user or '?'))}\n"
            f"🎮 <b>Steam:</b> <code>{html.escape(login)}</code>\n"
            f"🌍 <b>Регион:</b> {country_name} (уже совпадает с целевым)",
            ntype="success",
        )
        _cancel_qr_worker(oid, reason=f"order_already_{mode}")
        _terminate_buyer_session(oid, reason=f"order_already_{mode}", chat_id=chat_id)
        return

    st["success"] = st.get("success", 0) + 1
    _save_stats(st)
    sess["step"] = "finished"
    game_data = res.cart_game or {}
    g_title_buyer = str(game_data.get("title") or "Дешёвая игра")
    g_price_buyer = str(res.cart_subtotal or game_data.get("price_formatted") or pinfo["label"])
    card_brand, card_last4 = _extract_card_brand_and_last4(res.matched_card)
    success_msg = _render_buyer_msg(
        "success",
        country_name=country_name,
        order_id=oid,
        login=login,
        buyer_username=buyer_user,
        game_title=g_title_buyer,
        game_price=g_price_buyer,
        card_brand=card_brand,
        card_last4=card_last4,
        guide_url=POST_SWITCH_GUIDE_URL,
    )
    _send_buyer_fp_msg(cardinal, chat_id, success_msg, buyer_username=buyer_user)
    _log_event(
        "order_success",
        order_id=oid,
        login=login,
        country=country,
        transid=res.transid or "",
        verified=res.region_verified,
        mode=mode,
    )
    ok_lines = [
        f"✅ <b>РЕГИОН ИЗМЕНЁН · Заказ <code>#{oid}</code>{mode_badge}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>Покупатель:</b> {html.escape(str(buyer_user or '?'))}",
        f"🎮 <b>Steam:</b> <code>{html.escape(login)}</code>",
        f"🌍 <b>Новый регион:</b> {country_name} (<code>{country}</code>)",
    ]
    if res.cart_game:
        g_title = html.escape(str(res.cart_game.get("title") or "Дешёвая игра"))
        g_price = html.escape(str(res.cart_subtotal or res.cart_game.get("price_formatted") or ""))
        ok_lines.append(f"🕹 <b>Куплена игра:</b> {g_title}" + (f" ({g_price})" if g_price else ""))
    if res.matched_card:
        c = res.matched_card
        src_txt = src_labels.get(res.card_source or "", "база карт")
        ok_lines.append(f"💳 <b>Карта:</b> <code>{_mask_card_number(c.get('number', ''))}</code> ({src_txt})")
    if res.transid:
        ok_lines.append(f"🧾 <b>TransID:</b> <code>{html.escape(str(res.transid))}</code>")
    ok_lines.append(
        f"🔍 <b>Проверка региона:</b> {'✅ подтверждён в Steam' if res.region_verified else '⚠️ оплата прошла (обновляется)'}"
    )
    _notify_tg("\n".join(ok_lines), ntype="success")
    _cancel_qr_worker(oid, reason=f"order_success_{mode}")
    _terminate_buyer_session(oid, reason=f"order_success_{mode}", chat_id=chat_id)

def _run_qr_order_process(cardinal, sess):
    chat_id = sess["chat_id"]
    country = sess["country"]
    proxies = sess.get("proxies") or _get_proxies_for_country(country, include_any=True)
    lot_id = sess.get("lot_id")
    lot_cfg = sess.get("lot_cfg")
    oid = str(sess.get("order_id", ""))
    buyer_user = sess.get("buyer_username", "")
    country_name = _country_display(country)
    cfg = _load_config()
    cancel_evt = _get_or_create_qr_cancel_event(oid)
    sess["cancel_qr_event"] = cancel_evt
    _log_event("qr_worker_started", order_id=oid, chat_id=chat_id)

    if not proxies:
        if cfg.get("auto_deactivate_lots", True) and lot_id:
            _deactivate_funpay_lot(cardinal, lot_id)
            lots = _get_lots_dict(cfg)
            if str(lot_id) in lots:
                lots[str(lot_id)]["enabled"] = False
                cfg["lots"] = lots
                _save_config(cfg)
        finish_failed_order(
            cardinal,
            sess,
            effective_reason=FailureReason.PROXY_EXHAUSTED,
            error_text="У лота отсутствуют рабочие прокси",
            mode="qr",
            proxy_pool=None,
        )
        return

    _log_event("order_qr_start", order_id=oid, lot_id=lot_id, country=country)

    async def _async_qr_worker():
        proxy_pool = ProxyPool(proxies)

        displayed = False
        async def _display_cb(challenge_url: str):
            nonlocal displayed
            if displayed or cancel_evt.is_set():
                return
            displayed = True
            qr_sent = False
            try:
                import qrcode
                qr_obj = qrcode.QRCode(box_size=8, border=2)
                qr_obj.add_data(challenge_url)
                qr_obj.make(fit=True)
                img = qr_obj.make_image(fill_color="black", back_color="white")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                if getattr(cardinal, 'account', None) and hasattr(cardinal.account, 'send_image'):
                    try:
                        cardinal.account.send_image(int(chat_id), buf)
                        qr_sent = True
                        _log_event("order_qr_image_sent", order_id=oid, chat_id=chat_id)
                    except Exception as e:
                        logger.warning(f"[{NAME}] Ошибка send_image в чат #{chat_id}: {e}")
            except Exception as e:
                logger.warning(f"[{NAME}] Ошибка генерации QR-изображения: {e}")

            msg_qr = _render_buyer_msg(
                "welcome_qr",
                country_name=country_name,
                order_id=oid,
                buyer_username=buyer_user,
                challenge_url=challenge_url
            )
            _send_buyer_fp_msg(cardinal, chat_id, msg_qr, buyer_username=buyer_user)
            _log_event("order_qr_welcome_sent", order_id=oid, chat_id=chat_id, img_sent=qr_sent)

        async def _confirmed_cb(login_name: str):
            if login_name:
                sess["login"] = login_name
            c_msg = _render_buyer_msg(
                "qr_confirmed",
                country_name=country_name,
                order_id=oid,
                buyer_username=buyer_user,
                login=login_name or ""
            )
            if not c_msg:
                c_msg = "✅ Вход подтверждён. Начинаю смену..."
            _send_buyer_fp_msg(cardinal, chat_id, c_msg, buyer_username=buyer_user)
            _log_event("order_qr_confirmed", order_id=oid, login=login_name)

        async def _checkout_start_cb(game: dict[str, Any]):
            if cancel_evt.is_set() or sess.get("canceled"):
                return
            g_dict = game if isinstance(game, dict) else {}
            tx_msg = _render_buyer_msg(
                "checkout_started",
                country_name=country_name,
                order_id=oid,
                buyer_username=buyer_user,
                login=str(sess.get("login") or ""),
                game_title=str(g_dict.get("title") or ""),
                game_price=str(g_dict.get("price_formatted") or ""),
            )
            if tx_msg:
                _send_buyer_fp_msg(cardinal, chat_id, tx_msg, buyer_username=buyer_user)
            _log_event("order_checkout_started", order_id=oid, mode="qr", game_title=str(g_dict.get("title") or ""))

        res = await process_one_account_qr(
            proxy_pool=proxy_pool,
            country_code=country,
            display_cb=_display_cb,
            poll_timeout=180.0,
            confirmed_cb=_confirmed_cb,
            cancel_event=cancel_evt,
            order_id=oid,
            lot_cfg=lot_cfg,
            on_checkout_start_cb=_checkout_start_cb,
        )

        if cancel_evt.is_set() or sess.get("canceled"):
            return

        login = res.login or "(qr)"
        sess["login"] = login

        if res.is_success:
            _handle_order_success_or_cart(cardinal, sess, res, mode="qr")
        else:
            effective_reason = classify_failure_reason(res, proxy_pool=proxy_pool)
            if effective_reason == FailureReason.QR_TIMEOUT:
                refund_enabled = should_refund(FailureReason.QR_TIMEOUT, order_id=oid)
                if not refund_enabled:
                    sess["step"] = "waiting_qr_timeout"
                    sess["timeout_qr_time"] = time.time()
                    _register_buyer_session(sess)
                    t_msg = _render_buyer_msg(
                        "qr_timeout",
                        order_id=oid,
                        country_name=country_name,
                        buyer_username=buyer_user,
                        login=login,
                    )
                    _send_buyer_fp_msg(cardinal, chat_id, t_msg, buyer_username=buyer_user)
                    _log_event("order_qr_timeout", order_id=oid, login=login)
                    _notify_tg(
                        f"⏱ <b>QR ИСТЁК · Заказ <code>#{oid}</code></b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>Покупатель:</b> {html.escape(str(buyer_user or '?'))}\n"
                        f"🌍 <b>Регион:</b> {country_name}\n"
                        f"⏳ <b>Статус:</b> ожидаем «+» / «2» для повтора QR или «1» для входа по логину",
                        ntype="order",
                    )
                    return
                else:
                    finish_failed_order(
                        cardinal,
                        sess,
                        effective_reason=effective_reason,
                        error_text=str(res.error or ""),
                        mode="qr",
                        proxy_pool=proxy_pool,
                        refund_enabled=True,
                    )
                    return

            finish_failed_order(
                cardinal,
                sess,
                effective_reason=effective_reason,
                error_text=str(res.error or ""),
                mode="qr",
                proxy_pool=proxy_pool,
                res=res,
            )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_qr_worker())
    except Exception as e:
        logger.error(f"[{NAME}] Order processing QR error: {e}", exc_info=True)
        finish_failed_order(
            cardinal,
            sess,
            effective_reason=FailureReason.UNKNOWN,
            error_text=f"Непредвиденная ошибка при выполнении заказа (чаще всего карта или прокси): {str(e)[:200]}",
            mode="qr",
            proxy_pool=None,
        )
    finally:
        _log_event("qr_worker_finished", order_id=oid)
        loop.close()

def _run_order_process(cardinal, sess):
    chat_id = sess["chat_id"]
    login = sess["login"]
    password = sess["password"]
    country = sess["country"]
    proxies = sess.get("proxies") or _get_proxies_for_country(country, include_any=True)
    lot_id = sess.get("lot_id")
    lot_cfg = sess.get("lot_cfg")
    oid = str(sess.get("order_id", ""))
    buyer_user = sess.get("buyer_username", "")
    cfg = _load_config()
    country_name = _country_display(country)
    if not proxies:
        if cfg.get("auto_deactivate_lots", True) and lot_id:
            _deactivate_funpay_lot(cardinal, lot_id)
            lots = _get_lots_dict(cfg)
            if str(lot_id) in lots:
                lots[str(lot_id)]["enabled"] = False
                cfg["lots"] = lots
                _save_config(cfg)
        finish_failed_order(
            cardinal,
            sess,
            effective_reason=FailureReason.PROXY_EXHAUSTED,
            error_text="У лота отсутствуют рабочие прокси",
            mode="login",
            proxy_pool=None,
        )
        return
    _log_event("order_start", order_id=oid, lot_id=lot_id, login=login, country=country)
    async def _async_worker():
        proxy_pool = ProxyPool(proxies)
        guard_call_count = 0
        async def _guard_cb(login_str: str, guard_type: str = "guard", guard_detail: str = "", is_retry: bool = False) -> str:
            nonlocal guard_call_count
            if guard_type == "push_code":
                return str(sess.pop("guard_code", "") or "").strip().upper()
            if guard_type == "push_code_invalid":
                sess.pop("guard_code", None)
                sess["step"] = "waiting_push"
                _register_buyer_session(sess)
                retry_msg = _render_buyer_msg(
                    "guard_retry",
                    login=login,
                    order_id=oid,
                    buyer_username=buyer_user,
                    guard_destination="📱 Откройте раздел Steam Guard в мобильном приложении и возьмите новый 5-значный код.",
                    country_name=_country_display(country),
                )
                _send_buyer_fp_msg(cardinal, chat_id, retry_msg, buyer_username=buyer_user)
                _log_event("order_guard_push_code_rejected", order_id=oid, login=login)
                return ""
            guard_call_count += 1
            if guard_type == "push":
                sess.pop("guard_code", None)
                sess["step"] = "waiting_push"
                _register_buyer_session(sess)
                push_msg = _render_buyer_msg(
                    "guard_push",
                    login=login,
                    order_id=oid,
                    buyer_username=buyer_user,
                    country_name=_country_display(country),
                )
                _send_buyer_fp_msg(cardinal, chat_id, push_msg, buyer_username=buyer_user)
                _log_event("order_guard_push_requested", order_id=oid, login=login)
                return ""
            sess["step"] = "waiting_guard"
            sess.pop("guard_code", None)
            sess["guard_event"] = threading.Event()
            _register_buyer_session(sess)
            if guard_type == "email":
                hint_domain = f" (@{guard_detail})" if guard_detail and "@" not in guard_detail else (f" ({guard_detail})" if guard_detail else "")
                dest_str = f"📧 Проверочный код отправлен на вашу почту{hint_domain}."
            elif guard_type == "device":
                dest_str = "📱 Откройте мобильное приложение Steam Guard и посмотрите 5-значный проверочный код."
            else:
                dest_str = "На ваш мобильный Guard или почту отправлен проверочный код."
            msg_key = "guard_retry" if (is_retry or guard_call_count > 1) else "guard_request"
            req_msg = _render_buyer_msg(
                msg_key,
                login=login,
                order_id=oid,
                buyer_username=buyer_user,
                guard_destination=dest_str,
                country_name=_country_display(country),
            )
            _send_buyer_fp_msg(cardinal, chat_id, req_msg, buyer_username=buyer_user)
            _log_event("order_guard_requested", order_id=oid, login=login, attempt=guard_call_count, guard_type=guard_type)
            remind_enabled = cfg.get("buyer_inactivity_reminders", True)
            if remind_enabled:
                got = await asyncio.get_event_loop().run_in_executor(None, lambda: sess["guard_event"].wait(timeout=90))
                if not got:
                    _log_event("order_guard_reminder_sent", order_id=oid, login=login)
                    remind_msg = _render_buyer_msg("reminder_guard", login=login, order_id=oid, buyer_username=buyer_user, country_name=_country_display(country))
                    _send_buyer_fp_msg(cardinal, chat_id, remind_msg, buyer_username=buyer_user)
                    got = await asyncio.get_event_loop().run_in_executor(None, lambda: sess["guard_event"].wait(timeout=180))
                    if not got:
                        _send_buyer_fp_msg(
                            cardinal, chat_id,
                            f"⏳ Мы всё ещё ожидаем код Steam Guard для заказа #{oid}.\n\n"
                            f"Пожалуйста, проверьте мобильное приложение Steam или почту и отправьте проверочный код в этот чат.",
                            buyer_username=buyer_user
                        )
                        got = await asyncio.get_event_loop().run_in_executor(None, lambda: sess["guard_event"].wait(timeout=330))
                if sess.get("canceled"):
                    return ""
                if got:
                    code = sess.get("guard_code", "")
                    sess["step"] = "processing"
                    _log_event("order_guard_received", order_id=oid, login=login)
                    return code
                if sess.get("canceled"):
                    return ""
                sess["step"] = "paused_silent"
                sess["is_silent_guard"] = True
                _log_event("order_guard_timeout_silent", level=logging.WARNING, order_id=oid, login=login)
                return ""
            else:
                got = await asyncio.get_event_loop().run_in_executor(None, lambda: sess["guard_event"].wait(timeout=600))
                if sess.get("canceled"):
                    return ""
                if got:
                    code = sess.get("guard_code", "")
                    sess["step"] = "processing"
                    _log_event("order_guard_received", order_id=oid, login=login)
                    return code
                if sess.get("canceled"):
                    return ""
                sess["step"] = "failed"
                _log_event("order_guard_timeout", level=logging.WARNING, order_id=oid, login=login)
                return ""
        async def _checkout_start_cb(game: dict[str, Any]):
            if sess.get("canceled"):
                return
            g_dict = game if isinstance(game, dict) else {}
            tx_msg = _render_buyer_msg(
                "checkout_started",
                country_name=country_name,
                order_id=oid,
                buyer_username=buyer_user,
                login=login,
                game_title=str(g_dict.get("title") or ""),
                game_price=str(g_dict.get("price_formatted") or ""),
            )
            if tx_msg:
                _send_buyer_fp_msg(cardinal, chat_id, tx_msg, buyer_username=buyer_user)
            _log_event("order_checkout_started", order_id=oid, mode="login", game_title=str(g_dict.get("title") or ""))

        sem = asyncio.Semaphore(1)
        res = await process_one_account(
            login=login,
            password=password,
            shared_secret=None,
            proxy_pool=proxy_pool,
            country_code=country,
            semaphore=sem,
            guard_provider=_guard_cb,
            lot_cfg=lot_cfg,
            order_id=oid,
            on_checkout_start_cb=_checkout_start_cb,
        )
        if sess.get("canceled"):
            logger.info(f"[{NAME}] Заказ #{oid} был сброшен/отменён покупателем, прерываем выполнение.")
            return
        if cfg.get("auto_deactivate_lots", True) and lot_id:
            cfg_curr = _load_config()
            lots_curr = _get_lots_dict(cfg_curr)
            lot_entry = lots_curr.get(str(lot_id)) or {}
            cfg_proxies = list(lot_entry.get("proxies") or []) or _get_proxies_for_country(country, include_any=True)
            should_deactivate = False
            deact_reason = ""
            if len(cfg_proxies) == 0:
                should_deactivate = True
                deact_reason = "У лота отсутствуют привязанные прокси."
            elif len(proxy_pool) == 0:
                alive_found = 0
                for p in cfg_proxies:
                    try:
                        ok, _ = await check_proxy(p, timeout=5.0)
                        if ok:
                            alive_found += 1
                            break
                    except Exception:
                        pass
                if alive_found == 0:
                    should_deactivate = True
                    deact_reason = "Все прокси лота недоступны при проверке."
                else:
                    logger.info(f"[{NAME}] Лот #{lot_id} НЕ деактивирован: найдены доступные прокси в пуле лота.")
            if should_deactivate:
                _log_event("lot_auto_deactivated", level=logging.WARNING, lot_id=lot_id, reason=deact_reason)
                _deactivate_funpay_lot(cardinal, lot_id)
                if str(lot_id) in lots_curr:
                    lots_curr[str(lot_id)]["enabled"] = False
                    cfg_curr["lots"] = lots_curr
                    _save_config(cfg_curr)
                if bot_instance and admin_chat_id:
                    _tg_send(admin_chat_id, f"🛑 <b>Лот #{lot_id} деактивирован на FunPay</b>\n{deact_reason}")
        if res.is_success:
            _handle_order_success_or_cart(cardinal, sess, res, mode="login")
        else:
            if sess.get("is_silent_guard") and cfg.get("buyer_inactivity_reminders", True):
                refund_enabled = should_refund(FailureReason.USER_TIMEOUT, order_id=oid)
                if refund_enabled:
                    finish_failed_order(
                        cardinal,
                        sess,
                        effective_reason=FailureReason.USER_TIMEOUT,
                        error_text="Время ожидания ответа покупателя истекло",
                        mode="login",
                        proxy_pool=proxy_pool,
                        refund_enabled=True,
                    )
                    return
                else:
                    sess["step"] = "paused_silent"
                    _register_buyer_session(sess)
                    pause_msg = (
                        f"⏳ Время ожидания кода Steam Guard истекло.\n\n"
                        f"Заказ #{oid} ожидает вашего ответа. Как только вы отправите проверочный код или данные в этот чат — бот продолжит смену региона."
                    )
                    _send_buyer_fp_msg(cardinal, chat_id, pause_msg, buyer_username=buyer_user)
                    _log_event("order_paused_silent", order_id=oid, login=login)
                    _notify_tg(
                        f"⏳ <b>ОЖИДАНИЕ ПОКУПАТЕЛЯ · Заказ <code>#{oid}</code></b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>Покупатель:</b> {html.escape(str(buyer_user or '?'))}\n"
                        f"🎮 <b>Steam:</b> <code>{html.escape(str(login))}</code>\n"
                        f"📋 <b>Причина:</b> не ввёл код Steam Guard вовремя\n"
                        f"💸 <b>Автовозврат:</b> ➖ не оформлен (режим ожидания)",
                        ntype="order",
                    )
                    return

            if res.status == RegionResult.FAIL_WRONG_PASS:
                refund_enabled = should_refund(FailureReason.WRONG_CREDENTIALS, order_id=oid)
                if refund_enabled:
                    finish_failed_order(
                        cardinal,
                        sess,
                        effective_reason=FailureReason.WRONG_CREDENTIALS,
                        error_text="Неверный логин или пароль Steam",
                        mode="login",
                        proxy_pool=proxy_pool,
                        refund_enabled=True,
                    )
                    return
                else:
                    sess["step"] = "waiting_credentials"
                    sess.pop("password", None)
                    sess["cred_time"] = time.time()
                    _register_buyer_session(sess)
                    wrong_pwd_msg = (
                        f"❌ Неверный логин или пароль Steam для аккаунта «{login}»!\n\n"
                        f"Пожалуйста, проверьте введённые данные и отправьте правильные в этот чат в формате:\n"
                        f"логин:пароль\n"
                        f"(или отдельно логин, а затем пароль)."
                    )
                    _send_buyer_fp_msg(cardinal, chat_id, wrong_pwd_msg, buyer_username=buyer_user)
                    _log_event("order_wrong_password_prompt", order_id=oid, login=login)
                    _notify_tg(
                        f"🔑 <b>НЕВЕРНЫЕ ДАННЫЕ · Заказ <code>#{oid}</code></b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>Покупатель:</b> {html.escape(str(buyer_user or '?'))}\n"
                        f"🎮 <b>Steam:</b> <code>{html.escape(str(login))}</code>\n"
                        f"⏳ <b>Статус:</b> повторно запрошены логин и пароль (без автовозврата)",
                        ntype="order",
                    )
                    return

            if res.status == RegionResult.FAIL_WRONG_GUARD:
                finish_failed_order(
                    cardinal,
                    sess,
                    effective_reason=FailureReason.WRONG_GUARD,
                    error_text="Неверный или просроченный код Steam Guard",
                    mode="login",
                    proxy_pool=proxy_pool,
                )
                return

            effective_reason = classify_failure_reason(res, proxy_pool, str(res.error or ""))

            finish_failed_order(
                cardinal,
                sess,
                effective_reason=effective_reason,
                error_text=str(res.error or ""),
                mode="login",
                proxy_pool=proxy_pool,
                res=res,
            )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_worker())
    except Exception as e:
        logger.error(f"[{NAME}] Order processing error: {e}", exc_info=True)
        finish_failed_order(
            cardinal,
            sess,
            effective_reason=FailureReason.UNKNOWN,
            error_text=f"Непредвиденная ошибка при выполнении заказа (чаще всего карта или прокси): {str(e)[:200]}",
            mode="login",
            proxy_pool=None,
        )
    finally:
        loop.close()
_stop_inactivity_watcher = threading.Event()

def _handle_user_inactivity_timeout(cardinal, sess: dict[str, Any], step: str) -> None:
    oid = str(sess.get("order_id", "")).strip().lstrip('#')
    chat_id = sess.get("chat_id")
    buyer_user = sess.get("buyer_username", "")
    login = sess.get("login", "")

    _log_event("user_inactivity_timeout", order_id=oid, step=step)

    refund_enabled = should_refund(FailureReason.USER_TIMEOUT, order_id=oid)
    if refund_enabled:
        finish_failed_order(
            cardinal,
            sess,
            effective_reason=FailureReason.USER_TIMEOUT,
            error_text="Время ожидания ответа покупателя истекло",
            mode="login",
            refund_enabled=True,
        )
    else:
        sess["step"] = "paused_silent"
        _register_buyer_session(sess)
        pause_msg = (
            f"⏳ Время ожидания ответа истекло.\n\n"
            f"Заказ #{oid} ожидает вашего ответа. Как только вы отправите необходимые данные в этот чат — бот продолжит смену региона."
        )
        _send_buyer_fp_msg(cardinal, chat_id, pause_msg, buyer_username=buyer_user)
        _log_event("order_paused_silent", order_id=oid, login=login, step=step)
        _notify_tg(
            f"⏳ <b>ОЖИДАНИЕ ПОКУПАТЕЛЯ · Заказ <code>#{oid}</code></b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Покупатель:</b> {html.escape(str(buyer_user or '?'))}\n"
            f"🎮 <b>Steam:</b> <code>{html.escape(str(login or 'не указан'))}</code>\n"
            f"📋 <b>Этап:</b> {html.escape(str(step))}\n"
            f"💸 <b>Автовозврат:</b> ➖ не оформлен (режим ожидания)",
            ntype="order",
        )

def _inactivity_watcher_loop(cardinal):
    while not _stop_inactivity_watcher.is_set():
        try:
            if _stop_inactivity_watcher.wait(timeout=30):
                break
            cfg = _load_config()
            if not cfg.get("plugin_enabled", True) or not cfg.get("buyer_inactivity_reminders", True):
                continue
            now = time.time()
            with _sessions_lock:
                sessions_snapshot = list(_active_sessions)
            for sess in sessions_snapshot:
                step = sess.get("step")
                oid = str(sess.get("order_id", "")).strip().lstrip('#')
                with _terminated_orders_lock:
                    if oid in _terminated_orders:
                        continue
                if step in ("finished", "refunded", "failed_closed"):
                    continue
                chat_id = sess.get("chat_id")
                buyer_user = sess.get("buyer_username", "")
                country_name = _country_display(sess.get("country", "KZ"))
                login = sess.get("login", "")
                if step == "waiting_login":
                    login_wait_at = sess.get("login_wait_time", sess.get("created_at", now))
                    if not sess.get("reminded_login"):
                        if (now - login_wait_at) >= 180:
                            sess["reminded_login"] = True
                            sess["reminded_login_time"] = now
                            _register_buyer_session(sess)
                            msg = _render_buyer_msg(
                                "reminder_login",
                                order_id=oid,
                                buyer_username=buyer_user,
                                country_name=country_name,
                                login=login
                            )
                            _send_buyer_fp_msg(cardinal, chat_id, msg, buyer_username=buyer_user)
                            _log_event("inactivity_reminder_sent", order_id=oid, step=step)
                    else:
                        rem_time = sess.get("reminded_login_time", login_wait_at + 180)
                        if (now - rem_time) >= 180:
                            _handle_user_inactivity_timeout(cardinal, sess, step)
                elif step == "waiting_password":
                    login_at = sess.get("login_time", now)
                    if not sess.get("reminded_password"):
                        if (now - login_at) >= 120:
                            sess["reminded_password"] = True
                            sess["reminded_password_time"] = now
                            _register_buyer_session(sess)
                            msg = _render_buyer_msg(
                                "reminder_password",
                                order_id=oid,
                                buyer_username=buyer_user,
                                country_name=country_name,
                                login=login
                            )
                            _send_buyer_fp_msg(cardinal, chat_id, msg, buyer_username=buyer_user)
                            _log_event("inactivity_reminder_sent", order_id=oid, step=step)
                    else:
                        rem_time = sess.get("reminded_password_time", login_at + 120)
                        if (now - rem_time) >= 180:
                            _handle_user_inactivity_timeout(cardinal, sess, step)
                elif step == "waiting_credentials":
                    cred_at = sess.get("cred_time", now)
                    if not sess.get("reminded_credentials"):
                        if (now - cred_at) >= 180:
                            sess["reminded_credentials"] = True
                            sess["reminded_credentials_time"] = now
                            _register_buyer_session(sess)
                            msg = (
                                f"⚠️ Напоминание по заказу #{oid}!\n\n"
                                f"Мы всё ещё ожидаем корректные данные для входа в Steam в формате «логин:пароль»."
                            )
                            _send_buyer_fp_msg(cardinal, chat_id, msg, buyer_username=buyer_user)
                            _log_event("inactivity_reminder_sent", order_id=oid, step=step)
                    else:
                        rem_time = sess.get("reminded_credentials_time", cred_at + 180)
                        if (now - rem_time) >= 180:
                            _handle_user_inactivity_timeout(cardinal, sess, step)
        except Exception as e:
            logger.debug(f"[{NAME}] Ошибка цикла проверки неактивности: {e}")
def _check_duplicate_plugin_files():
    try:
        plugins_dir = os.path.join(BASE_DIR, "plugins")
        if not os.path.isdir(plugins_dir):
            return
        current_name = os.path.basename(__file__) if "__file__" in globals() else "SRC-Plugin.py"
        duplicates = []
        for fname in os.listdir(plugins_dir):
            if not fname.endswith(".py"):
                continue
            if fname == current_name:
                continue
            f_lower = fname.lower()
            if any(t in f_lower for t in ("src-plugin", "steam region changer", "src_plugin", "steam-region-changer")):
                duplicates.append(fname)
        if duplicates:
            logger.warning(
                f"[{NAME}] ⚠️ ВНИМАНИЕ: в папке plugins/ обнаружены дубликаты плагина: {', '.join(duplicates)}! "
                f"Наличие нескольких копий приводит к конфликту UUID в Cardinal. "
                f"Пожалуйста, удалите лишние копии из папки plugins/!"
            )
    except Exception:
        pass
def init_cardinal(cardinal, *args):
    global cardinal_instance, bot_instance, admin_chat_id
    cardinal_instance = cardinal
    _check_duplicate_plugin_files()
    _init_storage()
    try:
        _stop_inactivity_watcher.clear()
        threading.Thread(target=_inactivity_watcher_loop, args=(cardinal,), daemon=True, name="SRC_InactivityWatcher").start()
    except Exception as e:
        logger.warning(f"[{NAME}] Ошибка запуска фонового таймера напоминаний: {e}")
    bot_obj = getattr(getattr(cardinal, 'telegram', None), 'bot', None)
    globals()['bot_instance'] = bot_obj
    try:
        auth = getattr(getattr(cardinal, 'telegram', None), 'authorized_users', None)
        if isinstance(auth, dict) and auth:
            admin_chat_id = int(list(auth.keys())[0])
    except Exception:
        admin_chat_id = None
    try:
        cardinal.add_telegram_commands(UUID, [('src_v2', 'Steam Region Changer v2: меню смены региона Steam', True)])
    except Exception as e:
        logger.warning(f"[{NAME}] Ошибка регистрации команд: {e}")
    if bot_obj:
        def _open_plugin_home(call):
            try:
                bot_obj.answer_callback_query(call.id)
            except Exception:
                pass
            cid = getattr(getattr(getattr(call, 'message', None), 'chat', None), 'id', None)
            mid = getattr(getattr(call, 'message', None), 'message_id', None)
            if cid:
                _open_home(cid, mid)
        def _plugin_entry(data):
            data = str(data or '')
            if data in (CBT_SETTINGS, f'{UUID}:0'):
                return True
            edit = getattr(_CBT, 'EDIT_PLUGIN', None)
            settings = getattr(_CBT, 'PLUGIN_SETTINGS', None)
            return bool((edit is not None and data.startswith(f'{edit}:{UUID}')) or
                        (settings is not None and data.startswith(f'{settings}:{UUID}')))
        try:
            cardinal.telegram.cbq_handler(_open_plugin_home, func=lambda call: _plugin_entry(getattr(call, 'data', None)))
        except Exception as e:
            logger.warning(f"[{NAME}] Не удалось привязать кнопку настроек: {e}")
        try:
            bot_obj.register_callback_query_handler(
                _cb_router,
                func=lambda call: isinstance(getattr(call, 'data', None), str) and call.data.startswith('src_')
            )
        except Exception as e:
            logger.warning(f"[{NAME}] Не удалось привязать callback-обработчик: {e}")
        def _cmd_src(message):
            cid = getattr(getattr(message, 'chat', None), 'id', None)
            if cid and _is_authorized(getattr(getattr(message, 'from_user', None), 'id', None)):
                _open_home(cid)
        try:
            bot_obj.register_message_handler(_cmd_src, commands=['src_v2'])
        except Exception as e:
            logger.warning(f"[{NAME}] Не удалось привязать команду /src_v2: {e}")
        def _msg_predicate(message):
            cid = getattr(getattr(message, 'chat', None), 'id', None)
            if not cid:
                return False
            return cid in _waiting
        try:
            bot_obj.register_message_handler(_handle_waiting_message, func=_msg_predicate, content_types=['text', 'document'])
            if hasattr(bot_obj, 'message_handlers') and isinstance(bot_obj.message_handlers, list) and bot_obj.message_handlers:
                h = bot_obj.message_handlers.pop()
                bot_obj.message_handlers.insert(0, h)
        except Exception as e:
            logger.warning(f"[{NAME}] Не удалось привязать обработчик ввода: {e}")
    logger.info(f"[{NAME}] Плагин инициализирован (v{VERSION})")
def order_status_changed_handler(cardinal, event, *args):
    try:
        order = getattr(event, 'order', None)
        order_id = (
            getattr(order, 'id', None)
            or getattr(order, 'order_id', None)
            or getattr(event, 'order_id', None)
        )
        if not order_id:
            return
        oid = str(order_id).strip().lstrip('#')
        status = (
            getattr(order, 'status', None)
            or getattr(event, 'status', None)
            or getattr(event, 'new_status', None)
        )
        status_name = str(getattr(status, 'name', status) or '').upper()
        if status_name in ('CLOSED', 'REFUNDED', 'PARTIALLY_REFUNDED', '1', '2', '3'):
            chat_id = getattr(order, 'chat_id', None) or getattr(event, 'chat_id', None)
            logger.info(f"[{NAME}] Заказ #{oid} сменил статус на {status_name} — завершаем сессию")
            _terminate_buyer_session(oid, reason=f"OrderStatusChanged:{status_name}", chat_id=chat_id)
    except Exception as e:
        logger.exception(f"[{NAME}] Ошибка в order_status_changed_handler: {e}")

def on_delete(cardinal, *args, **kwargs):
    _stop_inactivity_watcher.set()
    try:
        with _sessions_lock:
            for sess in list(_active_sessions):
                sess["canceled"] = True
                ev = sess.get("cancel_qr_event")
                if isinstance(ev, threading.Event):
                    ev.set()
                guard_ev = sess.get("guard_event")
                if isinstance(guard_ev, threading.Event):
                    guard_ev.set()
    except Exception:
        pass
    for executor in (_EVENT_EXECUTOR, _ORDER_EXECUTOR):
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
    logger.info(f"[{NAME}] Плагин удален.")
BIND_TO_PRE_INIT = [init_cardinal]
BIND_TO_NEW_ORDER = [handle_new_order]
BIND_TO_NEW_MESSAGE = [handle_new_message]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [handle_new_message]
BIND_TO_ORDER_STATUS_CHANGED = [order_status_changed_handler]
BIND_TO_DELETE = on_delete