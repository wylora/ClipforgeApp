"""Опциональный LLM-анализ через OpenAI-совместимый API (Ollama, LM Studio, облачные сервисы).
Без LLM приложение полностью работает на локальных алгоритмах."""
from __future__ import annotations

import json
import logging
import re
import urllib.request

log = logging.getLogger("clipforge.llm")

MODE_HINT = {
    "funny": "Оцени, насколько фрагмент смешной и сработает как короткий юмористический ролик.",
    "serious": "Оцени, насколько фрагмент содержателен, завершён и полезен как самостоятельный короткий ролик.",
    "best": "Оцени, насколько фрагмент интересен и цепляет как короткий ролик (юмор, эмоции, польза, динамика).",
}


class LLMClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 90.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(self.base_url + path, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def available(self) -> bool:
        try:
            req = urllib.request.Request(self.base_url + "/models",
                                         headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            return False

    def chat_json(self, system: str, user: str) -> dict | None:
        try:
            data = self._post("/chat/completions", {
                "model": self.model, "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            text = data["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0)) if m else None
        except Exception as e:
            log.warning("LLM-запрос не удался: %s", e)
            return None

    def rate(self, text: str, mode: str, custom_prompt: str = "") -> dict | None:
        task = MODE_HINT.get(mode) or f"Оцени, насколько фрагмент соответствует запросу: «{custom_prompt}»."
        res = self.chat_json(
            "Ты редактор коротких видео. Отвечай ТОЛЬКО JSON: "
            '{"score": число 0-100, "reason": "одна фраза", "title": "заголовок до 60 символов"}.',
            f"{task}\nТранскрипт фрагмента:\n{text[:2500]}")
        if res and isinstance(res.get("score"), (int, float)):
            return {"score": max(0.0, min(100.0, float(res["score"]))), "reason": str(res.get("reason", "")),
                    "title": str(res.get("title", ""))}
        return None

    def metadata(self, text: str, mode: str) -> dict | None:
        return self.chat_json(
            "Ты SMM-редактор. Отвечай ТОЛЬКО JSON: "
            '{"titles": ["3 варианта до 60 символов"], "description": "до 200 символов", "hashtags": ["5-8 хэштегов без #"]}.',
            f"Режим: {mode}. Язык ответа — язык транскрипта.\n{text[:2500]}")

    def translate_lines(self, lines: list[str], target: str) -> list[str] | None:
        res = self.chat_json(
            'Переводи построчно. Отвечай ТОЛЬКО JSON: {"lines": ["перевод 1", ...]} — столько же строк, сколько на входе.',
            f"Переведи на язык «{target}»:\n" + json.dumps(lines, ensure_ascii=False))
        if res and isinstance(res.get("lines"), list) and len(res["lines"]) == len(lines):
            return [str(x) for x in res["lines"]]
        return None
