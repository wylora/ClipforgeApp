"""Лексические признаки (русский и английский). Для других языков работают языконезависимые сигналы:
пунктуация, токены смеха, звук, видео и (опционально) LLM."""
from __future__ import annotations

import re
from dataclasses import dataclass


def _rx(*stems: str) -> re.Pattern:
    body = "|".join(stems)
    return re.compile(rf"(?<!\w)(?:{body})\w*", re.IGNORECASE | re.UNICODE)


LAUGH_RE = re.compile(
    r"(?<!\w)(?:а?ха(?:-?ха)+\w*|хех(?:е)+|ахах\w*|хихи+|хи-хи|лол|кек|ржу\w*|ржач\w*|"
    r"ha(?:-?ha)+\w*|he(?:-?he)+\w*|lol|lmao|rofl)(?!\w)|"
    r"[\[\(\*]\s*(?:смех|смеётся|смеется|смеются|аплодисменты|laughter|laughs|laughing|applause|chuckles)\s*[\]\)\*]|[😂🤣😆]",
    re.IGNORECASE | re.UNICODE)

HUMOR_RE = _rx("шут", "прикол", "смешн", "угар", "юмор", "анекдот", "мем", "кринж", "нелеп", "абсурд", "умор",
               "обхохот", "комеди", "ирони", "сарказм", "прикинь", "представь себе", "ну ты даешь", "ну ты даёшь",
               "joke", "funny", "hilarious", "kidding", "ridiculous", "ironic", "sarcas", "absurd", "meme", "cringe",
               "imagine this", "punchline")
SURPRISE_RE = _rx("неожиданн", "внезапн", "оказал", "ничего себе", "не может быть", "офигеть", "обалдеть", "ого", "вау",
                  "капец", "жесть", "в смысле", "ты шутишь", "не верю", "серьезно\\?", "серьёзно\\?", "чего\\?",
                  "no way", "wait what", "what the", "oh my god", "omg", "seriously\\?", "unexpected", "turns out",
                  "suddenly", "can't believe", "cannot believe", "wow", "are you kidding")
CONTRAST_RE = _rx("зато", "вместо", "на самом деле", "хотя", "однако", "а он", "а она", "а я", "и тут",
                  "instead", "actually", "however", "except", "and then", "but then")
HYPER_RE = _rx("никогда", "всегда", "вообще", "абсолютно", "самый", "миллион", "тысячу раз", "ни разу",
               "literally", "never", "always", "worst", "million", "forever", "insane", "crazy")

INSIGHT_RE = _rx("главное", "важно", "ключ", "суть", "вывод", "итог", "понял", "осознал", "секрет", "правило", "принцип",
                 "закон", "ошибк", "урок", "причина", "поэтому", "следовательно", "потому что", "то есть",
                 "на самом деле", "дело в том", "парадокс", "истина",
                 "important", "key ", "the reason", "lesson", "realized", "secret", "rule", "principle", "mistake",
                 "the point", "in conclusion", "that's why", "which means", "the truth")
ADVICE_RE = _rx("совет", "рекоменду", "нужно", "надо", "следует", "старайтесь", "не делайте", "попробуйте", "запомните",
                "помните", "начните", "перестаньте", "лучше всего", "стоит", "научитесь",
                "advice", "recommend", "you should", "you need", "try to", "don't ", "remember", "start ", "stop ", "make sure")
FACT_RE = _rx("исследован", "учёные", "ученые", "статистик", "процент", "данные", "доказан", "эксперимент", "по данным",
              "в среднем", "миллиард", "study", "research", "statistic", "percent", "according to", "data shows",
              "scientists", "proven", "experiment", "on average", "billion")
EMOTION_RE = _rx("честно", "признаюсь", "боялся", "боялась", "плакал", "плакала", "больно", "благодарен", "благодарна",
                 "люблю", "ненавижу", "мечта", "потерял", "потеряла", "семь", "смерт", "счасть", "страх", "тяжело",
                 "honestly", "i admit", "afraid", "cried", "painful", "grateful", "love", "hate", "dream", "lost",
                 "family", "death", "happiness", "fear", "hard for me")
