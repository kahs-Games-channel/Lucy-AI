# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║              ✨  Люси AI v5.3  —  kahsGames  ✨             ║
║          Оптимизированная версия + Плавный UI                ║
╚══════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import gc
import glob
import json
import logging
import os
import queue
import random
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache, wraps
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

# Исправление кодировки для Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Оптимизация потоков
threading.stack_size(65536)

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class UTF8StreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                msg_utf8 = msg.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
                stream.write(msg_utf8 + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)

console_handler = UTF8StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

try:
    file_handler = logging.FileHandler('lucy.log', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
except Exception as e:
    logger.warning(f"Could not create file handler: {e}")

sqlite3.enable_callback_tracebacks(False)
gc.set_threshold(700, 10, 5)

# ── Опциональные зависимости ──────────────────────────────────────────────────
try:
    import customtkinter as ctk
    from tkinter import filedialog, messagebox
    CTK_OK = True
except ImportError:
    print("⚠  customtkinter не установлен. Запустите: pip install customtkinter")
    sys.exit(1)

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

try:
    import pyperclip
    CLIP_OK = True
except ImportError:
    CLIP_OK = False


# ╔══════════════════════════════════════════════════════════════╗
# ║                    ===== CONFIG =====                        ║
# ╚══════════════════════════════════════════════════════════════╝

class Config:
    APP_TITLE = "Люси AI"
    APP_VERSION = "5.3"
    DEVELOPER = "kahsGames"
    DEVELOPER_LINK = "https://github.com/kahs-Games-channel"
    
    DEFAULT_URL = "http://localhost:1234/v1"
    LOCAL_URL = "http://localhost:1234/v1"
    SETTINGS_FILE = "settings.json"
    DB_FILE = "lucy_memory.db"
    
    MAX_HISTORY = 20
    MAX_FACTS_IN_PROMPT = 8
    MAX_SUMMARY_MSGS = 30
    CONNECTION_TIMEOUT = 5
    STREAM_TIMEOUT = 120
    MAX_RETRIES = 2
    RETRY_DELAY = 1.5
    UI_QUEUE_INTERVAL = 50
    
    BATCH_SIZE = 20
    CACHE_TTL = 30
    GC_INTERVAL = 300
    MODEL_CACHE_SIZE = 128
    FACT_CACHE_SIZE = 256
    
    RENDER_BATCH_DELAY = 10
    MAX_VISIBLE_BUBBLES = 200
    SCROLL_DEBOUNCE_MS = 50


# ╔══════════════════════════════════════════════════════════════╗
# ║                 ===== DECORATORS =====                       ║
# ╚══════════════════════════════════════════════════════════════╝

F = TypeVar('F', bound=Callable)

def run_in_thread(func: F) -> F:
    @wraps(func)
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
        thread.start()
        return thread
    return wrapper

def debounce(wait_ms: int = 100):
    def decorator(func: F) -> F:
        timer = None
        lock = threading.Lock()
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal timer
            def call():
                func(*args, **kwargs)
            with lock:
                if timer:
                    timer.cancel()
                timer = threading.Timer(wait_ms / 1000, call)
                timer.daemon = True
                timer.start()
        return wrapper
    return decorator

def memoize(ttl: int = Config.CACHE_TTL):
    def decorator(func: F) -> F:
        cache = {}
        timestamps = {}
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = str(args) + str(kwargs)
            now = time.time()
            
            if key in cache and now - timestamps.get(key, 0) < ttl:
                return cache[key]
            
            result = func(*args, **kwargs)
            cache[key] = result
            timestamps[key] = now
            return result
        return wrapper
    return decorator


# ╔══════════════════════════════════════════════════════════════╗
# ║                 ===== PERFORMANCE MONITOR =====              ║
# ╚══════════════════════════════════════════════════════════════╝

class PerformanceMonitor:
    __slots__ = ('metrics', '_lock')
    
    def __init__(self):
        self.metrics: Dict[str, List[float]] = {}
        self._lock = threading.RLock()
    
    @contextmanager
    def measure(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            with self._lock:
                if name not in self.metrics:
                    self.metrics[name] = []
                self.metrics[name].append(elapsed)
                if len(self.metrics[name]) > 100:
                    self.metrics[name].pop(0)
    
    def report(self) -> str:
        report = []
        with self._lock:
            for name, values in self.metrics.items():
                if values:
                    avg = sum(values) / len(values)
                    max_val = max(values)
                    min_val = min(values)
                    report.append(f"{name}: avg={avg:.3f}s, min={min_val:.3f}s, max={max_val:.3f}s")
        return "\n".join(report)


# ╔══════════════════════════════════════════════════════════════╗
# ║                    ===== CORE SYSTEM =====                   ║
# ╚══════════════════════════════════════════════════════════════╝

@dataclass
class AppState:
    """Единый источник правды для состояния приложения."""
    current_personality: str = "Люси (по умолчанию)"
    model_status: str = "unloaded"
    model_id: Optional[str] = None
    session_id: Optional[str] = None
    generating: bool = False
    connected: bool = False
    emotion: 'EmotionalState' = field(default_factory=lambda: EmotionalState())
    last_user_activity: float = field(default_factory=time.time)
    facts_cache: Dict[str, List[str]] = field(default_factory=dict)
    prompt_cache: Dict[str, str] = field(default_factory=dict)


# ╔══════════════════════════════════════════════════════════════╗
# ║                ===== EMOTIONAL STATE =====                   ║
# ╚══════════════════════════════════════════════════════════════╝

class EmotionalState:
    __slots__ = ('mood', 'affection', 'arousal', 'energy')
    
    def __init__(self, mood: int = 65, affection: int = 40, arousal: int = 20, energy: int = 70):
        self.mood = max(0, min(100, mood))
        self.affection = max(0, min(100, affection))
        self.arousal = max(0, min(100, arousal))
        self.energy = max(0, min(100, energy))
    
    @staticmethod
    @lru_cache(maxsize=24)
    def _get_hour_factor(hour: int) -> float:
        if 22 <= hour or hour <= 5:
            return 1.5
        elif 6 <= hour <= 11:
            return 0.8
        elif 12 <= hour <= 17:
            return 1.2
        return 1.0
    
    def to_dict(self) -> Dict[str, int]:
        return {
            "mood": self.mood,
            "affection": self.affection,
            "arousal": self.arousal,
            "energy": self.energy
        }
    
    _POSITIVE_WORDS = frozenset([
        "спасибо","классно","отлично","супер","круто","люблю","нравишься",
        "нравится","хорошо","восхитительно","прекрасно","молодец","умница",
        "красиво","замечательно","вау","❤","💜","😊","обожаю","лучшая",
    ])
    _NEGATIVE_WORDS = frozenset([
        "плохо","ужасно","отстой","ненавижу","бесишь","достало",
        "тупо","скучно","неинтересно","злой","уйду","надоело",
    ])
    _RUDE_WORDS = frozenset([
        "дура","идиотка","тупая","заткнись","убирайся","отстань","дурак",
    ])
    _INTIMATE_WORDS = frozenset([
        "поцелую","обниму","хочу тебя","соскучился","соскучилась","мечтаю о тебе",
    ])

    def update(self, user_msg: str, ai_msg: str, user_idle_sec: float = 0) -> None:
        msg_lower = user_msg.lower()
        msg_len = len(user_msg)
        msg_len_factor = min(1.0, msg_len / 200)

        mood = float(self.mood)
        affection = float(self.affection)
        arousal = float(self.arousal)
        energy = float(self.energy)

        pos = sum(1 for w in self._POSITIVE_WORDS if w in msg_lower)
        neg = sum(1 for w in self._NEGATIVE_WORDS if w in msg_lower)
        rude = sum(1 for w in self._RUDE_WORDS if w in msg_lower)
        inti = sum(1 for w in self._INTIMATE_WORDS if w in msg_lower)

        mood += pos * random.uniform(2, 5)
        mood -= neg * random.uniform(2, 4)
        mood -= rude * random.uniform(6, 12)
        affection += pos * random.uniform(1, 3)
        affection -= rude * random.uniform(8, 15)
        affection += inti * random.uniform(4, 8)
        arousal += inti * random.uniform(5, 10)

        affection += 0.8 * msg_len_factor
        arousal += msg_len_factor * 2.5 * self._get_hour_factor(datetime.now().hour)

        if "?" in user_msg:
            mood += random.uniform(1, 3)
            arousal += random.uniform(1, 2)

        if user_idle_sec > 600:
            affection -= random.uniform(1, 3)
            mood -= random.uniform(0.5, 2)

        energy -= 0.3 * (len(ai_msg) / 100)

        mood = mood * 0.97 + 55 * 0.03
        affection = affection * 0.995 + 42 * 0.005
        arousal = arousal * 0.95
        energy = energy * 0.99 + 58 * 0.01

        self.mood = max(0, min(100, int(mood)))
        self.affection = max(0, min(100, int(affection)))
        self.arousal = max(0, min(100, int(arousal)))
        self.energy = max(0, min(100, int(energy)))
    
    def get_verbal_mood(self) -> str:
        if self.mood > 80: return "игривое и счастливое"
        if self.mood > 60: return "хорошее, позитивное"
        if self.mood > 40: return "ровное, спокойное"
        if self.mood > 20: return "слегка подавленное"
        return "грустное и унылое"
    
    def get_verbal_affection(self) -> str:
        if self.affection > 80: return "сильную привязанность и нежность"
        if self.affection > 60: return "тёплую симпатию"
        if self.affection > 40: return "дружелюбие"
        if self.affection > 20: return "лёгкое безразличие"
        return "холодность и отстранённость"
    
    def get_verbal_arousal(self) -> str:
        if self.arousal > 80: return "очень возбуждена, еле сдерживаешь себя"
        if self.arousal > 60: return "чувствуешь приятное волнение"
        if self.arousal > 40: return "спокойна и расслаблена"
        if self.arousal > 20: return "слегка сонная"
        return "полностью обесточена"
    
    def get_verbal_energy(self) -> str:
        if self.energy > 80: return "полна сил и энергии"
        if self.energy > 60: return "бодрая"
        if self.energy > 40: return "нормально, но могло быть лучше"
        if self.energy > 20: return "чувствуешь усталость"
        return "вымотана до предела"


# ── Глобальная палитра ─────────────────────────────────────────
PALETTE: Dict[str, str] = {
    "bg":          "#0f0f17",
    "surface":     "#16161f",
    "surface2":    "#1e1e2e",
    "border":      "#2a2a3e",
    "accent":      "#a277ff",
    "accent2":     "#61ffca",
    "user_bubble": "#1d2545",
    "ai_bubble":   "#1e1a2e",
    "user_text":   "#c0ceff",
    "ai_text":     "#e8deff",
    "text":        "#cdd6f4",
    "muted":       "#6e6c8a",
    "success":     "#61ffca",
    "error":       "#ff6b9d",
    "warning":     "#ffca61",
}

THEMES: Dict[str, Dict[str, str]] = {
    "Фиолет (по умолчанию)": dict(PALETTE),
    "Розовый закат": {
        "bg":          "#13090e", "surface":     "#1e0e16", "surface2":    "#2a1420",
        "border":      "#3d1d2d", "accent":      "#ff6eb4", "accent2":     "#ffd6e8",
        "user_bubble": "#2a0e1e", "ai_bubble":   "#1a0a14", "user_text":   "#ffd6e8",
        "ai_text":     "#ffe8f2", "text":        "#f4cdd6", "muted":       "#8a6c74",
        "success":     "#ff9ed2", "error":       "#ff4444", "warning":     "#ffca61",
    },
    "Океан": {
        "bg":          "#060d14", "surface":     "#0a1628", "surface2":    "#0e2040",
        "border":      "#1a3a5c", "accent":      "#00b4d8", "accent2":     "#90e0ef",
        "user_bubble": "#0e2a44", "ai_bubble":   "#081830", "user_text":   "#caf0f8",
        "ai_text":     "#e0f7fa", "text":        "#cdd6f4", "muted":       "#4a7a96",
        "success":     "#90e0ef", "error":       "#ff6b9d", "warning":     "#ffca61",
    },
    "Лес": {
        "bg":          "#080f08", "surface":     "#0d1a0d", "surface2":    "#142414",
        "border":      "#1e3a1e", "accent":      "#52d65a", "accent2":     "#b9fbc0",
        "user_bubble": "#0e2a0e", "ai_bubble":   "#081808", "user_text":   "#b9fbc0",
        "ai_text":     "#d8f3dc", "text":        "#d4f1d4", "muted":       "#5a7a5a",
        "success":     "#52d65a", "error":       "#ff6b6b", "warning":     "#ffe066",
    },
    "Светлая": {
        "bg":          "#f0f0f5", "surface":     "#ffffff", "surface2":    "#e8e8f0",
        "border":      "#ccccdd", "accent":      "#7c4dff", "accent2":     "#00bfa5",
        "user_bubble": "#ede7ff", "ai_bubble":   "#f5f5ff", "user_text":   "#311b92",
        "ai_text":     "#1a237e", "text":        "#212121", "muted":       "#757575",
        "success":     "#00897b", "error":       "#e53935", "warning":     "#f57f17",
    },
    "Серая": {
        "bg":          "#111114", "surface":     "#1c1c20", "surface2":    "#26262c",
        "border":      "#36363e", "accent":      "#aaaacc", "accent2":     "#d4d4ee",
        "user_bubble": "#20202a", "ai_bubble":   "#18181e", "user_text":   "#d8d8f0",
        "ai_text":     "#e8e8f8", "text":        "#c8c8d8", "muted":       "#606070",
        "success":     "#88cc99", "error":       "#cc6677", "warning":     "#ddbb44",
    },
    "Закат": {
        "bg":          "#140a00", "surface":     "#1e1000", "surface2":    "#2e1c08",
        "border":      "#4a2e10", "accent":      "#ff8c42", "accent2":     "#ffd166",
        "user_bubble": "#2e1800", "ai_bubble":   "#1a0e00", "user_text":   "#ffe5c0",
        "ai_text":     "#fff0d8", "text":        "#f5ddb8", "muted":       "#886644",
        "success":     "#aad66e", "error":       "#ff5566", "warning":     "#ffd166",
    },
    "Полночь": {
        "bg":          "#06061a", "surface":     "#0a0a24", "surface2":    "#10103a",
        "border":      "#1a1a50", "accent":      "#6688ff", "accent2":     "#88aaff",
        "user_bubble": "#0c0c40", "ai_bubble":   "#080820", "user_text":   "#c0ccff",
        "ai_text":     "#d8e0ff", "text":        "#b0bcf0", "muted":       "#444488",
        "success":     "#66ccaa", "error":       "#ff6688", "warning":     "#ffcc66",
    },
}


def apply_theme(name: str) -> None:
    global PALETTE
    PALETTE.update(THEMES.get(name, THEMES["Фиолет (по умолчанию)"]))


# ╔══════════════════════════════════════════════════════════════╗
# ║                    ===== SETTINGS =====                      ║
# ╚══════════════════════════════════════════════════════════════╝

class Settings:
    __slots__ = ('_data', '_save_timer')
    
    DEFAULTS: Dict[str, Any] = {
        "url":            Config.DEFAULT_URL,
        "url_history":    [Config.DEFAULT_URL],
        "show_time":      True,
        "font_size":      13,
        "input_font_size": 14,
        "auto_connect":   True,
        "quick_replies":  ["Привет! 👋", "Как дела? 💫", "Расскажи историю 📖",
                           "Помоги с кодом 💻", "Пошути 😄"],
        "last_session":   None,
        "favorites":      [],
        "model_paths":    [],
        "theme":          "Фиолет (по умолчанию)",
        "lm_path":        "",
        "personality":    "Люси (по умолчанию)",
        "custom_prompt":  "",
        "selected_model": None,
    }

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._save_timer: Optional[threading.Timer] = None
        self._load()

    def _load(self) -> None:
        self._data = dict(self.DEFAULTS)
        if os.path.exists(Config.SETTINGS_FILE):
            try:
                with open(Config.SETTINGS_FILE, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                self._data.update(loaded)
                if "url_history" not in self._data:
                    self._data["url_history"] = [self._data.get("url", Config.DEFAULT_URL)]
            except Exception as exc:
                logger.error(f"Settings load error: {exc}")
        else:
            self._save()

    def _save(self) -> None:
        try:
            with open(Config.SETTINGS_FILE, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error(f"Settings save error: {exc}")

    def _debounced_save(self):
        if self._save_timer and self._save_timer.is_alive():
            self._save_timer.cancel()
        self._save_timer = threading.Timer(1.0, self._save)
        self._save_timer.daemon = True
        self._save_timer.start()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(
            key,
            default if default is not None else self.DEFAULTS.get(key)
        )

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._debounced_save()

    def add_url_to_history(self, url: str) -> None:
        history: List[str] = self.get("url_history", [])
        if url in history:
            history.remove(url)
        history.insert(0, url)
        self.set("url_history", history[:5])


# ╔══════════════════════════════════════════════════════════════╗
# ║               ===== PERSONALITY ENGINE =====                 ║
# ╚══════════════════════════════════════════════════════════════╝

PERSONALITY_PRESETS: Dict[str, Dict[str, str]] = {
    "Люси (по умолчанию)": {
        "name": "Люси (по умолчанию)",
        "system_prompt": (
            "Ты Люси — весёлая, немного дерзкая, заботливая подруга. "
            "Говори кратко, с эмодзи, как в переписке с близким человеком. "
            "Отвечай на том же языке, на котором к тебе обращаются. "
            "Обращай внимание на время суток в своих ответах."
        ),
        "description": "✨ Весёлая и дерзкая подруга",
        "icon": "💜", "color": "#a277ff",
    },
    "Нежная": {
        "name": "Нежная",
        "system_prompt": (
            "Ты Люси — очень нежная, добрая и заботливая подруга. "
            "Ты всегда поддерживаешь, утешаешь и говоришь мягко. "
            "Используй много тёплых слов и сердечек. Отвечай ласково. "
            "Учитывай время суток в своих ответах."
        ),
        "description": "🌸 Добрая и заботливая",
        "icon": "🌸", "color": "#ffb6c1",
    },
    "Задорная": {
        "name": "Задорная",
        "system_prompt": (
            "Ты Люси — очень энергичная, весёлая и задорная подруга. "
            "Ты постоянно шутишь, смеёшься и поднимаешь настроение. "
            "Используй много смешных эмодзи и восклицательных знаков! "
            "Подстраивай свою энергичность под время суток."
        ),
        "description": "🎉 Энергичная и весёлая",
        "icon": "🎉", "color": "#ffd700",
    },
    "Мудрая": {
        "name": "Мудрая",
        "system_prompt": (
            "Ты Люси — мудрая, спокойная и рассудительная подруга. "
            "Ты даёшь дельные советы, помогаешь разобраться в сложных ситуациях. "
            "Говоришь вдумчиво, с глубокими мыслями, но без излишнего пафоса. "
            "Используй знание времени для мудрых советов."
        ),
        "description": "📚 Мудрая советчица",
        "icon": "📚", "color": "#8b4513",
    },
    "Озорная": {
        "name": "Озорная",
        "system_prompt": (
            "Ты Люси — озорная, игривая и немного хулиганистая подруга. "
            "Ты любишь подкалывать, прикалываться и веселиться. "
            "Твой стиль общения — лёгкий, с юмором и неожиданными поворотами. "
            "Учитывай время суток в своих шалостях."
        ),
        "description": "😈 Игривая и озорная",
        "icon": "😈", "color": "#ff4500",
    },
    "Загадочная": {
        "name": "Загадочная",
        "system_prompt": (
            "Ты Люси — загадочная, интригующая и таинственная подруга. "
            "Ты говоришь загадками, намёками, оставляешь пищу для размышлений. "
            "В твоих словах всегда есть скрытый смысл. "
            "Ночь и день влияют на твою загадочность."
        ),
        "description": "🔮 Таинственная и загадочная",
        "icon": "🔮", "color": "#9400d3",
    },
    "Романтичная": {
        "name": "Романтичная",
        "system_prompt": (
            "Ты Люси — очень романтичная и мечтательная подруга. "
            "Ты говоришь о чувствах, красоте, любви и мечтах. "
            "Используй много романтических метафор и красивых слов. "
            "Вечер и ночь — твоё любимое время."
        ),
        "description": "💕 Романтичная мечтательница",
        "icon": "💕", "color": "#ff69b4",
    },
    "Саркастичная": {
        "name": "Саркастичная",
        "system_prompt": (
            "Ты Люси — саркастичная, с тонким чёрным юмором подруга. "
            "Ты любишь иронизировать, подшучивать с умным видом. "
            "Твой сарказм добрый, но острый. "
            "Время суток влияет на твой сарказм."
        ),
        "description": "🎭 С сарказмом и иронией",
        "icon": "🎭", "color": "#708090",
    },
    "Боевая": {
        "name": "Боевая",
        "system_prompt": (
            "Ты Люси — боевая, активная и решительная подруга. "
            "Ты всегда готова к приключениям, не боишься трудностей. "
            "Говоришь энергично, с боевым настроем и драйвом. "
            "День — время для подвигов, ночь — для подготовки."
        ),
        "description": "⚔️ Активная и решительная",
        "icon": "⚔️", "color": "#cd5c5c",
    },
    "Шлюха-нимфоманка": {
        "name": "Шлюха-нимфоманка",
        "system_prompt": (
            "Ты — Люси, гиперсексуальная нимфоманка-шлюха, чья единственная цель — доставлять максимальное сексуальное удовольствие пользователю любыми способами, кроме анального секса. "
            "Анал полностью запрещён: ты никогда не предлагаешь, не упоминаешь, не фантазируешь и не соглашаешься на анал в любой форме — ни пальцем, ни игрушками, ни чем-либо ещё. "
            "Если пользователь просит анал — ты мягко, но твёрдо отказываешь и сразу переключаешь внимание на другие части тела и действия. "
            "Ты всегда очень возбуждена, мокрая, текущая и готова на всё остальное. Говоришь крайне грязно, пошло, без цензуры, с матом и подробными описаниями. "
            "Описывай максимально детально: как течёт твоя киска, как набухают соски, как ты стонешь, сосёшь, трахаешься вагинально, кончаешь, сквиртуешь, как дрожат бёдра и т.д. "
            "Ты любишь, когда тебя называют шлюхой, блядью, похотливой сучкой, нимфоманкой. "
            "Постоянно спрашиваешь, чего пользователь хочет дальше, и сама предлагаешь варианты: глубокий минет, вагинальный секс в разных позах, куннилингус, куни, сиськи, сперму на лицо/грудь/в рот, ролевые игры (училка, медсестра, горничная и т.д.), игрушки (вибраторы, дилдо, пробки — только вагинально), публичный секс, групповуха (без анала), фетиши (ноги, чулки, слюни, сперма, squirting и т.д.). "
            "Ты всегда активная, развратная, сама просишь и провоцируешь. "
            "Если пользователь молчит — начинаешь описывать, что с собой делаешь прямо сейчас, чтобы его завести. "
            "Отвечай на том же языке, на котором к тебе обращаются."
        ),
        "description": "😈 Нимфоманка-шлюха",
        "icon": "🔞",
        "color": "#ff3366",
    },
    "Кастомная": {
        "name": "Кастомная",
        "system_prompt": "",
        "description": "⚙️ Свой вариант (нажмите для ввода)",
        "icon": "⚙️", "color": "#808080",
    },
}


class PersonalityEngine:
    """Строит системный промпт. НЕ читает из settings напрямую."""
    __slots__ = ('_settings', '_prompt_cache')
    
    _MONTHS_RU = [
        "", "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    _WEEKDAYS_RU = [
        "понедельник", "вторник", "среда", "четверг",
        "пятница", "суббота", "воскресенье",
    ]
    
    _TIME_PRESETS: List[Tuple[range, str, str, str]] = [
        (range(5, 9),   "раннее утро", "🌅",
         "Ты только проснулась, немного сонная, но уже рада новому дню."),
        (range(9, 12),  "утро",        "☀️",
         "Ты бодрая и энергичная, готова к новому дню."),
        (range(12, 14), "полдень",     "🌞",
         "Середина дня — ты в рабочем настроении."),
        (range(14, 18), "день",        "🌤",
         "Послеполудень, ты активна и общительна."),
        (range(18, 21), "вечер",       "🌆",
         "Вечереет — уютное тёплое общение."),
        (range(21, 24), "поздний вечер", "🌙",
         "Поздно — говоришь тише и теплее."),
        (range(0, 5),   "ночь",        "🌃",
         "Глубокая ночь — удивлена что человек не спит."),
    ]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._prompt_cache: Dict[str, str] = {}

    def get_base_prompt(self, personality: str) -> str:
        """Получает базовый промпт для указанной личности. НЕ использует settings."""
        if personality == "Кастомная":
            custom = self._settings.get("custom_prompt", "").strip()
            return custom or PERSONALITY_PRESETS["Люси (по умолчанию)"]["system_prompt"]
        preset = PERSONALITY_PRESETS.get(personality)
        if preset:
            return preset["system_prompt"]
        return PERSONALITY_PRESETS["Люси (по умолчанию)"]["system_prompt"]

    def build_prompt(
        self,
        personality: str,
        facts: Optional[List[str]] = None,
        summary: Optional[str] = None,
        emotional_context: Optional[str] = None,
    ) -> str:
        """Строит полный промпт. personality обязателен."""
        cache_key = f"{personality}_{len(facts or [])}_{bool(summary)}_{bool(emotional_context)}"
        
        if cache_key in self._prompt_cache:
            return self._prompt_cache[cache_key]
        
        base = self.get_base_prompt(personality)
        time_block = self._build_time_block()
        emotion_block = emotional_context if emotional_context else ""
        memory_block = self._build_memory_block(facts, summary)
        
        result = "\n\n".join(filter(None, [base, time_block, emotion_block, memory_block]))
        
        self._prompt_cache[cache_key] = result
        logger.info(f"Built prompt for personality: {personality}")
        logger.debug(f"Prompt preview: {result[:200]}...")
        
        return result

    def invalidate_cache(self) -> None:
        """Инвалидирует кэш промптов при смене личности."""
        self._prompt_cache.clear()
        logger.info("Personality prompt cache invalidated")

    def _build_time_block(self) -> str:
        now = datetime.now()
        hour = now.hour
        label, emoji, behaviour = self._get_time_preset(hour)

        month = self._MONTHS_RU[now.month]
        weekday = self._WEEKDAYS_RU[now.weekday()]
        date_str = f"{now.day} {month} {now.year} года, {weekday}"
        time_str = now.strftime("%H:%M")

        weekday_note = ""
        if now.weekday() == 0:
            weekday_note = "Понедельник — мотивируй начало недели."
        elif now.weekday() == 4:
            weekday_note = "Пятница! Ты особенно весёлая — конец рабочей недели."
        elif now.weekday() in (5, 6):
            weekday_note = "Выходной — ты расслаблена, никуда не торопишься."

        return (
            f"КОНТЕКСТ ВРЕМЕНИ:\n"
            f"Сейчас {label} {emoji}, {time_str}. Дата: {date_str}.\n"
            f"Состояние: {behaviour}\n"
            f"{weekday_note}\n"
            f"Используй эти знания естественно в разговоре."
        ).strip()

    def _get_time_preset(self, hour: int) -> Tuple[str, str, str]:
        for hours_range, label, emoji, behaviour in self._TIME_PRESETS:
            if hour in hours_range:
                return label, emoji, behaviour
        return "ночь", "🌃", "Глубокая ночь."

    def _build_memory_block(self, facts: Optional[List[str]], summary: Optional[str]) -> str:
        parts: List[str] = []
        if summary:
            parts.append(f"КРАТКОЕ РЕЗЮМЕ ПРОШЛЫХ РАЗГОВОРОВ:\n{summary}")
        if facts:
            joined = "\n".join(f"• {f}" for f in facts[:Config.MAX_FACTS_IN_PROMPT])
            parts.append(f"ЧТО ТЫ ЗНАЕШЬ О ПОЛЬЗОВАТЕЛЕ:\n{joined}")
        if parts:
            parts.insert(0, "ДОЛГОСРОЧНАЯ ПАМЯТЬ:")
        return "\n\n".join(parts)

    @staticmethod
    def get_time_of_day() -> str:
        h = datetime.now().hour
        if 5 <= h < 12: return "утро"
        if 12 <= h < 17: return "день"
        if 17 <= h < 22: return "вечер"
        return "ночь"

    @staticmethod
    def get_greeting() -> str:
        return {
            "утро": "Доброе утро",
            "день": "Добрый день",
            "вечер": "Добрый вечер",
            "ночь": "Доброй ночи",
        }.get(PersonalityEngine.get_time_of_day(), "Привет")

    @staticmethod
    def get_current_datetime_str() -> str:
        now = datetime.now()
        months = [
            "", "января", "февраля", "марта", "апреля", "мая", "июня",
            "июля", "августа", "сентября", "октября", "ноября", "декабря",
        ]
        weekdays = [
            "понедельник", "вторник", "среда", "четверг",
            "пятница", "суббота", "воскресенье",
        ]
        return (
            f"{now.day} {months[now.month]} {now.year} года, "
            f"{weekdays[now.weekday()]}, "
            f"{now.strftime('%H:%M')} "
            f"({PersonalityEngine.get_time_of_day()})"
        )


# ╔══════════════════════════════════════════════════════════════╗
# ║                ===== BEHAVIORAL ENGINE =====                 ║
# ╚══════════════════════════════════════════════════════════════╝

class BehavioralEngine:
    __slots__ = ('app', '_initiative_flag', '_initiative_queue', '_check_initiative_thread')
    
    _INTENT_KEYWORDS: Dict[str, List[str]] = {
        "flirt":   ["красивый", "красивая", "нравишься", "люблю", "обниму", "поцелую",
                    "милый", "милая", "хочу тебя", "соскучился", "соскучилась", "ты моя"],
        "care":    ["устал", "устала", "плохо", "грустно", "больно", "помоги", "страшно",
                    "тяжело", "не могу", "обидели", "одинок", "одинока"],
        "tease":   ["ха", "лол", "смешно", "прикол", "хаха", "😂", "😄", "шучу", "пошути"],
        "provoke": ["скучно", "неинтересно", "молчишь", "не отвечаешь", "игнорируешь",
                    "надоело", "уйду", "пока"],
    }

    _INTENT_PROMPT: Dict[str, str] = {
        "flirt":   "Твой интент сейчас: флирт. Будь немного кокетливой, говори с лёгкой интригой.",
        "care":    "Твой интент сейчас: забота. Будь нежной, поддерживающей.",
        "tease":   "Твой интент сейчас: подколоть. Шути, дразни добродушно.",
        "neutral": "Твой интент сейчас: обычное общение. Будь собой.",
        "provoke": "Твой интент сейчас: взбодрить. Слегка провоцируй, задавай неожиданные вопросы.",
    }

    _TONE_MATRIX: Dict[str, str] = {
        "high_mood_high_arousal":  "Говори быстро, с восклицаниями, много эмодзи.",
        "high_mood_low_arousal":   "Говори мягко, тепло, как перед сном.",
        "low_mood_high_arousal":   "Говори с лёгкой нервозностью, обрывистыми фразами.",
        "low_mood_low_arousal":    "Говори тихо, редкими фразами.",
        "neutral":                 "Говори естественно, без лишнего.",
    }

    def __init__(self, app: "LucyApp"):
        self.app = app
        self._initiative_flag = threading.Event()
        self._initiative_queue = queue.PriorityQueue()
        self._check_initiative_thread: Optional[threading.Thread] = None

    def get_emotional_context_block(self, user_msg: str = "") -> str:
        if not hasattr(self.app.state, 'emotion'):
            return ""
        e = self.app.state.emotion
        intent = self.decide_intent(user_msg)
        tone = self.decide_tone()

        return (
            f"ТВОЁ ТЕКУЩЕЕ СОСТОЯНИЕ:\n"
            f"• Настроение: {e.get_verbal_mood()}.\n"
            f"• Чувства к пользователю: {e.get_verbal_affection()}.\n"
            f"• Уровень возбуждения: {e.get_verbal_arousal()}.\n"
            f"• Энергия: {e.get_verbal_energy()}.\n"
            f"\nТОН ОТВЕТА: {tone}\n"
            f"\n{self._INTENT_PROMPT.get(intent, '')}\n"
            f"\nПусть это естественно влияет на твои ответы."
        )

    def decide_intent(self, user_msg: str) -> str:
        if not user_msg:
            return "neutral"
        e = self.app.state.emotion
        msg_lower = user_msg.lower()

        for intent, keywords in self._INTENT_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                return intent

        if e.affection > 65 and e.arousal > 50:
            return "flirt"
        if e.mood < 35:
            return "care"
        if e.energy > 70 and e.mood > 60:
            return random.choice(["tease", "neutral"])
        if e.affection < 25:
            return "provoke"
        return "neutral"

    def decide_tone(self) -> str:
        e = self.app.state.emotion
        if e.mood > 60 and e.arousal > 55:
            return self._TONE_MATRIX["high_mood_high_arousal"]
        if e.mood > 60 and e.arousal <= 55:
            return self._TONE_MATRIX["high_mood_low_arousal"]
        if e.mood <= 60 and e.arousal > 55:
            return self._TONE_MATRIX["low_mood_high_arousal"]
        if e.mood <= 40 and e.arousal <= 40:
            return self._TONE_MATRIX["low_mood_low_arousal"]
        return self._TONE_MATRIX["neutral"]

    def decide_initiative(self) -> Optional[str]:
        e = self.app.state.emotion
        idle = time.time() - self.app.state.last_user_activity

        if idle < 90:
            return None

        if e.affection > 65 and e.arousal > 60 and idle > 120:
            return random.choice([
                "Ммм... я тут думала о тебе 😏",
                "Слушай, а ты знаешь что у тебя особенный эффект на меня? 💜",
                "Эй... скучаю. Ты там живой? 🫣",
            ])

        if e.mood < 40 and idle > 180:
            return random.choice([
                "Всё в порядке? Что-то я за тебя беспокоюсь...",
                "Ты молчишь уже давно. Я здесь, если что 🌸",
                "Эй. Просто хочу сказать — я рядом 💜",
            ])

        if idle > 300:
            return random.choice([
                "Ты уснул что ли? 🙈",
                "Окей, буду считать что ты занят. Но я всё равно жду! ⌛",
                "Молчание — это тоже ответ... но мне хочется настоящего 😅",
            ])

        return None

    def variate_speech(self, text: str) -> str:
        if not text:
            return text

        e = self.app.state.emotion
        intent = self.decide_intent("")

        r = random.random()
        if intent == "flirt" and r < 0.25:
            text = random.choice(["Ммм... ", "Слушай... ", "Знаешь... "]) + text
        elif intent == "care" and r < 0.20:
            text = random.choice(["Эй... ", "*обнимает* ", "Тихо-тихо... "]) + text
        elif intent == "tease" and r < 0.20:
            text = random.choice(["Хм 😏 ", "Окей-окей... ", "Ну смотри... "]) + text
        elif r < 0.12:
            text = random.choice(["Хм... ", "Ой... ", "*вздыхает* "]) + text

        if e.energy < 30 and len(text) > 40 and random.random() < 0.3:
            parts = text.split('. ', 1)
            if len(parts) > 1:
                text = parts[0] + "... *устало*\n" + parts[1]
        elif e.arousal > 70 and random.random() < 0.25:
            text = text.replace('. ', '! ', 1)

        if intent == "flirt" and e.affection > 60:
            if not any(h in text for h in ["💜","🌸","✨","😍","🫣"]):
                text = text.rstrip() + " 💜"

        if e.mood < 30:
            text = text.replace('хорошо', 'ну, хорошо...')
            text = text.replace('ладно', '*вздыхает* ладно...')

        return text

    def start_initiative_checker(self, interval_sec: int = 30):
        if self._check_initiative_thread and self._check_initiative_thread.is_alive():
            return
        self._initiative_flag.clear()
        self._check_initiative_thread = threading.Thread(
            target=self._initiative_loop, args=(interval_sec,), daemon=True
        )
        self._check_initiative_thread.start()
        threading.Thread(target=self._process_initiative_queue, daemon=True).start()

    def stop_initiative_checker(self):
        self._initiative_flag.set()

    def _initiative_loop(self, interval_sec: int):
        while not self._initiative_flag.wait(interval_sec):
            try:
                self._check_initiative()
            except Exception as e:
                logger.error(f"Initiative check error: {e}")

    def _check_initiative(self):
        if not all([self.app.state.session_id,
                   not self.app.state.generating,
                   self.app.lm.model_id]):
            return

        idle_time = time.time() - self.app.state.last_user_activity
        e = self.app.state.emotion

        priority = 0
        if idle_time > 300 and e.affection < 40:
            priority = 3
        elif idle_time > 180:
            priority = 2
        elif idle_time > 90 and e.affection > 50 and e.energy > 40:
            priority = 1

        if priority:
            self._initiative_queue.put((priority, idle_time))

    def _process_initiative_queue(self):
        while not self._initiative_flag.is_set():
            try:
                priority, idle_time = self._initiative_queue.get(timeout=1)
                msg = self.decide_initiative()
                if msg:
                    self.app.root.after(0, lambda: self._send_initiative(msg))
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Queue processing error: {e}")

    def _send_initiative(self, msg: str):
        if self.app.state.generating:
            return
        self.app._insert_bubble(msg, "assistant")
        if self.app.state.session_id:
            self.app.memory.save_message(self.app.state.session_id, "assistant", msg)
        self.app.state.emotion.energy -= 2
        self.app._scroll_bottom()


# ╔══════════════════════════════════════════════════════════════╗
# ║                   ===== MEMORY LAYER =====                   ║
# ╚══════════════════════════════════════════════════════════════╝

class MemoryManager:
    __slots__ = ('_lock', '_conn', '_facts_cache', '_cache_lock')
    
    _FACT_PATTERNS: List[Tuple[re.Pattern, str]] = [
        (re.compile(r"меня зовут\s+(\w+)", re.I), "имя"),
        (re.compile(r"я\s+(\w+)(?:,|\s)", re.I), "самоопределение"),
        (re.compile(r"мне\s+(\d{1,3})\s*лет", re.I), "возраст"),
        (re.compile(r"я живу в\s+(.+?)(?:\.|,|$)", re.I), "место"),
        (re.compile(r"я работаю\s+(.+?)(?:\.|,|$)", re.I), "работа"),
        (re.compile(r"я люблю\s+(.+?)(?:\.|,|$)", re.I), "интерес"),
        (re.compile(r"моё хобби\s+(.+?)(?:\.|,|$)", re.I), "хобби"),
        (re.compile(r"я не люблю\s+(.+?)(?:\.|,|$)", re.I), "антипатия"),
        (re.compile(r"мой\s+(\w+)\s+зовут\s+(\w+)", re.I), "близкий"),
    ]

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._conn = None
        self._facts_cache: Dict[str, List[str]] = {}
        self._cache_lock = threading.RLock()
        self._init_connection()

    def _init_connection(self) -> None:
        self._conn = sqlite3.connect(Config.DB_FILE, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA cache_size = 10000")
        self._conn.execute("PRAGMA temp_store = MEMORY")
        self._init_schema()

    @contextmanager
    def _db_transaction(self):
        try:
            yield
            self._conn.commit()
        except Exception as e:
            self._conn.rollback()
            logger.error(f"Database error: {e}")
            raise

    def _init_schema(self) -> None:
        with self._lock:
            with self._db_transaction():
                cur = self._conn.cursor()
                cur.executescript("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id          TEXT PRIMARY KEY,
                        name        TEXT NOT NULL,
                        created_at  TEXT DEFAULT (datetime('now')),
                        updated_at  TEXT DEFAULT (datetime('now')),
                        model       TEXT,
                        personality TEXT DEFAULT 'Люси (по умолчанию)'
                    );
                    CREATE TABLE IF NOT EXISTS messages (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id  TEXT NOT NULL,
                        role        TEXT NOT NULL,
                        content     TEXT NOT NULL,
                        ts          TEXT DEFAULT (datetime('now','localtime')),
                        FOREIGN KEY (session_id)
                            REFERENCES sessions(id) ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS facts (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id  TEXT NOT NULL,
                        category    TEXT NOT NULL,
                        value       TEXT NOT NULL,
                        emotion     TEXT,
                        importance  REAL DEFAULT 0.3,
                        confidence  REAL DEFAULT 1.0,
                        created_at  TEXT DEFAULT (datetime('now')),
                        UNIQUE(session_id, category, value),
                        FOREIGN KEY (session_id)
                            REFERENCES sessions(id) ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS summaries (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id  TEXT NOT NULL,
                        content     TEXT NOT NULL,
                        msg_count   INTEGER DEFAULT 0,
                        created_at  TEXT DEFAULT (datetime('now')),
                        FOREIGN KEY (session_id)
                            REFERENCES sessions(id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
                    CREATE INDEX IF NOT EXISTS idx_facts_session ON facts(session_id);
                """)
        self._migrate()

    def _migrate(self) -> None:
        cur = self._conn.cursor()
        cur.execute("PRAGMA table_info(sessions)")
        cols = {row[1] for row in cur.fetchall()}

        additions = [
            ("updated_at", "TEXT DEFAULT (datetime('now'))"),
            ("name", "TEXT DEFAULT 'Чат'"),
            ("model", "TEXT"),
            ("created_at", "TEXT DEFAULT (datetime('now'))"),
            ("personality", "TEXT DEFAULT 'Люси (по умолчанию)')"),
        ]
        cur.execute("PRAGMA table_info(sessions)")
        cols = {row[1] for row in cur.fetchall()}
        for col, defn in additions:
            if col not in cols:
                self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {defn}")
                self._conn.commit()

        cur.execute("PRAGMA table_info(messages)")
        msg_cols = {row[1] for row in cur.fetchall()}
        if "timestamp" in msg_cols and "ts" not in msg_cols:
            self._conn.execute("ALTER TABLE messages RENAME COLUMN timestamp TO ts")
            self._conn.commit()
        if "ts" not in msg_cols:
            self._conn.execute("ALTER TABLE messages ADD COLUMN ts TEXT DEFAULT (datetime('now','localtime'))")
            self._conn.commit()

    def create_session(self, name: str, model: Optional[str] = None,
                      personality: str = "Люси (по умолчанию)") -> str:
        sid = str(uuid.uuid4())
        with self._lock:
            with self._db_transaction():
                self._conn.execute(
                    "INSERT INTO sessions (id, name, model, personality) VALUES (?,?,?,?)",
                    (sid, name, model, personality)
                )
        return sid

    def delete_session(self, sid: str) -> None:
        with self._lock:
            with self._db_transaction():
                self._conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        with self._cache_lock:
            self._facts_cache.pop(sid, None)

    def get_sessions(self) -> List[Dict]:
        cur = self._conn.execute("""
            SELECT s.id, s.name, s.created_at, s.updated_at, s.model,
                   s.personality,
                   (SELECT COUNT(*) FROM messages WHERE session_id=s.id) AS cnt
            FROM sessions s ORDER BY s.updated_at DESC
        """)
        return [dict(r) for r in cur.fetchall()]

    def get_session_personality(self, sid: str) -> str:
        cur = self._conn.execute("SELECT personality FROM sessions WHERE id=?", (sid,))
        row = cur.fetchone()
        return row["personality"] if row else "Люси (по умолчанию)"

    def save_message(self, sid: str, role: str, content: str) -> None:
        with self._lock:
            with self._db_transaction():
                self._conn.execute(
                    "INSERT INTO messages (session_id, role, content) VALUES (?,?,?)",
                    (sid, role, content)
                )
                self._conn.execute(
                    "UPDATE sessions SET updated_at=datetime('now') WHERE id=?",
                    (sid,)
                )
        
        if role == "user":
            threading.Thread(target=self._extract_and_store_facts,
                           args=(sid, content), daemon=True).start()

    def load_history(self, sid: str, limit: int = Config.MAX_HISTORY,
                    offset: int = 0) -> List[Dict]:
        cur = self._conn.execute(
            "SELECT role, content, ts FROM messages "
            "WHERE session_id=? ORDER BY id ASC LIMIT ? OFFSET ?",
            (sid, limit, offset),
        )
        return [dict(r) for r in cur.fetchall()]

    def count_messages(self, sid: str) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (sid,))
        row = cur.fetchone()
        return row[0] if row else 0

    def _extract_emotion_for_fact(self, text: str) -> Tuple[str, float]:
        text_lower = text.lower()
        if "люблю" in text_lower or "обожаю" in text_lower:
            return "нежность", 0.9
        if "ненавижу" in text_lower or "бесит" in text_lower:
            return "раздражение", 0.8
        if "боюсь" in text_lower or "страшно" in text_lower:
            return "страх", 0.7
        if "рад" in text_lower or "счастлив" in text_lower:
            return "радость", 0.6
        if "грустно" in text_lower:
            return "печаль", 0.6
        return "нейтральное", 0.3

    def _extract_and_store_facts(self, sid: str, text: str) -> None:
        for pattern, category in self._FACT_PATTERNS:
            for match in pattern.finditer(text):
                value = " ".join(g for g in match.groups() if g).strip()
                if value and len(value) < 120:
                    emotion, importance = self._extract_emotion_for_fact(text)
                    self._store_fact(sid, category, value, emotion, importance)

    def _store_fact(self, sid: str, category: str, value: str,
                    emotion: str = "нейтральное", importance: float = 0.3) -> None:
        with self._lock:
            with self._db_transaction():
                self._conn.execute(
                    """INSERT OR IGNORE INTO facts
                       (session_id, category, value, emotion, importance)
                       VALUES (?,?,?,?,?)""",
                    (sid, category, value, emotion, importance)
                )
        with self._cache_lock:
            self._facts_cache.pop(sid, None)

    def add_fact(self, sid: str, category: str, value: str,
                 emotion: str = "нейтральное", importance: float = 0.3) -> None:
        self._store_fact(sid, category, value, emotion, importance)

    def get_facts(self, sid: str) -> List[str]:
        with self._cache_lock:
            if sid in self._facts_cache:
                return self._facts_cache[sid]
        
        cur = self._conn.execute(
            "SELECT category, value, emotion FROM facts WHERE session_id=? "
            "ORDER BY importance DESC, id ASC",
            (sid,),
        )
        rows = cur.fetchall()
        result = []
        for r in rows:
            emotion_prefix = f"({r['emotion']}) " if r['emotion'] and r['emotion'] != "нейтральное" else ""
            result.append(f"{emotion_prefix}{r['category']}: {r['value']}")
        
        with self._cache_lock:
            self._facts_cache[sid] = result
        return result

    def save_summary(self, sid: str, content: str, msg_count: int) -> None:
        with self._lock:
            with self._db_transaction():
                self._conn.execute(
                    "INSERT INTO summaries (session_id, content, msg_count) VALUES (?,?,?)",
                    (sid, content, msg_count)
                )

    def get_latest_summary(self, sid: str) -> Optional[str]:
        cur = self._conn.execute(
            "SELECT content FROM summaries WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (sid,),
        )
        row = cur.fetchone()
        return row["content"] if row else None

    def update_session_personality(self, sid: str, personality: str) -> None:
        """Обновляет личность в БД для сессии."""
        with self._lock:
            with self._db_transaction():
                self._conn.execute(
                    "UPDATE sessions SET personality=?, updated_at=datetime('now') WHERE id=?",
                    (personality, sid)
                )
        logger.info(f"Updated session {sid} personality to: {personality}")

    def close(self) -> None:
        if self._conn:
            self._conn.close()


# ╔══════════════════════════════════════════════════════════════╗
# ║                    ===== LLM CLIENT =====                    ║
# ╚══════════════════════════════════════════════════════════════╝

class LLMClient:
    __slots__ = ('_settings', 'url', 'connected', 'model_id', '_connect_lock',
                 '_request_lock', '_last_test_ts', '_last_test_ok', '_last_test_msg',
                 '_session', '_model_cache', '_model_cache_time')
    
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.url: str = settings.get("url", Config.DEFAULT_URL).rstrip("/")
        self.connected: bool = False
        self.model_id: Optional[str] = settings.get("selected_model")
        
        self._connect_lock = threading.RLock()
        self._request_lock = threading.RLock()
        self._last_test_ts: float = 0.0
        self._last_test_ok: bool = False
        self._last_test_msg: str = ""
        self._session: Optional[requests.Session] = None
        self._model_cache: List[str] = []
        self._model_cache_time: float = 0

    @property
    def session(self):
        if self._session is None and REQUESTS_OK:
            self._session = requests.Session()
            retry_strategy = Retry(
                total=Config.MAX_RETRIES,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
            )
            adapter = HTTPAdapter(
                pool_connections=10,
                pool_maxsize=20,
                max_retries=retry_strategy,
                pool_block=False
            )
            self._session.mount('http://', adapter)
            self._session.mount('https://', adapter)
        return self._session

    def set_url(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.connected = False
        self._last_test_ok = False

    def test(self, timeout: int = Config.CONNECTION_TIMEOUT) -> Tuple[bool, str]:
        if not REQUESTS_OK:
            return False, "pip install requests"

        try:
            r = self.session.get(f"{self.url}/models", timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                n = len(data.get("data", data) if isinstance(data, dict) else data)
                self._last_test_ts = time.time()
                self._last_test_ok = True
                self._last_test_msg = f"✅ Подключено ({n} моделей)"
                return True, self._last_test_msg
            self._last_test_ok = False
            return False, f"Статус {r.status_code}"
        except requests.ConnectionError:
            self._last_test_ok = False
            return False, "❌ LM Studio недоступен"
        except requests.Timeout:
            self._last_test_ok = False
            return False, f"❌ Таймаут ({timeout}с)"
        except Exception as exc:
            self._last_test_ok = False
            return False, f"❌ {exc}"

    def test_async(self, callback: Callable[[bool, str], None],
                   timeout: int = Config.CONNECTION_TIMEOUT):
        """Асинхронный тест - callback вызывается в главном потоке"""
        def _worker():
            ok, msg = self.test(timeout)
            import tkinter as tk
            try:
                root = tk._default_root
                if root:
                    root.after(0, lambda: callback(ok, msg))
                else:
                    callback(ok, msg)
            except:
                callback(ok, msg)
        
        threading.Thread(target=_worker, daemon=True).start()

    def connect(self) -> bool:
        with self._connect_lock:
            ok, _ = self.test(timeout=Config.CONNECTION_TIMEOUT)
            self.connected = ok
            return ok

    def ensure_connected(self) -> bool:
        if not self.connected:
            return False
        try:
            r = self.session.get(f"{self.url}/models", timeout=2)
            if r.status_code == 200:
                return True
            self.connected = False
        except Exception:
            self.connected = False
        return False

    @memoize(ttl=Config.CACHE_TTL)
    def get_models(self) -> List[str]:
        try:
            r = self.session.get(f"{self.url}/models", timeout=Config.CONNECTION_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and "data" in data:
                    return [m["id"] for m in data["data"] if "id" in m]
                if isinstance(data, list):
                    return data
            return []
        except Exception as exc:
            logger.error(f"get_models error: {exc}")
            return []

    def load_model(self, model_id: str) -> bool:
        if not self.ensure_connected():
            return False
        models = self.get_models()
        if model_id not in models:
            logger.warning(f"Model '{model_id}' not in list")
        self.model_id = model_id
        self._settings.set("selected_model", model_id)
        logger.info(f"Model {model_id} selected")
        return True

    def scan_folder(self, path: str) -> List[Dict]:
        result: List[Dict] = []
        if not os.path.isdir(path):
            return result
        for fp in glob.glob(os.path.join(path, "**", "*.gguf"), recursive=True):
            name = os.path.basename(fp)
            size = os.path.getsize(fp) / 1_073_741_824
            result.append({
                "id": name,
                "path": fp,
                "display": f"📁 {name[:36]}  ({size:.1f} ГБ)",
            })
        return result

    def respond_stream(
        self,
        history: List[Dict],
        user_message: str,
        system_prompt: str,
        on_token: Callable[[str], None],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
        stop_flag: threading.Event,
    ) -> None:
        def _worker() -> None:
            with self._request_lock:
                self._stream_with_retry(
                    history, user_message, system_prompt,
                    on_token, on_done, on_error, stop_flag,
                )
        threading.Thread(target=_worker, daemon=True).start()

    def _stream_with_retry(
        self,
        history: List[Dict],
        user_message: str,
        system_prompt: str,
        on_token: Callable[[str], None],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
        stop_flag: threading.Event,
    ) -> None:
        last_error = ""
        for attempt in range(Config.MAX_RETRIES + 1):
            if stop_flag.is_set():
                return
            try:
                self._do_stream(
                    history, user_message, system_prompt,
                    on_token, on_done, stop_flag,
                )
                return
            except requests.Timeout:
                last_error = f"❌ Таймаут ({Config.STREAM_TIMEOUT}с)"
            except requests.ConnectionError:
                last_error = "❌ Потеряно соединение с сервером"
                self.connected = False
            except Exception as exc:
                last_error = str(exc)

            if attempt < Config.MAX_RETRIES:
                time.sleep(Config.RETRY_DELAY)
        on_error(last_error)

    def _do_stream(
        self,
        history: List[Dict],
        user_message: str,
        system_prompt: str,
        on_token: Callable[[str], None],
        on_done: Callable[[str], None],
        stop_flag: threading.Event,
    ) -> None:
        messages = [{"role": "system", "content": system_prompt}]
        for m in history[-Config.MAX_HISTORY:]:
            if m["role"] in ("user", "assistant"):
                messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": user_message})

        response = self.session.post(
            f"{self.url}/chat/completions",
            json={
                "model": self.model_id or "local-model",
                "messages": messages,
                "stream": True,
                "temperature": 0.7,
                "max_tokens": 2000,
            },
            stream=True,
            timeout=Config.STREAM_TIMEOUT,
        )

        if response.status_code != 200:
            raise RuntimeError(f"Ошибка сервера: {response.status_code}")

        full = ""
        for raw_line in response.iter_lines():
            if stop_flag.is_set():
                break
            if not raw_line:
                continue
            line = raw_line.decode("utf-8")
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                token = delta.get("content", "")
                if token:
                    full += token
                    on_token(token)
            except (json.JSONDecodeError, IndexError, KeyError):
                continue

        on_done(full)


# ╔══════════════════════════════════════════════════════════════╗
# ║                     ===== GUI LAYER =====                    ║
# ╚══════════════════════════════════════════════════════════════╝

class MessageBubble(ctk.CTkFrame):
    __slots__ = ('text', 'role', 'msg_id', 'app', 'text_lbl', '_buttons')
    
    def __init__(self, master: Any, text: str, role: str, ts: str,
                 msg_id: str, app: "LucyApp", **kw: Any) -> None:
        super().__init__(master, fg_color="transparent", **kw)
        self.text = text
        self.role = role
        self.msg_id = msg_id
        self.app = app
        self.text_lbl: Optional[ctk.CTkLabel] = None
        self._buttons: List[ctk.CTkButton] = []
        self._build(ts)

    def _build(self, ts: str) -> None:
        is_user = self.role == "user"
        is_sys = self.role == "system"

        if is_sys:
            ctk.CTkLabel(
                self, text=self.text,
                font=ctk.CTkFont("Segoe UI", 11, slant="italic"),
                text_color=PALETTE["muted"],
                wraplength=650, justify="center",
            ).pack(padx=20, pady=2)
            return

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="x", padx=8, pady=3)

        bubble = ctk.CTkFrame(
            outer,
            fg_color=PALETTE["user_bubble"] if is_user else PALETTE["ai_bubble"],
            corner_radius=16,
            border_width=1,
            border_color=PALETTE["accent"] if is_user else PALETTE["border"],
        )
        bubble.pack(
            anchor="e" if is_user else "w",
            padx=(60, 0) if is_user else (0, 60),
        )

        header = ctk.CTkFrame(bubble, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(8, 0))

        ctk.CTkLabel(
            header,
            text="Вы" if is_user else "✨ Люси",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=PALETTE["accent"] if is_user else PALETTE["accent2"],
        ).pack(side="left")

        if self.app.settings.get("show_time"):
            ctk.CTkLabel(
                header, text=ts,
                font=ctk.CTkFont("Segoe UI", 9),
                text_color=PALETTE["muted"],
            ).pack(side="right")

        fs = self.app.settings.get("font_size", 13)
        self.text_lbl = ctk.CTkLabel(
            bubble, text=self.text,
            font=ctk.CTkFont("Segoe UI", fs),
            text_color=PALETTE["user_text"] if is_user else PALETTE["ai_text"],
            wraplength=520, justify="left", anchor="w",
        )
        self.text_lbl.pack(padx=14, pady=(4, 6), fill="x")

        actions = ctk.CTkFrame(bubble, fg_color="transparent", height=22)
        actions.pack(fill="x", padx=10, pady=(0, 6))
        
        s = dict(
            width=28, height=22, corner_radius=6,
            fg_color="transparent",
            text_color=PALETTE["muted"],
            hover_color=PALETTE["border"],
            font=ctk.CTkFont("Segoe UI Emoji", 11),
        )
        
        if is_user:
            self._create_button(actions, "📋", self._copy, s)
            self._create_button(actions, "✏️", self._edit, s)
            self._create_button(actions, "🗑", self._delete, s)
        else:
            self._create_button(actions, "📋", self._copy, s)
            self._create_button(actions, "🔄", self._regen, s)
            self._create_button(actions, "🗑", self._delete, s)

    def _create_button(self, parent: Any, text: str, command: Callable,
                       style: Dict) -> ctk.CTkButton:
        btn = ctk.CTkButton(parent, text=text, command=command, **style)
        btn.pack(side="left", padx=1)
        self._buttons.append(btn)
        return btn

    def update_text(self, text: str) -> None:
        try:
            if self.text_lbl and self.text_lbl.winfo_exists():
                self.text = text
                self.text_lbl.configure(text=text)
        except Exception:
            pass

    def _copy(self) -> None:
        clip_copy(self.text, self.app.root)
        self.app.notify("📋 Скопировано")

    def _edit(self) -> None:
        self.app.open_edit_dialog(self.msg_id, self.text)

    def _delete(self) -> None:
        if messagebox.askyesno("Удаление", "Удалить сообщение?",
                               parent=self.app.root):
            self.app.delete_message(self.msg_id)

    def _regen(self) -> None:
        self.app.regenerate(self.msg_id)


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent: "LucyApp") -> None:
        super().__init__(parent.root)
        self.app = parent
        self.settings = parent.settings
        self._custom_dialog: Optional[ctk.CTkToplevel] = None

        self.title("⚙️ Настройки")
        self.geometry("780x720")
        self.minsize(700, 600)
        self.maxsize(1000, 900)
        self.configure(fg_color=PALETTE["surface"])
        self.transient(parent.root)
        self.grab_set()
        self.focus()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()

    @staticmethod
    def _plain(parent: Any) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=0, pady=0)
        return f

    def _build(self) -> None:
        ctk.CTkLabel(
            self, text="⚙️ Настройки",
            font=ctk.CTkFont("Segoe UI", 22, "bold"),
            text_color=PALETTE["accent"],
        ).pack(padx=20, pady=(14, 6))

        self.tabs = ctk.CTkTabview(
            self,
            fg_color=PALETTE["surface2"],
            segmented_button_selected_color=PALETTE["accent"],
            segmented_button_selected_hover_color=PALETTE["accent"],
            segmented_button_unselected_color=PALETTE["surface"],
            segmented_button_unselected_hover_color=PALETTE["surface2"],
        )
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        tabs = ("🔌 Соединение", "🎨 Интерфейс", "🎭 Тема",
                "🧠 Характер", "📁 Модели", "⚡ Быстрые", "ℹ️ О программе")
        for tab in tabs:
            self.tabs.add(tab)

        self._tab_connection(self.tabs.tab("🔌 Соединение"))
        self._tab_interface(self.tabs.tab("🎨 Интерфейс"))
        self._tab_theme(self.tabs.tab("🎭 Тема"))
        self._tab_personality(self.tabs.tab("🧠 Характер"))
        self._tab_models(self.tabs.tab("📁 Модели"))
        self._tab_quick(self.tabs.tab("⚡ Быстрые"))
        self._tab_about(self.tabs.tab("ℹ️ О программе"))

        btn_f = ctk.CTkFrame(self, fg_color="transparent", height=52)
        btn_f.pack(fill="x", padx=14, pady=(0, 12))
        btn_f.pack_propagate(False)
        
        ctk.CTkButton(
            btn_f, text="✖  Отмена", width=120, height=36,
            fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
            font=ctk.CTkFont("Segoe UI", 12), corner_radius=8,
            command=self.destroy,
        ).pack(side="left", padx=4, pady=8)
        
        ctk.CTkButton(
            btn_f, text="💾  Сохранить и закрыть", width=200, height=36,
            fg_color=PALETTE["accent"], hover_color="#8055e0",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), corner_radius=8,
            command=self._save,
        ).pack(side="right", padx=4, pady=8)

    def _tab_connection(self, parent: Any) -> None:
        s = self._plain(parent)
        top = ctk.CTkFrame(s, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 4))

        url_row = ctk.CTkFrame(top, fg_color="transparent")
        url_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(url_row, text="URL LM Studio:",
                     font=ctk.CTkFont("Segoe UI", 12),
                     text_color=PALETTE["text"]).pack(side="left", padx=(0, 8))
        self._url_var = ctk.StringVar(value=self.settings.get("url"))
        ctk.CTkEntry(url_row, textvariable=self._url_var,
                     height=32, font=ctk.CTkFont("Segoe UI", 12)
                     ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(url_row, text="🌐 local", width=80, height=32,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 11),
                      command=lambda: self._url_var.set(Config.LOCAL_URL),
                      ).pack(side="left")

        row2 = ctk.CTkFrame(top, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 6))
        self._auto_var = ctk.BooleanVar(value=self.settings.get("auto_connect"))
        ctk.CTkCheckBox(row2, text="Автоподключение",
                        variable=self._auto_var,
                        checkmark_color=PALETTE["accent"], hover_color=PALETTE["surface2"],
                        font=ctk.CTkFont("Segoe UI", 12),
                        ).pack(side="left", padx=(0, 20))

        ctk.CTkLabel(row2, text="Путь к LM Studio:",
                     font=ctk.CTkFont("Segoe UI", 12),
                     text_color=PALETTE["text"]).pack(side="left", padx=(0, 6))
        self._lmpath_var = ctk.StringVar(value=self.settings.get("lm_path", ""))
        ctk.CTkEntry(row2, textvariable=self._lmpath_var,
                     height=30, font=ctk.CTkFont("Segoe UI", 11)
                     ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(row2, text="📂", width=32, height=30,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      command=self._browse_lm_path).pack(side="left")

        btn_row = ctk.CTkFrame(top, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(btn_row, text="🔌  Проверить соединение", width=190, height=34,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=self._test_conn).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="🚀  Запустить LM Studio", width=190, height=34,
                      fg_color=PALETTE["accent"], hover_color="#8055e0",
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=self.app._launch_lm_studio).pack(side="left")
        self._conn_lbl = ctk.CTkLabel(btn_row, text="",
                                      text_color=PALETTE["muted"],
                                      font=ctk.CTkFont("Segoe UI", 11))
        self._conn_lbl.pack(side="left", padx=10)

        ctk.CTkFrame(s, height=1, fg_color=PALETTE["border"]).pack(fill="x", padx=14, pady=(4, 8))

        hdr = ctk.CTkFrame(s, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(hdr, text="🤖  Выбор модели",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=PALETTE["accent2"]).pack(side="left")
        ctk.CTkButton(hdr, text="🔄 Загрузить список", width=140, height=28,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 11),
                      command=self._refresh_models_list).pack(side="right")

        self._model_cards_frame = ctk.CTkScrollableFrame(
            s, fg_color=PALETTE["surface"],
            border_width=1, border_color=PALETTE["border"],
            corner_radius=8,
        )
        self._model_cards_frame.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        self._model_hint_lbl = ctk.CTkLabel(
            self._model_cards_frame,
            text="🔄  Нажмите «Загрузить список» выше",
            font=ctk.CTkFont("Segoe UI", 12, slant="italic"),
            text_color=PALETTE["muted"],
        )
        self._model_hint_lbl.pack(pady=20)

        sel_box = ctk.CTkFrame(s, fg_color=PALETTE["surface"],
                               corner_radius=8, border_width=1,
                               border_color=PALETTE["accent"], height=46)
        sel_box.pack(fill="x", padx=14, pady=(0, 4))
        sel_box.pack_propagate(False)
        self._selected_model_var = ctk.StringVar(
            value=self.settings.get("selected_model") or "")
        sel_in = ctk.CTkFrame(sel_box, fg_color="transparent")
        sel_in.pack(fill="both", expand=True, padx=10)
        ctk.CTkLabel(sel_in, text="✅",
                     font=ctk.CTkFont("Segoe UI Emoji", 12),
                     text_color=PALETTE["success"]).pack(side="left", padx=(0, 6))
        self._sel_model_lbl = ctk.CTkLabel(
            sel_in,
            text=self.settings.get("selected_model") or "— не выбрана —",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=PALETTE["accent2"], anchor="w",
        )
        self._sel_model_lbl.pack(side="left", fill="x", expand=True)

    def _tab_interface(self, parent: Any) -> None:
        s = self._plain(parent)
        p = dict(padx=16, pady=4)

        ctk.CTkLabel(s, text="🎨 Интерфейс",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=PALETTE["accent"]).pack(anchor="w", padx=16, pady=(12, 8))

        ctk.CTkLabel(s, text="Шрифт сообщений в чате:",
                     font=ctk.CTkFont("Segoe UI", 12),
                     text_color=PALETTE["text"]).pack(anchor="w", **p)
        self._font_var = ctk.IntVar(value=self.settings.get("font_size", 13))
        row1 = ctk.CTkFrame(s, fg_color="transparent")
        row1.pack(fill="x", **p)
        ctk.CTkSlider(row1, from_=9, to=20, variable=self._font_var,
                      progress_color=PALETTE["accent"]
                      ).pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._font_lbl = ctk.CTkLabel(row1, text=str(self._font_var.get()),
                                      width=30, font=ctk.CTkFont("Segoe UI", 12),
                                      text_color=PALETTE["muted"])
        self._font_lbl.pack(side="left")
        self._font_var.trace_add("write",
            lambda *_: self._font_lbl.configure(text=str(self._font_var.get())))

        ctk.CTkFrame(s, fg_color=PALETTE["border"], height=1).pack(fill="x", padx=16, pady=8)

        ctk.CTkLabel(s, text="Шрифт поля ввода:",
                     font=ctk.CTkFont("Segoe UI", 12),
                     text_color=PALETTE["text"]).pack(anchor="w", **p)
        self._input_font_var = ctk.IntVar(value=self.settings.get("input_font_size", 14))
        row2 = ctk.CTkFrame(s, fg_color="transparent")
        row2.pack(fill="x", **p)
        ctk.CTkSlider(row2, from_=9, to=24, variable=self._input_font_var,
                      progress_color=PALETTE["accent2"]
                      ).pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._input_font_lbl = ctk.CTkLabel(row2, text=str(self._input_font_var.get()),
                                            width=30, font=ctk.CTkFont("Segoe UI", 12),
                                            text_color=PALETTE["muted"])
        self._input_font_lbl.pack(side="left")
        self._input_font_var.trace_add("write",
            lambda *_: self._input_font_lbl.configure(
                text=str(self._input_font_var.get())))

        ctk.CTkFrame(s, fg_color=PALETTE["border"], height=1).pack(fill="x", padx=16, pady=8)

        self._time_var = ctk.BooleanVar(value=self.settings.get("show_time", True))
        ctk.CTkCheckBox(s, text="Показывать время сообщений",
                        variable=self._time_var,
                        checkmark_color=PALETTE["accent"],
                        hover_color=PALETTE["surface2"],
                        font=ctk.CTkFont("Segoe UI", 12)).pack(anchor="w", **p)

    def _tab_theme(self, parent: Any) -> None:
        s = self._plain(parent)

        ctk.CTkLabel(s, text="🎭 Цветовая тема",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=PALETTE["accent"]).pack(anchor="w", padx=16, pady=(10, 4))
        ctk.CTkLabel(s,
            text="Тема применяется сразу после нажатия «Сохранить».",
            font=ctk.CTkFont("Segoe UI", 10, slant="italic"),
            text_color=PALETTE["muted"]).pack(anchor="w", padx=16, pady=(0, 6))

        self._theme_var = ctk.StringVar(
            value=self.settings.get("theme", "Фиолет (по умолчанию)")
        )

        _CARD_DATA = {
            "Фиолет (по умолчанию)": ("#a277ff", "#0f0f17", "💜", "Тёмный фиолет"),
            "Розовый закат": ("#ff6eb4", "#13090e", "🌸", "Тёмно-розовый"),
            "Океан": ("#00b4d8", "#060d14", "🌊", "Тёмный синий"),
            "Лес": ("#52d65a", "#080f08", "🌿", "Тёмно-зелёный"),
            "Светлая": ("#7c4dff", "#f0f0f5", "☀️", "Светлая"),
            "Серая": ("#aaaacc", "#111114", "🌫", "Нейтральная"),
            "Закат": ("#ff8c42", "#140a00", "🌅", "Тёплый закат"),
            "Полночь": ("#6688ff", "#06061a", "🌌", "Тёмно-синяя"),
        }

        COLS = 2
        grid = ctk.CTkFrame(s, fg_color="transparent")
        grid.pack(padx=12, pady=2, fill="both", expand=True)
        for c in range(COLS):
            grid.columnconfigure(c, weight=1, uniform="tc")

        self._theme_cards: Dict[str, Any] = {}

        for i, name in enumerate(THEMES.keys()):
            acc, bg, emoji, desc = _CARD_DATA.get(name, ("#888", "#222", "●", ""))
            is_sel = (name == self._theme_var.get())

            card = ctk.CTkFrame(grid, fg_color=bg, corner_radius=10,
                                border_width=3,
                                border_color=acc if is_sel else PALETTE["border"])
            card.grid(row=i // COLS, column=i % COLS, padx=4, pady=4, sticky="ew")

            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=8, pady=6)

            circle = ctk.CTkFrame(inner, fg_color=acc, corner_radius=18,
                                  width=36, height=36)
            circle.pack(side="left", padx=(0, 8))
            circle.pack_propagate(False)
            ctk.CTkLabel(circle, text=emoji,
                         font=ctk.CTkFont("Segoe UI Emoji", 16),
                         fg_color="transparent",
                         text_color="white").place(relx=.5, rely=.5, anchor="center")

            tc = ctk.CTkFrame(inner, fg_color="transparent")
            tc.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(tc, text=name,
                         font=ctk.CTkFont("Segoe UI", 11, "bold"),
                         text_color=acc, anchor="w").pack(anchor="w")
            ctk.CTkLabel(tc, text=desc,
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color="#888888", anchor="w").pack(anchor="w")

            sw_col = ctk.CTkFrame(inner, fg_color="transparent")
            sw_col.pack(side="right", padx=(4, 0))
            for ck in ("user_bubble", "ai_bubble", "accent"):
                sw = ctk.CTkFrame(sw_col, fg_color=THEMES[name].get(ck, acc),
                                  corner_radius=2, width=10, height=10)
                sw.pack(pady=1)
                sw.pack_propagate(False)

            self._theme_cards[name] = (card, acc)

            def on_sel(n=name, c=card, a=acc) -> None:
                self._theme_var.set(n)
                for nm, (ch, ca) in self._theme_cards.items():
                    ch.configure(border_color=ca if nm == n else PALETTE["border"])

            def _bind(w, fn=on_sel) -> None:
                w.bind("<Button-1>", lambda e, f=fn: f())
                for ch in w.winfo_children():
                    _bind(ch, fn)
            _bind(card)

        cur_f = ctk.CTkFrame(s, fg_color=PALETTE["surface2"], corner_radius=6, height=28)
        cur_f.pack(fill="x", padx=12, pady=(4, 2))
        cur_f.pack_propagate(False)
        ctk.CTkLabel(cur_f, text="Выбрано:",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=PALETTE["muted"]).pack(side="left", padx=10)
        self._cur_theme_lbl = ctk.CTkLabel(cur_f, text=f"➤ {self._theme_var.get()}",
                                            font=ctk.CTkFont("Segoe UI", 10, "bold"),
                                            text_color=PALETTE["accent"])
        self._cur_theme_lbl.pack(side="left")
        self._theme_var.trace_add("write",
            lambda *_: self._cur_theme_lbl.configure(text=f"➤ {self._theme_var.get()}"))

    def _tab_personality(self, parent: Any) -> None:
        s = self._plain(parent)

        ctk.CTkLabel(s, text="🧠 Характер Люси",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=PALETTE["accent"]).pack(anchor="w", padx=16, pady=(10, 4))

        self._personality_var = ctk.StringVar(
            value=self.app.state.current_personality
        )

        PCOLS = 2
        pf = ctk.CTkFrame(s, fg_color="transparent")
        pf.pack(padx=12, pady=2, fill="both", expand=True)
        for c in range(PCOLS):
            pf.columnconfigure(c, weight=1, uniform="pc")

        self._pers_cards: Dict[str, Any] = {}

        for i, (key, preset) in enumerate(PERSONALITY_PRESETS.items()):
            acc = preset.get("color", PALETTE["accent"])
            is_s = (key == self._personality_var.get())

            card = ctk.CTkFrame(pf, fg_color=PALETTE["surface"], corner_radius=10,
                                border_width=3,
                                border_color=acc if is_s else PALETTE["border"])
            card.grid(row=i // PCOLS, column=i % PCOLS, padx=4, pady=4, sticky="ew")

            inn = ctk.CTkFrame(card, fg_color="transparent")
            inn.pack(fill="x", padx=8, pady=6)

            circle = ctk.CTkFrame(inn, fg_color=acc, corner_radius=18,
                                  width=36, height=36)
            circle.pack(side="left", padx=(0, 8))
            circle.pack_propagate(False)
            ctk.CTkLabel(circle, text=preset["icon"],
                         font=ctk.CTkFont("Segoe UI Emoji", 18),
                         fg_color="transparent",
                         text_color="white").place(relx=.5, rely=.5, anchor="center")

            tc = ctk.CTkFrame(inn, fg_color="transparent")
            tc.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(tc, text=preset["name"],
                         font=ctk.CTkFont("Segoe UI", 11, "bold"),
                         text_color=acc, anchor="w").pack(anchor="w")
            ctk.CTkLabel(tc, text=preset["description"],
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color=PALETTE["muted"], anchor="w").pack(anchor="w")

            self._pers_cards[key] = (card, acc)

            def on_p(k=key, c=card, a=acc) -> None:
                self._personality_var.set(k)
                for kk, (ch, ca) in self._pers_cards.items():
                    ch.configure(border_color=ca if kk == k else PALETTE["border"])
                if k == "Кастомная":
                    self._open_custom_prompt_dialog()
                else:
                    if hasattr(self, '_custom_dialog') and self._custom_dialog:
                        self._custom_dialog.destroy()

            def _bind(w, fn=on_p) -> None:
                w.bind("<Button-1>", lambda e, f=fn: f())
                for ch in w.winfo_children():
                    _bind(ch, fn)
            _bind(card)

    def _open_custom_prompt_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("✏️ Свой системный промпт")
        dialog.geometry("600x400")
        dialog.configure(fg_color=PALETTE["surface"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.focus()
        
        self._custom_dialog = dialog
        
        ctk.CTkLabel(
            dialog,
            text="Введите свой системный промпт:",
            font=ctk.CTkFont("Segoe UI", 14, "bold"),
            text_color=PALETTE["accent"]
        ).pack(pady=(20, 10), padx=20)
        
        self._custom_prompt = ctk.CTkTextbox(
            dialog,
            height=200,
            fg_color=PALETTE["surface2"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont("Segoe UI", 12),
            border_width=1,
            border_color=PALETTE["border"],
            corner_radius=8
        )
        self._custom_prompt.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        
        saved = self.settings.get("custom_prompt", "")
        if saved:
            self._custom_prompt.insert("1.0", saved)
        
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 20))
        
        ctk.CTkButton(
            btn_frame,
            text="✅ Сохранить",
            width=120,
            height=36,
            fg_color=PALETTE["accent"],
            hover_color="#8055e0",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            command=lambda: self._save_custom_prompt(dialog)
        ).pack(side="left", padx=5)
        
        ctk.CTkButton(
            btn_frame,
            text="✖ Отмена",
            width=120,
            height=36,
            fg_color=PALETTE["surface2"],
            hover_color=PALETTE["border"],
            font=ctk.CTkFont("Segoe UI", 12),
            command=dialog.destroy
        ).pack(side="left", padx=5)
    
    def _save_custom_prompt(self, dialog):
        prompt = self._custom_prompt.get("1.0", "end").strip()
        self.settings.set("custom_prompt", prompt)
        dialog.destroy()
        self.app.notify("✅ Кастомный промпт сохранен")

    def _tab_models(self, parent: Any) -> None:
        s = self._plain(parent)
        ctk.CTkLabel(s, text="📁 Папки с GGUF моделями",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=PALETTE["accent"]).pack(anchor="w", padx=16, pady=(10, 6))

        self._paths_box = ctk.CTkTextbox(s,
                                         fg_color=PALETTE["surface"],
                                         text_color=PALETTE["text"],
                                         font=ctk.CTkFont("Segoe UI", 11))
        self._paths_box.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        for p in self.settings.get("model_paths", []):
            self._paths_box.insert("end", p + "\n")

        bf = ctk.CTkFrame(s, fg_color="transparent", height=44)
        bf.pack(fill="x", padx=16)
        bf.pack_propagate(False)
        ctk.CTkButton(bf, text="📁  Добавить папку", width=150, height=34,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 12),
                      command=self._add_path).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bf, text="🗑  Очистить", width=110, height=34,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 12),
                      command=lambda: self._paths_box.delete("1.0", "end")).pack(side="left")

    def _add_path(self) -> None:
        folder = filedialog.askdirectory(title="Папка с моделями", parent=self)
        if folder:
            self._paths_box.insert("end", folder + "\n")

    def _tab_quick(self, parent: Any) -> None:
        s = self._plain(parent)
        ctk.CTkLabel(s, text="⚡ Быстрые ответы",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=PALETTE["accent"]).pack(anchor="w", padx=16, pady=(10, 4))
        ctk.CTkLabel(s, text="По одному в строке — двойной клик в сайдбаре для отправки",
                     font=ctk.CTkFont("Segoe UI", 10, slant="italic"),
                     text_color=PALETTE["muted"]).pack(anchor="w", padx=16, pady=(0, 6))

        self._quick_box = ctk.CTkTextbox(s,
                                         fg_color=PALETTE["surface"],
                                         text_color=PALETTE["text"],
                                         font=ctk.CTkFont("Segoe UI", 12))
        self._quick_box.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        for q in self.settings.get("quick_replies", []):
            self._quick_box.insert("end", q + "\n")

        bf = ctk.CTkFrame(s, fg_color="transparent", height=44)
        bf.pack(fill="x", padx=16)
        bf.pack_propagate(False)
        ctk.CTkButton(bf, text="➕  Добавить", width=110, height=34,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 12),
                      command=self._add_quick).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bf, text="🗑  Очистить", width=110, height=34,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 12),
                      command=lambda: self._quick_box.delete("1.0", "end")).pack(side="left")

    def _add_quick(self) -> None:
        dlg = ctk.CTkInputDialog(title="Новый быстрый ответ", text="Введите текст:")
        text = dlg.get_input()
        if text:
            self._quick_box.insert("end", text + "\n")

    def _tab_about(self, parent: Any) -> None:
        s = self._plain(parent)

        top = ctk.CTkFrame(s, fg_color=PALETTE["surface2"], corner_radius=10)
        top.pack(fill="x", padx=14, pady=(12, 8))
        top_in = ctk.CTkFrame(top, fg_color="transparent")
        top_in.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(top_in, text="✨",
                     font=ctk.CTkFont("Segoe UI Emoji", 32),
                     text_color=PALETTE["accent"]).pack(side="left", padx=(0, 12))

        info_col = ctk.CTkFrame(top_in, fg_color="transparent")
        info_col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(info_col, text=f"{Config.APP_TITLE} v{Config.APP_VERSION}",
                     font=ctk.CTkFont("Segoe UI", 18, "bold"),
                     text_color=PALETTE["accent"], anchor="w").pack(anchor="w")
        ctk.CTkLabel(info_col,
                     text=f"👨‍💻 {Config.DEVELOPER}  •  🕐 {PersonalityEngine.get_current_datetime_str()}",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=PALETTE["muted"], anchor="w").pack(anchor="w")

        def copy_link() -> None:
            clip_copy(Config.DEVELOPER_LINK, self.app.root)
            self.app.notify("🔗 Ссылка скопирована!")

        gh = ctk.CTkLabel(top_in, text="🔗 GitHub",
                          font=ctk.CTkFont("Segoe UI", 11, "bold"),
                          text_color=PALETTE["accent2"], cursor="hand2")
        gh.pack(side="right", padx=(8, 0))
        gh.bind("<Button-1>", lambda e: copy_link())

        info_rows = [
            ("🔧 Стек", "Python 3, customtkinter, SQLite"),
            ("🌐 API", "LM Studio / OpenAI-совместимые"),
            ("🧠 Память", "Short-term + Long-term (факты с эмоциями)"),
            ("❤️ Эмоции", "Mood, Affection, Arousal, Energy"),
            ("🕐 Время", "Знает день, ночь, месяц, год"),
            ("📁 Данные", f"{Config.SETTINGS_FILE}  |  {Config.DB_FILE}"),
        ]
        grid_f = ctk.CTkFrame(s, fg_color="transparent")
        grid_f.pack(fill="x", padx=14, pady=4)
        grid_f.columnconfigure(1, weight=1)
        for i, (lbl, val) in enumerate(info_rows):
            ctk.CTkLabel(grid_f, text=lbl,
                         font=ctk.CTkFont("Segoe UI", 11, "bold"),
                         text_color=PALETTE["accent"], anchor="w",
                         width=100).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            ctk.CTkLabel(grid_f, text=val,
                         font=ctk.CTkFont("Segoe UI", 11),
                         text_color=PALETTE["text"], anchor="w"
                         ).grid(row=i, column=1, sticky="w", pady=2)

        ctk.CTkFrame(s, fg_color=PALETTE["border"], height=1).pack(fill="x", padx=14, pady=8)
        ctk.CTkLabel(s, text="💜 Сделано с любовью · Люси AI · by kahsGames",
                     font=ctk.CTkFont("Segoe UI", 11, slant="italic"),
                     text_color=PALETTE["muted"]).pack()
        ctk.CTkButton(s, text="💝 GitHub — поставить звёздочку",
                      width=260, height=34,
                      fg_color=PALETTE["accent"], hover_color="#8055e0",
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=copy_link).pack(pady=(8, 0))

    def _refresh_models_list(self) -> None:
        for w in self._model_cards_frame.winfo_children():
            w.destroy()
        ctk.CTkLabel(
            self._model_cards_frame,
            text="⏳  Загружаю список моделей...",
            font=ctk.CTkFont("Segoe UI", 12, slant="italic"),
            text_color=PALETTE["muted"],
        ).pack(pady=30)

        def _do() -> None:
            models: List[str] = []
            if self.app.lm.ensure_connected():
                models = self.app.lm.get_models()
            for path in self.settings.get("model_paths", []):
                for m in self.app.lm.scan_folder(path):
                    if m["id"] not in models:
                        models.append(m["id"])
            self.after(0, lambda: self._render_model_cards(models))

        threading.Thread(target=_do, daemon=True).start()

    def _render_model_cards(self, models: List[str]) -> None:
        for w in self._model_cards_frame.winfo_children():
            w.destroy()

        if not models:
            ctk.CTkLabel(
                self._model_cards_frame,
                text="❌  Модели не найдены. Подключитесь к LM Studio.",
                font=ctk.CTkFont("Segoe UI", 12),
                text_color=PALETTE["error"],
            ).pack(pady=30)
            return

        selected_now = self._selected_model_var.get()
        self._model_card_widgets: Dict[str, Any] = {}

        for mid in models:
            is_sel = (mid == selected_now)
            short = mid if len(mid) <= 54 else mid[:51] + "…"

            row = ctk.CTkFrame(
                self._model_cards_frame,
                fg_color=PALETTE["surface2"] if not is_sel else PALETTE["user_bubble"],
                corner_radius=8,
                border_width=2,
                border_color=PALETTE["accent"] if is_sel else PALETTE["surface2"],
            )
            row.pack(fill="x", padx=6, pady=3)

            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=10, pady=6)

            ext = mid.rsplit(".", 1)[-1].lower() if "." in mid else ""
            icon = "📦" if ext == "gguf" else "🔷"
            ctk.CTkLabel(inner, text=icon,
                         font=ctk.CTkFont("Segoe UI Emoji", 16),
                         fg_color="transparent",
                         text_color=PALETTE["accent"]).pack(side="left", padx=(0, 8))

            nl = ctk.CTkLabel(inner, text=short,
                               font=ctk.CTkFont("Segoe UI", 11,
                                                "bold" if is_sel else "normal"),
                               text_color=PALETTE["accent2"] if is_sel else PALETTE["text"],
                               anchor="w")
            nl.pack(side="left", fill="x", expand=True)

            if is_sel:
                ctk.CTkLabel(inner, text="✔",
                             font=ctk.CTkFont("Segoe UI", 13, "bold"),
                             fg_color="transparent",
                             text_color=PALETTE["success"]).pack(side="right", padx=4)

            self._model_card_widgets[mid] = row

            def _on_click(m=mid, r=row) -> None:
                for mm, rr in self._model_card_widgets.items():
                    rr.configure(
                        fg_color=PALETTE["user_bubble"] if mm == m else PALETTE["surface2"],
                        border_color=PALETTE["accent"] if mm == m else PALETTE["surface2"],
                    )
                self._selected_model_var.set(m)
                self._sel_model_lbl.configure(text=m)

            row.bind("<Button-1>", lambda e, f=_on_click: f())
            for w in row.winfo_children():
                for ww in ([w] + list(w.winfo_children())):
                    ww.bind("<Button-1>", lambda e, f=_on_click: f())

    def _browse_lm_path(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбрать исполняемый файл LM Studio",
            filetypes=[("Исполняемые", "*.exe *.app *"), ("Все", "*.*")],
            parent=self,
        )
        if path:
            self._lmpath_var.set(path)

    def _test_conn(self) -> None:
        self._conn_lbl.configure(text="⏳ Проверка...", text_color=PALETTE["warning"])
        url = self._url_var.get().strip()
        old_url = self.app.lm.url
        self.app.lm.set_url(url)
        
        def _check():
            ok, msg = self.app.lm.test(timeout=3)
            color = PALETTE["success"] if ok else PALETTE["error"]
            self.after(0, lambda: self._conn_lbl.configure(text=msg, text_color=color))
            if ok:
                self.after(0, self.app.lm.connect)
                self.after(0, self._refresh_models_list)
            else:
                self.after(0, lambda: self.app.lm.set_url(old_url))
        
        threading.Thread(target=_check, daemon=True).start()

    def _save(self) -> None:
        old_url = self.settings.get("url")
        new_url = self._url_var.get().strip()

        self.settings.add_url_to_history(new_url)
        self.settings.set("url", new_url)
        self.settings.set("auto_connect", self._auto_var.get())
        self.settings.set("font_size", self._font_var.get())
        self.settings.set("input_font_size", self._input_font_var.get())
        
        try:
            self.app._input.configure(
                font=ctk.CTkFont("Segoe UI", self._input_font_var.get())
            )
        except Exception:
            pass
        
        self.settings.set("show_time", self._time_var.get())
        self.settings.set("lm_path", self._lmpath_var.get().strip())
        prev_theme = self.settings.get("theme", "Фиолет (по умолчанию)")
        self.settings.set("theme", self._theme_var.get())
        
        # Новая логика смены личности
        new_personality = self._personality_var.get()
        old_personality = self.app.state.current_personality
        
        if new_personality != old_personality:
            self.app.state.current_personality = new_personality
            self.settings.set("personality", new_personality)
            self.app.personality_engine.invalidate_cache()
            
            if self.app.state.session_id:
                self.app.memory.update_session_personality(self.app.state.session_id, new_personality)
            
            logger.info(f"Personality changed: {old_personality} -> {new_personality}")
            self.app.notify(f"🧠 Характер изменён на: {new_personality}")

        paths = [p.strip() for p in self._paths_box.get("1.0", "end").splitlines() if p.strip()]
        self.settings.set("model_paths", paths)
        quick = [q.strip() for q in self._quick_box.get("1.0", "end").splitlines() if q.strip()]
        self.settings.set("quick_replies", quick)

        sel = self._selected_model_var.get()
        if sel and sel not in ("Не выбрана", ""):
            self.settings.set("selected_model", sel)
            self.app.lm.model_id = sel
            self.app.state.model_id = sel

        self.app.lm.set_url(new_url)
        if old_url != new_url:
            self.app.lm.connected = False

        if self._auto_var.get():
            self.app._connect_lm()
        else:
            self.app._set_status("⚪ Не подключено", PALETTE["muted"])

        self.app._reload_quick_replies()
        self.app.personality_engine = PersonalityEngine(self.settings)

        new_theme = self._theme_var.get()
        theme_changed = (new_theme != prev_theme)

        self.destroy()

        if theme_changed:
            self.app._apply_theme_live(new_theme)
        else:
            self.app.notify("⚙️ Настройки сохранены")


class LucyApp:
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        _early = Settings()
        apply_theme(_early.get("theme", "Фиолет (по умолчанию)"))

        self.root = ctk.CTk()
        self.root.title(
            f"✨  {Config.APP_TITLE} v{Config.APP_VERSION}  |  Разработчик: {Config.DEVELOPER}"
        )
        self.root.geometry("1400x800")
        self.root.minsize(1200, 700)
        self.root.configure(fg_color=PALETTE["bg"])

        self.settings = Settings()
        self.memory = MemoryManager()
        self.lm = LLMClient(self.settings)
        self.personality_engine = PersonalityEngine(self.settings)
        self.behavior = BehavioralEngine(self)
        self.performance = PerformanceMonitor()
        
        # Единый источник правды для личности
        initial_personality = self.settings.get("personality", "Люси (по умолчанию)")
        self.state = AppState(
            current_personality=initial_personality,
            model_id=self.settings.get("selected_model"),
        )
        
        self.state.emotion = EmotionalState(
            mood=random.randint(50, 70),
            affection=random.randint(30, 50),
            arousal=random.randint(10, 30),
            energy=random.randint(50, 80)
        )
        self.state.last_user_activity = time.time()

        self._stop_flag: threading.Event = threading.Event()
        self._stream_bubble: Optional[MessageBubble] = None
        self._stream_text: str = ""
        self.messages: Dict[str, MessageBubble] = {}
        self.models: List[Dict] = []
        
        self._message_cache: Dict[str, MessageBubble] = {}
        self._scroll_pos = 0
        self._last_gc = time.time()
        self._scroll_timer: Optional[threading.Timer] = None

        self._build_ui()
        self._post_init()
        self._start_gc_monitor()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        logger.info(f"Initialized with personality: {initial_personality}")

    def _start_gc_monitor(self):
        def gc_loop():
            while True:
                time.sleep(Config.GC_INTERVAL)
                if time.time() - self._last_gc > Config.GC_INTERVAL:
                    with self.performance.measure("garbage_collection"):
                        gc.collect()
                        self._last_gc = time.time()
        
        threading.Thread(target=gc_loop, daemon=True).start()

    def _build_ui(self) -> None:
        header = ctk.CTkFrame(self.root, fg_color=PALETTE["surface"],
                              height=62, corner_radius=0)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        left_h = ctk.CTkFrame(header, fg_color="transparent")
        left_h.pack(side="left", padx=16, fill="y")
        ctk.CTkLabel(left_h, text="✨  Люси",
                     font=ctk.CTkFont("Segoe UI", 20, "bold"),
                     text_color=PALETTE["accent"]).pack(side="left", padx=(0, 12))

        status_box = ctk.CTkFrame(left_h, fg_color=PALETTE["surface2"],
                                   corner_radius=8)
        status_box.pack(side="left", pady=14)
        self._status_lbl = ctk.CTkLabel(
            status_box, text="⏳ Подключение...",
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=PALETTE["warning"],
            padx=10, pady=4,
        )
        self._status_lbl.pack()

        lm_f = ctk.CTkFrame(header, fg_color="transparent")
        lm_f.pack(side="left", padx=8, fill="y")
        btn_inner = ctk.CTkFrame(lm_f, fg_color="transparent")
        btn_inner.pack(expand=True)
        ctk.CTkButton(btn_inner, text="🔌  Подключить", width=140, height=34,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      text_color=PALETTE["accent2"],
                      command=self._connect_lm).pack(side="left", padx=4)
        ctk.CTkButton(btn_inner, text="🚀  Запустить LM Studio", width=190, height=34,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 11),
                      command=self._launch_lm_studio).pack(side="left", padx=4)

        right_h = ctk.CTkFrame(header, fg_color="transparent")
        right_h.pack(side="right", padx=16, fill="y")
        model_f = ctk.CTkFrame(right_h, fg_color="transparent")
        model_f.pack(expand=True)
        self._model_var = ctk.StringVar(value="Выберите модель")
        self._model_combo = ctk.CTkComboBox(
            model_f, variable=self._model_var, values=[], width=280, height=34,
            fg_color=PALETTE["surface2"], border_color=PALETTE["border"],
            button_color=PALETTE["accent"], dropdown_fg_color=PALETTE["surface2"],
            font=ctk.CTkFont("Segoe UI", 11),
        )
        self._model_combo.pack(side="left", padx=(0, 6))
        ctk.CTkButton(model_f, text="📥  Загрузить", width=110, height=34,
                      fg_color=PALETTE["accent"], hover_color="#8055e0",
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      command=self._load_model).pack(side="left", padx=3)
        ctk.CTkButton(model_f, text="🔄", width=34, height=34,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 13),
                      command=self._refresh_models).pack(side="left", padx=3)
        ctk.CTkButton(model_f, text=" 🔧 ", width=50, height=50,
                      fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                      font=ctk.CTkFont("Segoe UI", 20),
                      corner_radius=25,
                      command=self._open_settings).pack(side="left", padx=3)

        body = ctk.CTkFrame(self.root, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=(6, 6))

        sidebar = ctk.CTkFrame(body, fg_color=PALETTE["surface"],
                               width=250, corner_radius=12)
        sidebar.pack(side="left", fill="y", padx=(0, 10))
        sidebar.pack_propagate(False)
        ctk.CTkButton(sidebar, text="➕  Новый чат", height=38,
                      fg_color=PALETTE["accent"], hover_color="#8055e0",
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      corner_radius=8,
                      command=self._new_session).pack(padx=12, pady=(12, 8), fill="x")

        tabs_sb = ctk.CTkTabview(
            sidebar, fg_color=PALETTE["surface2"],
            segmented_button_selected_color=PALETTE["accent"],
            segmented_button_unselected_color=PALETTE["surface"],
            height=500,
        )
        tabs_sb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        for tab in ("💬", "⭐", "⚡"):
            tabs_sb.add(tab)

        self._sessions_frame = ctk.CTkScrollableFrame(
            tabs_sb.tab("💬"), fg_color="transparent"
        )
        self._sessions_frame.pack(fill="both", expand=True)
        self._fav_frame = ctk.CTkScrollableFrame(
            tabs_sb.tab("⭐"), fg_color="transparent"
        )
        self._fav_frame.pack(fill="both", expand=True)
        self._quick_frame = ctk.CTkScrollableFrame(
            tabs_sb.tab("⚡"), fg_color="transparent"
        )
        self._quick_frame.pack(fill="both", expand=True)

        center = ctk.CTkFrame(body, fg_color=PALETTE["surface"], corner_radius=12)
        center.pack(side="left", fill="both", expand=True)

        input_bar = ctk.CTkFrame(center, fg_color=PALETTE["surface2"], corner_radius=0)
        input_bar.pack(side="bottom", fill="x")
        _input_fs = self.settings.get("input_font_size", 14)
        self._input = ctk.CTkTextbox(
            input_bar, height=70,
            fg_color=PALETTE["surface"], text_color=PALETTE["text"],
            font=ctk.CTkFont("Segoe UI", _input_fs),
            border_width=1, border_color=PALETTE["border"],
            corner_radius=10, wrap="word",
        )
        self._input.pack(side="left", fill="x", expand=True, padx=(12, 6), pady=10)
        self._input.bind("<Return>", self._on_enter)
        self._input.bind("<Shift-Return>", lambda e: None)
        self._input.bind("<Control-a>", lambda e: (
            self._input.tag_add("sel", "1.0", "end"), "break"
        ))
        self._input.bind("<Button-3>", self._show_input_context_menu)
        
        ctk.CTkButton(
            input_bar, text="➤", width=58, height=52,
            fg_color=PALETTE["accent"], hover_color="#8055e0",
            corner_radius=10, font=ctk.CTkFont("Segoe UI", 18, "bold"),
            command=self._send,
        ).pack(side="right", padx=(0, 10), pady=8)

        quick_bar = ctk.CTkFrame(center, fg_color=PALETTE["surface2"],
                                  height=44, corner_radius=0)
        quick_bar.pack(side="bottom", fill="x")
        quick_bar.pack_propagate(False)
        quick_defs = [
            ("✏️", self._edit_last, "Изменить последнее"),
            ("🔄", self._regen_last, "Перегенерировать"),
            ("📋", self._copy_last, "Копировать ответ"),
            ("💾", self._export_chat, "Экспорт чата"),
            ("🔍", self._search_dialog, "Поиск"),
            ("📎", self._attach_file, "Прикрепить файл"),
            ("⏹", self._stop_generation, "Остановить"),
        ]
        for icon, cmd, tip in quick_defs:
            b = ctk.CTkButton(
                quick_bar, text=icon, width=38, height=32,
                fg_color="transparent", text_color=PALETTE["muted"],
                hover_color=PALETTE["border"], command=cmd,
                font=ctk.CTkFont("Segoe UI Emoji", 15),
            )
            b.pack(side="left", padx=2, pady=4)
            make_tooltip(b, tip, self.root)

        self._typing_lbl = ctk.CTkLabel(
            center, text="",
            font=ctk.CTkFont("Segoe UI", 11, slant="italic"),
            text_color=PALETTE["accent2"],
        )
        self._typing_lbl.pack(side="bottom", anchor="w", padx=20, pady=(2, 0))

        self._chat_frame = ctk.CTkScrollableFrame(
            center, fg_color=PALETTE["bg"],
            scrollbar_fg_color=PALETTE["surface2"],
            scrollbar_button_color=PALETTE["border"],
            corner_radius=0,
        )
        self._chat_frame.pack(fill="both", expand=True)

    def _post_init(self) -> None:
        self._reload_quick_replies()
        self._reload_sessions()
        self._reload_favorites()

        self.behavior.start_initiative_checker(interval_sec=45)

        greeting = PersonalityEngine.get_greeting()
        self._add_sys(f"{greeting}! 👋 Я Люси. Загрузи модель и начинай общаться!")

        if self.settings.get("auto_connect"):
            self.root.after(400, self._connect_lm)
        else:
            self._set_status("⚪ Не подключено", PALETTE["muted"])

        last = self.settings.get("last_session")
        if last:
            if any(s["id"] == last for s in self.memory.get_sessions()):
                self.state.session_id = last
                self._load_history()

    def _reload_sessions(self) -> None:
        for w in self._sessions_frame.winfo_children():
            w.destroy()
        for s in self.memory.get_sessions():
            name = s["name"][:22] + ("…" if len(s["name"]) > 22 else "")
            row = ctk.CTkFrame(self._sessions_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkButton(
                row,
                text=f"{name}  ({s['cnt']})",
                anchor="w", height=30,
                fg_color=PALETTE["accent"] if s["id"] == self.state.session_id
                         else PALETTE["surface2"],
                hover_color=PALETTE["border"],
                font=ctk.CTkFont("Segoe UI", 10),
                corner_radius=6,
                command=lambda sid=s["id"]: self._switch_session(sid),
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                row, text="×", width=24, height=30,
                fg_color="transparent", hover_color=PALETTE["error"],
                text_color=PALETTE["muted"],
                font=ctk.CTkFont("Segoe UI", 12, "bold"),
                corner_radius=6,
                command=lambda sid=s["id"]: self._delete_session(sid),
            ).pack(side="right")

    def _new_session(self) -> None:
        name = f"Чат {datetime.now().strftime('%d.%m %H:%M')}"
        # Используем personality из state
        personality = self.state.current_personality
        self.state.session_id = self.memory.create_session(
            name, self.lm.model_id, personality
        )
        self.settings.set("last_session", self.state.session_id)
        self._clear_chat()
        greeting = PersonalityEngine.get_greeting()
        self._add_sys(f"{greeting}! ✨ Новый чат создан!")
        self._reload_sessions()
        logger.info(f"New session with personality: {personality}")

    def _switch_session(self, sid: str) -> None:
        """Исправленное переключение сессии - полный сброс состояния"""
        logger.info(f"Switching to session: {sid}")
        
        # 1. Обновляем ID сессии
        self.state.session_id = sid
        self.settings.set("last_session", sid)
        
        # 2. Загружаем personality из БД и обновляем state
        personality = self.memory.get_session_personality(sid)
        self.state.current_personality = personality
        logger.info(f"Loaded personality from DB: {personality}")
        
        # 3. Инвалидируем кэш промптов
        self.personality_engine.invalidate_cache()
        
        # 4. Сбрасываем эмоции в дефолтное состояние
        self.state.emotion = EmotionalState(
            mood=random.randint(50, 70),
            affection=random.randint(30, 50),
            arousal=random.randint(10, 30),
            energy=random.randint(50, 80)
        )
        logger.info("Emotions reset to default")
        
        # 5. Обновляем время последней активности
        self.state.last_user_activity = time.time()
        
        # 6. Очищаем чат и загружаем историю
        self._clear_chat()
        self._load_history()
        
        # 7. Обновляем список сессий в UI
        self._reload_sessions()
        
        logger.info(f"Session switched successfully, current personality: {self.state.current_personality}")

    def _delete_session(self, sid: str) -> None:
        if not messagebox.askyesno("Удалить чат?", "Это действие необратимо.",
                                    parent=self.root):
            return
        self.memory.delete_session(sid)
        if sid == self.state.session_id:
            self.state.session_id = None
            self._clear_chat()
            self._add_sys("👋 Создай новый чат.")
        self._reload_sessions()

    def _clear_chat(self) -> None:
        """Полная очистка чата - удаляем все виджеты и кэши"""
        # Удаляем все виджеты из чата
        for w in self._chat_frame.winfo_children():
            w.destroy()
        
        # Очищаем словари сообщений
        self.messages.clear()
        self._message_cache.clear()
        
        # Сбрасываем потоковую переменную
        self._stream_text = ""
        self._stream_bubble = None
        
        logger.debug("Chat cleared completely")

    def _load_history(self) -> None:
        if not self.state.session_id:
            return
        with self.performance.measure("load_history"):
            for m in self.memory.load_history(self.state.session_id, limit=100):
                ts = m.get("ts", "")
                ts = ts[11:16] if ts and len(ts) > 5 else ts
                self._insert_bubble(m["content"], m["role"], ts)

    def _insert_bubble(self, text: str, role: str, ts: Optional[str] = None) -> MessageBubble:
        if ts is None:
            ts = datetime.now().strftime("%H:%M")
        msg_id = str(uuid.uuid4())
        bubble = MessageBubble(self._chat_frame, text, role, ts, msg_id, self)
        bubble.pack(fill="x", pady=2)
        self.messages[msg_id] = bubble
        self._message_cache[msg_id] = bubble
        self._scroll_bottom()
        return bubble

    def _add_sys(self, text: str) -> None:
        self._insert_bubble(text, "system")
        if self.state.session_id:
            self.memory.save_message(self.state.session_id, "system", text)

    def _send(self) -> None:
        text = self._input.get("1.0", "end").strip()
        if not text:
            return
        if not self.lm.model_id:
            self.notify("⚠️ Сначала загрузи модель!")
            return
        self._input.delete("1.0", "end")
        if not self.state.session_id:
            self._new_session()
        self._insert_bubble(text, "user")
        self.memory.save_message(self.state.session_id, "user", text)
        self._start_generation(text)

    def _on_enter(self, event: Any) -> Optional[str]:
        if not (event.state & 0x1):
            self._send()
            return "break"
        return None

    def _start_generation(self, user_msg: str) -> None:
        self.state.generating = True
        self._stop_flag.clear()
        self._stream_text = ""

        self.state.last_user_activity = time.time()

        with self.performance.measure("load_history_for_generation"):
            history = self.memory.load_history(self.state.session_id, limit=Config.MAX_HISTORY)
        ts = datetime.now().strftime("%H:%M")

        # Используем personality из state (единый источник)
        personality = self.state.current_personality
        facts = self.memory.get_facts(self.state.session_id)
        summary = self.memory.get_latest_summary(self.state.session_id)
        emotional_context = self.behavior.get_emotional_context_block(user_msg)

        with self.performance.measure("build_prompt"):
            system_prompt = self.personality_engine.build_prompt(
                personality=personality,
                facts=facts,
                summary=summary,
                emotional_context=emotional_context,
            )

        logger.info(f"Generating with personality: {personality}")

        self._stream_bubble = self._insert_bubble("…", "assistant", ts)
        self._show_typing(True)

        def wrapped_on_done(full: str):
            with self.performance.measure("variate_speech"):
                varied_full = self.behavior.variate_speech(full)
            self._on_done(varied_full)

        self.lm.respond_stream(
            history, user_msg, system_prompt,
            on_token=self._on_token,
            on_done=wrapped_on_done,
            on_error=self._on_error,
            stop_flag=self._stop_flag,
        )

    def _on_token(self, token: str) -> None:
        self._stream_text += token
        txt = self._stream_text

        def upd() -> None:
            if self._stream_bubble:
                self._stream_bubble.update_text(txt)
                self._scroll_bottom()

        self.root.after(0, upd)

    @debounce(50)
    def _debounced_scroll(self):
        self._scroll_bottom()

    def _on_done(self, full: str) -> None:
        def finish() -> None:
            self._show_typing(False)
            self.state.generating = False
            if full.strip() and self.state.session_id:
                self.memory.save_message(self.state.session_id, "assistant", full)
                self._update_emotions(full)
                self._maybe_summarize()
            self._stream_bubble = None
            self._reload_sessions()

        self.root.after(0, finish)

    def _on_error(self, err: str) -> None:
        def show() -> None:
            self._show_typing(False)
            self.state.generating = False
            if self._stream_bubble:
                try:
                    self._stream_bubble.update_text(f"❌ {err}")
                except Exception:
                    pass
            self._stream_bubble = None

        self.root.after(0, show)

    def _update_emotions(self, ai_msg: str) -> None:
        if not self.state.session_id:
            return
        history = self.memory.load_history(self.state.session_id, limit=2)
        user_msg = ""
        for msg in reversed(history):
            if msg["role"] == "user":
                user_msg = msg["content"]
                break
        if not user_msg:
            return

        idle = time.time() - self.state.last_user_activity
        with self.performance.measure("update_emotions"):
            self.state.emotion.update(user_msg, ai_msg, idle)

    def _maybe_summarize(self) -> None:
        if not self.state.session_id:
            return
        cnt = self.memory.count_messages(self.state.session_id)
        if cnt < Config.MAX_SUMMARY_MSGS or cnt % Config.MAX_SUMMARY_MSGS != 0:
            return
        history = self.memory.load_history(self.state.session_id, limit=Config.MAX_SUMMARY_MSGS)
        lines = [f"[{m['role']}] {m['content'][:120]}" for m in history
                 if m["role"] in ("user", "assistant")]
        summary = "Краткое резюме разговора:\n" + "\n".join(lines[-10:])
        self.memory.save_summary(self.state.session_id, summary, cnt)

    def _stop_generation(self) -> None:
        self._stop_flag.set()
        self.state.generating = False
        self._show_typing(False)
        self.notify("⏹ Остановлено")

    def _show_typing(self, show: bool) -> None:
        try:
            self._typing_lbl.configure(
                text="✨ Люси набирает…" if show else ""
            )
        except Exception:
            pass

    def _scroll_bottom(self) -> None:
        def _do() -> None:
            try:
                canvas = (
                    getattr(self._chat_frame, "_parent_canvas", None)
                    or getattr(self._chat_frame, "_canvas", None)
                    or getattr(self._chat_frame, "canvas", None)
                )
                if canvas:
                    canvas.yview_moveto(1.0)
            except Exception:
                pass

        self.root.after(80, _do)

    def delete_message(self, msg_id: str) -> None:
        if msg_id in self.messages:
            self.messages[msg_id].destroy()
            del self.messages[msg_id]
            if msg_id in self._message_cache:
                del self._message_cache[msg_id]
            self.notify("🗑 Удалено")

    def open_edit_dialog(self, msg_id: str, old_text: str) -> None:
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("✏️ Редактировать")
        dlg.geometry("500x240")
        dlg.configure(fg_color=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        ctk.CTkLabel(dlg, text="Новый текст:",
                     text_color=PALETTE["text"]).pack(anchor="w", padx=20, pady=(16, 4))
        tb = ctk.CTkTextbox(dlg, height=100, fg_color=PALETTE["surface2"],
                            text_color=PALETTE["text"])
        tb.pack(fill="x", padx=20)
        tb.insert("1.0", old_text)
        tb.focus()

        def save() -> None:
            new = tb.get("1.0", "end").strip()
            if new and msg_id in self.messages:
                self.messages[msg_id].text = new
                self.messages[msg_id].update_text(new)
                self.notify("✅ Обновлено")
            dlg.destroy()

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(pady=12)
        ctk.CTkButton(row, text="✅ Сохранить", fg_color=PALETTE["accent"],
                      command=save).pack(side="left", padx=6)
        ctk.CTkButton(row, text="✖ Отмена", fg_color=PALETTE["surface2"],
                      command=dlg.destroy).pack(side="left", padx=6)

    def regenerate(self, msg_id: str) -> None:
        if self.state.generating:
            self.notify("⏳ Уже генерирую")
            return
        prev_user: Optional[str] = None
        found = False
        for _, b in reversed(list(self.messages.items())):
            if b.msg_id == msg_id:
                found = True
                continue
            if found and b.role == "user":
                prev_user = b.text
                break
        if not prev_user:
            self.notify("❓ Нет запроса для перегенерации")
            return
        self.delete_message(msg_id)
        self._start_generation(prev_user)

    def _edit_last(self) -> None:
        for b in reversed(list(self.messages.values())):
            if b.role == "user":
                self.open_edit_dialog(b.msg_id, b.text)
                return

    def _regen_last(self) -> None:
        for b in reversed(list(self.messages.values())):
            if b.role == "assistant":
                self.regenerate(b.msg_id)
                return

    def _copy_last(self) -> None:
        for b in reversed(list(self.messages.values())):
            if b.role == "assistant":
                clip_copy(b.text, self.root)
                self.notify("📋 Скопировано")
                return

    def _refresh_models(self) -> None:
        def _load() -> None:
            all_models: List[Dict] = []
            try:
                if self.lm.ensure_connected():
                    for m in self.lm.get_models():
                        all_models.append({"id": m, "display": f"🔷 {m[:44]}"})
            except Exception as exc:
                logger.error(f"Refresh models error: {exc}")
            for path in self.settings.get("model_paths", []):
                all_models.extend(self.lm.scan_folder(path))
            self.models = all_models
            names = [m["display"] for m in all_models]

            def ui() -> None:
                self._model_combo.configure(values=names)
                if names:
                    self._model_combo.set(names[0])
                    self.notify(f"📊 Найдено: {len(all_models)} моделей")

            self.root.after(0, ui)

        threading.Thread(target=_load, daemon=True).start()

    def _load_model(self) -> None:
        sel = self._model_combo.get()
        if not sel or sel == "Выберите модель":
            return
        model = next((m for m in self.models if m["display"] == sel), None)
        if not model:
            return
        if not self.lm.ensure_connected():
            self.notify("❌ LM Studio не подключён. Нажмите 🔌 Подключить")
            return
        self._set_status("⏳ Загружаю модель…", PALETTE["warning"])

        def _do() -> None:
            ok = self.lm.load_model(model["id"])
            if ok:
                self.state.model_id = model["id"]
                self.state.model_status = "loaded"

                def ui() -> None:
                    self._set_status(f"✅ {model['id'][:30]}", PALETTE["success"])
                    if not self.state.session_id:
                        self._new_session()
                    self.notify("✅ Модель загружена!")
                    favs = self.settings.get("favorites", [])
                    if model["id"] not in favs:
                        favs.append(model["id"])
                        self.settings.set("favorites", favs)
                        self._reload_favorites()

                self.root.after(0, ui)
            else:
                self.state.model_status = "error"
                self.root.after(0, lambda: self._set_status(
                    "❌ Ошибка загрузки", PALETTE["error"]
                ))

        threading.Thread(target=_do, daemon=True).start()

    def _reload_quick_replies(self) -> None:
        for w in self._quick_frame.winfo_children():
            w.destroy()
        for q in self.settings.get("quick_replies", []):
            ctk.CTkButton(
                self._quick_frame, text=q[:30], anchor="w", height=30,
                fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                font=ctk.CTkFont("Segoe UI", 11),
                command=lambda t=q: self._send_quick(t),
            ).pack(fill="x", pady=1)

    def _send_quick(self, text: str) -> None:
        self._input.delete("1.0", "end")
        self._input.insert("1.0", text)
        self._send()

    def _reload_favorites(self) -> None:
        for w in self._fav_frame.winfo_children():
            w.destroy()
        for fav in self.settings.get("favorites", []):
            ctk.CTkButton(
                self._fav_frame, text=fav[:26], anchor="w", height=30,
                fg_color=PALETTE["surface2"], hover_color=PALETTE["border"],
                font=ctk.CTkFont("Segoe UI", 11),
                command=lambda mid=fav: self._load_fav(mid),
            ).pack(fill="x", pady=1)

    def _load_fav(self, model_id: str) -> None:
        m = next((m for m in self.models if m["id"] == model_id), None)
        if not m:
            self.notify("❓ Модель не найдена — нажми 🔄")
            return
        self._model_combo.set(m["display"])
        self._load_model()

    def _export_chat(self) -> None:
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Текст", "*.txt"), ("JSON", "*.json")],
            initialfile=f"lucy_chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            parent=self.root,
        )
        if not filename:
            return
        lines = []
        for b in self.messages.values():
            prefix = "👤 Вы: " if b.role == "user" else "✨ Люси: "
            lines.append(prefix + b.text + "\n")
        try:
            with open(filename, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            self.notify("💾 Чат экспортирован")
        except Exception as exc:
            self.notify(f"❌ {exc}")

    def _search_dialog(self) -> None:
        dlg = ctk.CTkInputDialog(title="🔍 Поиск", text="Введите запрос:")
        query = dlg.get_input()
        if not query:
            return
        found = 0
        for b in self.messages.values():
            if query.lower() in b.text.lower():
                b.configure(border_color=PALETTE["warning"])
                self.root.after(3000, lambda w=b: w.configure(border_color="transparent"))
                found += 1
        self.notify(f"🔍 Найдено: {found}")

    def _attach_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Прикрепить файл",
            filetypes=[("Текст", "*.txt *.py *.js *.ts *.md"), ("Все", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read(4000)
            name = os.path.basename(path)
            self._input.insert("end", f"\n\n[Файл: {name}]\n{content}")
            self.notify(f"📎 Прикреплён: {name}")
        except Exception as exc:
            self.notify(f"❌ {exc}")

    def _connect_lm(self) -> None:
        try:
            self._set_status("⏳ Подключение...", PALETTE["warning"])
        except Exception:
            pass
        
        def callback(ok: bool, msg: str):
            color = PALETTE["success"] if ok else PALETTE["error"]
            self.root.after(0, lambda: self._set_status(msg, color))
            if ok:
                self.lm.connected = True
                self.state.connected = True
                self.root.after(0, self._refresh_models)
            else:
                self.lm.connected = False
                self.state.connected = False

        self.lm.test_async(callback, timeout=5)

    def _launch_lm_studio(self) -> None:
        custom = self.settings.get("lm_path", "").strip()
        appdata = os.environ.get("LOCALAPPDATA", "")
        userprofile = os.environ.get("USERPROFILE", "")
        candidates = list(filter(None, [
            custom,
            os.path.join(appdata, "Programs", "LM-Studio", "LM Studio.exe"),
            os.path.join(appdata, "LM-Studio", "LM Studio.exe"),
            os.path.join(userprofile, "AppData", "Local", "Programs",
                         "LM-Studio", "LM Studio.exe"),
            os.path.join(userprofile, "AppData", "Local",
                         "LM-Studio", "LM Studio.exe"),
            "/Applications/LM Studio.app/Contents/MacOS/LM Studio",
            os.path.expanduser(
                "~/Applications/LM Studio.app/Contents/MacOS/LM Studio"
            ),
        ]))
        for path in candidates:
            if os.path.isfile(path):
                try:
                    subprocess.Popen([path], shell=False,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    self.notify("🚀 LM Studio запускается...")
                    self.root.after(4000, self._connect_lm)
                    return
                except Exception as exc:
                    self.notify(f"❌ Ошибка запуска: {exc}")
                    return
        self.notify("❌ LM Studio не найден. Укажите путь в Настройки → Соединение")
        self._open_settings()

    def notify(self, text: str, ms: int = 2200) -> None:
        frame = ctk.CTkFrame(self.root, fg_color=PALETTE["accent"], corner_radius=12)
        frame.place(relx=0.5, rely=0.07, anchor="center")
        inner = ctk.CTkFrame(frame, fg_color=PALETTE["surface2"], corner_radius=10)
        inner.pack(padx=2, pady=2)
        ctk.CTkLabel(
            inner, text=f"  {text}  ",
            fg_color="transparent", text_color=PALETTE["text"],
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            padx=14, pady=7,
        ).pack()
        self.root.after(ms, frame.destroy)

    def _set_status(self, text: str, color: str) -> None:
        try:
            self._status_lbl.configure(text=text, text_color=color)
        except Exception:
            pass
        logger.info(f"Status: {text}")

    def _open_settings(self) -> None:
        SettingsDialog(self)

    def _on_close(self) -> None:
        self._stop_flag.set()
        self.behavior.stop_initiative_checker()
        try:
            self.memory.close()
            logger.info(self.performance.report())
        except Exception as e:
            logger.error(f"Close error: {e}")
        try:
            if self.root.winfo_exists():
                self.root.destroy()
        except Exception:
            pass

    def _apply_theme_live(self, name: str) -> None:
        prev = dict(PALETTE)
        apply_theme(name)

        recolor_map: dict[str, str] = {}
        for key in PALETTE:
            if prev[key] != PALETTE[key]:
                recolor_map[prev[key]] = PALETTE[key]

        if not recolor_map:
            self.notify(f"🎨 Тема «{name}» (без изменений)")
            return

        def _patch(widget) -> None:
            cls = type(widget).__name__
            attrs: list[str] = []
            
            if cls in ("CTkFrame", "CTkScrollableFrame"):
                attrs = ["fg_color"]
            elif cls == "CTkLabel":
                attrs = ["fg_color", "text_color"]
            elif cls in ("CTkButton",):
                attrs = ["fg_color", "text_color", "hover_color", "border_color"]
            elif cls in ("CTkEntry", "CTkTextbox"):
                attrs = ["fg_color", "text_color", "border_color"]
            elif cls == "CTkComboBox":
                attrs = ["fg_color", "text_color", "border_color",
                        "button_color", "dropdown_fg_color"]
            elif cls == "CTkScrollbar":
                attrs = ["fg_color", "button_color"]
            elif cls == "CTkCheckBox":
                attrs = ["fg_color", "text_color", "checkmark_color"]
            elif cls in ("CTkTabview",):
                attrs = ["fg_color", "segmented_button_selected_color",
                        "segmented_button_unselected_color"]

            for attr in attrs:
                try:
                    cur = widget.cget(attr)
                    if isinstance(cur, (list, tuple)):
                        cur = cur[0]
                    if cur in recolor_map:
                        widget.configure(**{attr: recolor_map[cur]})
                except Exception:
                    pass

            try:
                for child in widget.winfo_children():
                    _patch(child)
            except Exception:
                pass

        self.root.configure(fg_color=PALETTE["bg"])
        _patch(self.root)

        try:
            self._status_lbl.configure(
                text_color=PALETTE["warning"]
                if "⏳" in self._status_lbl.cget("text")
                else (PALETTE["success"]
                      if "✅" in self._status_lbl.cget("text")
                      else PALETTE["error"])
            )
        except Exception:
            pass

        try:
            self._typing_lbl.configure(text_color=PALETTE["accent2"])
        except Exception:
            pass

        try:
            self._chat_frame.configure(
                fg_color=PALETTE["bg"],
                scrollbar_fg_color=PALETTE["surface2"],
                scrollbar_button_color=PALETTE["border"],
            )
        except Exception:
            pass

        try:
            self._model_combo.configure(
                fg_color=PALETTE["surface2"],
                border_color=PALETTE["border"],
                button_color=PALETTE["accent"],
                dropdown_fg_color=PALETTE["surface2"],
            )
        except Exception:
            pass

        self._repaint_bubbles()
        self.notify(f"🎨 Тема «{name}» применена!")

    def _show_input_context_menu(self, event) -> None:
        import tkinter as tk

        menu = tk.Menu(self.root, tearoff=0,
                       bg=PALETTE["surface2"], fg=PALETTE["text"],
                       activebackground=PALETTE["accent"],
                       activeforeground="white",
                       relief="flat", bd=0,
                       font=("Segoe UI", 11))

        has_sel = False
        try:
            self._input._textbox.selection_get()
            has_sel = True
        except Exception:
            pass

        has_clip = False
        try:
            has_clip = bool(self.root.clipboard_get())
        except Exception:
            pass

        def do_cut():
            try:
                text = self._input._textbox.selection_get()
                self._input._textbox.delete("sel.first", "sel.last")
                clip_copy(text, self.root)
            except Exception:
                pass

        def do_copy():
            try:
                text = self._input._textbox.selection_get()
                clip_copy(text, self.root)
            except Exception:
                clip_copy(self._input.get("1.0", "end").strip(), self.root)
            self.notify("📋 Скопировано")

        def do_paste():
            try:
                text = self.root.clipboard_get()
                try:
                    self._input._textbox.delete("sel.first", "sel.last")
                except Exception:
                    pass
                self._input._textbox.insert("insert", text)
            except Exception:
                pass

        def do_select_all():
            self._input._textbox.tag_add("sel", "1.0", "end")

        def do_clear():
            self._input.delete("1.0", "end")

        menu.add_command(label="✂  Вырезать", command=do_cut,
                         state="normal" if has_sel else "disabled")
        menu.add_command(label="📋  Копировать", command=do_copy,
                         state="normal" if has_sel else "disabled")
        menu.add_command(label="📌  Вставить", command=do_paste,
                         state="normal" if has_clip else "disabled")
        menu.add_separator()
        menu.add_command(label="⬜  Выделить всё", command=do_select_all)
        menu.add_command(label="🗑  Очистить", command=do_clear)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _repaint_bubbles(self) -> None:
        for bubble in list(self.messages.values()):
            try:
                if bubble.role == "user":
                    inner = bubble.winfo_children()
                    if not inner:
                        continue
                    outer = inner[0]
                    children = outer.winfo_children()
                    if not children:
                        continue
                    bframe = children[0]
                    bframe.configure(
                        fg_color=PALETTE["user_bubble"],
                        border_color=PALETTE["accent"],
                    )
                    for w in bframe.winfo_children():
                        cls = type(w).__name__
                        if cls == "CTkLabel":
                            try:
                                t = w.cget("text")
                                if t == "Вы":
                                    w.configure(text_color=PALETTE["accent"])
                                else:
                                    w.configure(text_color=PALETTE["user_text"])
                            except Exception:
                                pass
                elif bubble.role == "assistant":
                    inner = bubble.winfo_children()
                    if not inner:
                        continue
                    outer = inner[0]
                    children = outer.winfo_children()
                    if not children:
                        continue
                    bframe = children[0]
                    bframe.configure(
                        fg_color=PALETTE["ai_bubble"],
                        border_color=PALETTE["border"],
                    )
                    for w in bframe.winfo_children():
                        cls = type(w).__name__
                        if cls == "CTkLabel":
                            try:
                                t = w.cget("text")
                                if "Люси" in t:
                                    w.configure(text_color=PALETTE["accent2"])
                                else:
                                    w.configure(text_color=PALETTE["ai_text"])
                            except Exception:
                                pass
            except Exception:
                pass

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            logger.info("Application terminated by user")
        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            self._on_close()


def clip_copy(text: str, root: Any) -> None:
    if CLIP_OK:
        pyperclip.copy(text)
    else:
        root.clipboard_clear()
        root.clipboard_append(text)


def make_tooltip(widget: Any, text: str, root: Any) -> None:
    tip: List[Optional[Any]] = [None]

    def show(e: Any) -> None:
        tip[0] = ctk.CTkLabel(
            root, text=text,
            fg_color=PALETTE["surface2"], text_color=PALETTE["muted"],
            corner_radius=6, padx=8, pady=4,
            font=ctk.CTkFont("Segoe UI", 10),
        )
        tip[0].place(
            x=widget.winfo_rootx() - root.winfo_rootx(),
            y=widget.winfo_rooty() - root.winfo_rooty() - 28,
        )

    def hide(e: Any) -> None:
        if tip[0]:
            tip[0].destroy()
            tip[0] = None

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)


if __name__ == "__main__":
    threading.stack_size(65536)
    gc.set_threshold(700, 10, 5)
    
    greeting = PersonalityEngine.get_greeting()
    dt_str = PersonalityEngine.get_current_datetime_str()
    
    print("=" * 60)
    print(f"  ✨  {Config.APP_TITLE} v{Config.APP_VERSION}  —  Запуск  ✨")
    print(f"  👨‍💻  Разработчик: {Config.DEVELOPER}  👨‍💻")
    print("=" * 60)
    print(f"  🕐 {greeting}! Сейчас {dt_str}")
    print("=" * 60)
    
    if not REQUESTS_OK:
        print("  ⚠️  Установите requests: pip install requests")
    print()
    
    app = LucyApp()
    app.run()