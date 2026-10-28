"""Персонализация по обратной связи пользователя.

Это НЕ обучение нейросети: простая логистическая регрессия на 6 числах (оценки кандидата), обученная
на отметках «смешно/не смешно/интересно/скучно», сохранённых локально. Влияние растёт с числом отметок и
ограничено, чтобы не ломать базовое ранжирование.
"""
from __future__ import annotations

import numpy as np

FEATURES = ["semantic", "humor", "reaction", "audio", "completeness", "visual"]
LABELS = {"funny": 1, "interesting": 1, "not_funny": 0, "boring": 0}
LABEL_TITLES = {"funny": "Смешно", "not_funny": "Не смешно", "interesting": "Интересно", "boring": "Скучно"}
MIN_SAMPLES = 8


def group_for_mode(mode: str) -> str:
    return "humor" if mode == "funny" else "general"


def labels_for_group(group: str) -> tuple[str, str]:
    return ("funny", "not_funny") if group == "humor" else ("interesting", "boring")


class PersonalModel:
    def __init__(self, samples: dict[str, list[tuple[dict, int]]] | None = None):
        self.w: dict[str, np.ndarray] = {}
        self.n: dict[str, int] = {}
        for group, rows in (samples or {}).items():
            self._fit(group, rows)

    @staticmethod
    def _vec(scores: dict) -> np.ndarray:
        return np.array([scores.get(k, 50.0) / 100.0 for k in FEATURES] + [1.0])

    def _fit(self, group: str, rows: list[tuple[dict, int]]) -> None:
        self.n[group] = len(rows)
        y = np.array([r[1] for r in rows], dtype=float)
        if len(rows) < MIN_SAMPLES or y.min() == y.max():
            return
        X = np.stack([self._vec(r[0]) for r in rows])
        w = np.zeros(X.shape[1])
        for _ in range(400):
            p = 1 / (1 + np.exp(-X @ w))
            w -= 0.5 * (X.T @ (p - y) / len(y) + 0.05 * w)
        self.w[group] = w

    def predict(self, scores: dict, mode: str) -> float | None:
        w = self.w.get(group_for_mode(mode))
        if w is None:
            return None
        return float(1 / (1 + np.exp(-self._vec(scores) @ w)))

    def influence(self, mode: str) -> float:
        return min(0.35, self.n.get(group_for_mode(mode), 0) / 80.0) if group_for_mode(mode) in self.w else 0.0

    def adjust(self, quality: float, scores: dict, mode: str) -> float:
        p = self.predict(scores, mode)
        lam = self.influence(mode)
        return quality if p is None else (1 - lam) * quality + lam * 100.0 * p
