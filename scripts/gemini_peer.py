# -*- coding: utf-8 -*-
"""
Peer-инженер (Gemini или GLM): отправить файл-запрос на ревью, получить ответ.

ЗАПУСК (локально, из корня проекта; ключи берутся из .env):
    python scripts/gemini_peer.py docs/reviews/REQUEST_notify.md

Провайдер выбирается в .env:
    PEER_PROVIDER=glm          # glm | gemini (дефолт: glm, если есть GLM_API_KEY)
    GLM_API_KEY=...            # ключ Zhipu/Z.ai
    GLM_MODEL=glm-5.2          # опционально
    GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4   # или https://api.z.ai/api/paas/v4

Ответ печатается и сохраняется рядом: <имя>_ANSWER.md
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
GLM_KEY = (os.environ.get("GLM_API_KEY") or "").strip()
GLM_MODEL = (os.environ.get("GLM_MODEL") or "glm-5.2").strip()
GLM_BASE = (os.environ.get("GLM_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4").strip().rstrip("/")
PROVIDER = (os.environ.get("PEER_PROVIDER")
            or ("glm" if GLM_KEY else "gemini")).strip().lower()
# Цепочка моделей Gemini: у бесплатного тира AI Studio квота на Pro почти
# нулевая, поэтому после 429 падаем на Flash (щедрый бесплатный тир).
_pref = (os.environ.get("GEMINI_TEXT_MODEL") or "gemini-2.5-pro").strip()
MODELS = list(dict.fromkeys([_pref, "gemini-2.5-flash", "gemini-2.0-flash"]))

SYSTEM = (
    "Ты — опытный инженер-геймдев (Telegram-боты, Python/aiogram, MUD/MMO-механики), "
    "выступаешь peer-ревьюером в команде. Тебе дают архитектурный бриф или код. "
    "Отвечай по-русски, кратко и по делу: 1) риски и дыры в дизайне; 2) что бы ты "
    "сделал иначе и почему; 3) чего не хватает; 4) что хорошо (одной строкой). "
    "Не пересказывай бриф. Конкретика важнее вежливости."
)


def _call(model: str, text: str) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={KEY}")
    body = json.dumps({
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": text}]}],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    return d["candidates"][0]["content"]["parts"][0]["text"]


def _retry_delay(err_body: str) -> int:
    """Вытащить рекомендованную паузу из ответа 429 (retryDelay: '17s')."""
    m = re.search(r'"retryDelay"\s*:\s*"(\d+)', err_body or "")
    return min(int(m.group(1)), 30) if m else 15


def _call_glm(text: str) -> str:
    """GLM (Zhipu/Z.ai): OpenAI-совместимый chat/completions с Bearer-ключом."""
    body = json.dumps({
        "model": GLM_MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": text}],
    }).encode("utf-8")
    req = urllib.request.Request(
        GLM_BASE + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {GLM_KEY}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"]


def ask(text: str) -> str:
    """Спросить пира: GLM напрямую, либо Gemini по цепочке моделей с ретраями."""
    if PROVIDER == "glm":
        if not GLM_KEY:
            raise SystemExit("❌ PEER_PROVIDER=glm, но нет GLM_API_KEY в .env")
        print(f"→ Модель {GLM_MODEL} (GLM)...")
        answer = _call_glm(text)
        print(f"✓ Ответила {GLM_MODEL}")
        return answer
    last_err = None
    for model in MODELS:
        for attempt in (1, 2):
            try:
                print(f"→ Модель {model} (попытка {attempt})...")
                answer = _call(model, text)
                print(f"✓ Ответила {model}")
                return answer
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "ignore")
                except Exception:
                    pass
                last_err = f"{model}: HTTP {e.code}"
                if e.code == 429 and attempt == 1:
                    delay = _retry_delay(body)
                    print(f"  ⏳ 429 (квота). Пауза {delay}с и повтор...")
                    time.sleep(delay)
                    continue
                if e.code in (404, 429, 500, 503):
                    print(f"  ↪ {last_err} — пробую следующую модель")
                    break                     # к следующей модели
                raise                          # другие коды — наружу
    raise SystemExit(
        f"❌ Все модели отказали ({last_err}). Похоже, дневная квота ключа "
        f"исчерпана — проверьте лимиты на aistudio.google.com (вкладка Usage) "
        f"или включите биллинг проекта (подписка AI Pro даёт $10/мес кредитов GCP).")


def main():
    if PROVIDER == "glm" and not GLM_KEY:
        print("❌ PEER_PROVIDER=glm, но нет GLM_API_KEY в .env"); sys.exit(1)
    if PROVIDER != "glm" and not KEY:
        print("❌ Нет GEMINI_API_KEY в .env"); sys.exit(1)
    if len(sys.argv) < 2:
        print("Использование: python scripts/gemini_peer.py <файл-запрос.md>"); sys.exit(1)
    path = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(ROOT, sys.argv[1])
    text = open(path, encoding="utf-8").read()
    chain = GLM_MODEL if PROVIDER == "glm" else ", ".join(MODELS)
    print(f"→ Запрос на ревью ({len(text)} символов), провайдер {PROVIDER}: {chain}")
    answer = ask(text)
    out = os.path.splitext(path)[0] + "_ANSWER.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(answer)
    print(answer)
    print(f"\n💾 Ответ сохранён: {out}")


if __name__ == "__main__":
    main()