MOTIVATION_RE = _rx("ты сможешь", "верь", "цель", "успех", "не сдавайся", "мотивац", "дисциплин", "привычк", "результат",
                    "никогда не поздно", "you can do", "believe", "goal", "success", "never give up", "discipline",
                    "habit", "it's never too late")
CONCLUSION_RE = _rx("итак", "в итоге", "в общем", "вывод", "короче говоря", "так вот", "поэтому",
                    "so ", "in the end", "overall", "that's why", "bottom line", "to sum up")
FILLER_RE = re.compile(r"(?<!\w)(?:ну|вот|типа|как бы|короче|э+|эм+|значит|um+|uh+|like|you know|i mean|basically|kind of|sort of)(?!\w)",
                       re.IGNORECASE | re.UNICODE)
DEPENDENT_START_RE = re.compile(
    r"^\W*(?:и|но|а|да|так|тогда|потому|поэтому|он|она|они|оно|это|этот|эта|эти|тот|та|те|его|её|их|"
    r"and|but|so|then|because|he|she|they|it|this|that|these|those|his|her|their)(?!\w)", re.IGNORECASE | re.UNICODE)
TERMINAL_RE = re.compile(r"[.!?…]+[\"'»)\]]*\s*$")

STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня
еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам сказал ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем
the a an and or but if then so of to in on at for with as is are was were be been it this that these those i you he she we
they me him her us them my your his its our their do does did have has had not no yes just very can will would should
could from by about into over than too also what which who whom when where why how all any some more most""".split())

_WORD_RE = re.compile(r"[\w'-]+", re.UNICODE)


def tokens(text: str) -> list[str]:
    return [w.lower().replace("ё", "е") for w in _WORD_RE.findall(text)]


def content_tokens(text: str) -> list[str]:
    return [w for w in tokens(text) if w not in STOP and len(w) > 2 and not w.isdigit()]


def stem(w: str) -> str:
    return w[:5]


def ends_terminal(text: str) -> bool:
    return bool(TERMINAL_RE.search(text.strip()))


def starts_dependent(text: str) -> bool:
    return bool(DEPENDENT_START_RE.match(text.strip()))


@dataclass
class HumorCues:
    laugh: int = 0
    marker: int = 0
    surprise: int = 0
    contrast: int = 0
    hyper: int = 0
    excl: int = 0
    question: int = 0


def humor_cues(text: str) -> HumorCues:
    return HumorCues(
        laugh=len(LAUGH_RE.findall(text)), marker=len(HUMOR_RE.findall(text)), surprise=len(SURPRISE_RE.findall(text)),
        contrast=len(CONTRAST_RE.findall(text)), hyper=len(HYPER_RE.findall(text)),
        excl=text.count("!"), question=text.count("?"))


@dataclass
class SeriousCues:
    insight: int = 0
    advice: int = 0
    fact: int = 0
    emotion: int = 0
    motivation: int = 0
    conclusion: int = 0
    numbers: int = 0
    filler_ratio: float = 0.0
    density: float = 0.0
    n_words: int = 0


def serious_cues(text: str) -> SeriousCues:
    toks = tokens(text)
    n = max(1, len(toks))
    ct = content_tokens(text)
    return SeriousCues(
        insight=len(INSIGHT_RE.findall(text)), advice=len(ADVICE_RE.findall(text)), fact=len(FACT_RE.findall(text)),
        emotion=len(EMOTION_RE.findall(text)), motivation=len(MOTIVATION_RE.findall(text)),
        conclusion=len(CONCLUSION_RE.findall(text)), numbers=len(re.findall(r"\d+[\d.,%]*", text)),
        filler_ratio=len(FILLER_RE.findall(text)) / n, density=len(ct) / n, n_words=len(toks))
