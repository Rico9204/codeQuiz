from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parents[1]
PROMPT_DIR = BASE_DIR / "prompts"

DIFFICULTY_GUIDES = {
    "기초": (
        "코드의 역할과 실행 흐름을 중심으로 질문하세요. "
        "함수/클래스의 목적, 주요 변수, 조건문과 데이터 흐름을 설명할 수 있는지 확인합니다. "
        "질문 구성은 easy 3개, medium 2개 정도로 하고 억지로 어려운 설계 질문을 만들지 마세요."
    ),
    "보통": (
        "코드의 실행 흐름뿐 아니라 왜 그렇게 구현했는지, 특정 로직을 변경했을 때 어떤 영향이 있는지 확인하세요. "
        "구현 이유, 의존 관계, 간단한 edge case를 포함합니다. "
        "질문 구성은 easy 1개, medium 3개, hard 1개 정도로 하세요."
    ),
    "심화": (
        "구현 세부, edge case, 상태 변화, 의존 관계, 성능/안전성, 변경 영향과 설계 trade-off를 중심으로 질문하세요. "
        "단, 제출 코드에서 근거를 찾을 수 없는 추상적 시스템 설계 질문은 피하세요. "
        "질문 구성은 medium 1개, hard 4개 정도로 하세요."
    ),
}

EVALUATION_DIFFICULTY_GUIDES = {
    "기초": (
        "핵심 역할이나 실행 흐름을 맞게 말하면 짧은 답변도 3점까지 인정하세요. "
        "구현 이유, 변경 영향, 예외 상황은 4점을 위한 추가 근거입니다."
    ),
    "보통": (
        "핵심 동작과 그 이유를 모두 설명하면 3점으로 인정하세요. "
        "변경 영향이나 간단한 예외 상황은 4점을 위한 추가 근거입니다."
    ),
    "심화": (
        "핵심 동작과 이유만 설명한 답변은 보통 2점으로 평가하세요. "
        "3점 이상은 코드 근거를 바탕으로 영향, 예외 상황, 의존 관계 또는 설계 판단을 설명해야 합니다."
    ),
}


class LLMError(RuntimeError):
    pass


def _read_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise LLMError("OPENAI_API_KEY가 설정되어 있지 않습니다.")
    return OpenAI(api_key=api_key)


def _model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-5.6-luna")


def _call(prompt: str, instructions: str) -> str:
    try:
        response = _client().responses.create(
            model=_model(),
            instructions=instructions,
            input=prompt,
        )
        text = (response.output_text or "").strip()
        if not text:
            raise LLMError("LLM 응답이 비어 있습니다.")
        return text
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"LLM 호출 실패: {exc}") from exc


def _parse_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
        if not starts:
            raise LLMError("LLM JSON 응답을 해석할 수 없습니다.")
        start = min(starts)
        end = max(text.rfind("]"), text.rfind("}"))
        if end < start:
            raise LLMError("LLM JSON 응답을 해석할 수 없습니다.")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError("LLM JSON 응답을 해석할 수 없습니다.") from exc


def generate_questions(
    code_context: str,
    difficulty: str = "보통",
    count: int = 5,
    previous_questions: list[str] | None = None,
) -> list[dict]:
    difficulty = difficulty if difficulty in DIFFICULTY_GUIDES else "보통"
    prompt = _read_prompt("question.txt").format(
        code=code_context,
        difficulty=difficulty,
        difficulty_guide=DIFFICULTY_GUIDES[difficulty],
        count=count,
        previous_questions=json.dumps(previous_questions or [], ensure_ascii=False),
    )
    raw = _call(
        prompt,
        "코드 기반 구술시험 질문과 참고 답안을 만드는 면접관 역할을 수행하세요. 출력 형식을 엄격히 지키세요.",
    )
    data = _parse_json(raw)

    if not isinstance(data, list) or not data:
        raise LLMError("질문 목록 형식이 올바르지 않습니다.")

    normalized = []
    for item in data[:count]:
        if not isinstance(item, dict) or not item.get("question"):
            continue

        key_points = item.get("key_points", [])
        if not isinstance(key_points, list):
            key_points = []

        normalized.append(
            {
                "question": str(item["question"]).strip(),
                "difficulty": str(item.get("difficulty", "medium")).strip(),
                "category": str(item.get("category", "logic")).strip(),
                "target": str(item.get("target", "")).strip(),
                "intent": str(item.get("intent", "")).strip(),
                "reference_answer": str(item.get("reference_answer", "")).strip(),
                "key_points": [str(x).strip() for x in key_points if str(x).strip()][:4],
                "is_followup": False,
            }
        )

    if (
        len(normalized) != count
        or any(
            not question["reference_answer"] or len(question["key_points"]) < 2
            for question in normalized
        )
    ):
        raise LLMError("사용 가능한 질문을 생성하지 못했습니다.")
    return normalized


def evaluate_answer(
    code_context: str,
    question: str,
    answer: str,
    reference_answer: str = "",
    key_points: list[str] | None = None,
    difficulty: str = "보통",
) -> dict:
    difficulty = difficulty if difficulty in EVALUATION_DIFFICULTY_GUIDES else "보통"
    prompt = _read_prompt("evaluation.txt").format(
        code=code_context,
        question=question,
        reference_answer=reference_answer or "(참고 답안 없음)",
        key_points=json.dumps(key_points or [], ensure_ascii=False),
        answer=answer,
        difficulty=difficulty,
        evaluation_difficulty_guide=EVALUATION_DIFFICULTY_GUIDES[difficulty],
    )
    raw = _call(
        prompt,
        "제출 코드와 답변을 대조하여 이해도를 평가하세요. JSON 외의 텍스트는 출력하지 마세요.",
    )
    data = _parse_json(raw)
    if not isinstance(data, dict):
        raise LLMError("평가 응답 형식이 올바르지 않습니다.")

    try:
        score = max(0, min(4, int(data.get("score", 0))))
    except (TypeError, ValueError):
        score = 0

    return {
        "score": score,
        "reason": str(data.get("reason", "")).strip(),
        "understood": [str(x) for x in data.get("understood", [])][:5],
        "missing": [str(x) for x in data.get("missing", [])][:5],
        "need_followup": bool(data.get("need_followup", score <= 2)),
    }


def generate_followup(
    code_context: str,
    question: str,
    answer: str,
    evaluation: dict,
) -> dict:
    prompt = _read_prompt("followup.txt").format(
        code=code_context,
        question=question,
        answer=answer,
        evaluation=json.dumps(evaluation, ensure_ascii=False),
    )
    raw = _call(
        prompt,
        "부족한 코드 이해도를 검증하는 꼬리질문과 참고 답안을 생성하세요. JSON 외의 텍스트는 출력하지 마세요.",
    )
    data = _parse_json(raw)

    if not isinstance(data, dict) or not data.get("question"):
        raise LLMError("꼬리질문 응답 형식이 올바르지 않습니다.")

    key_points = data.get("key_points", [])
    if not isinstance(key_points, list):
        key_points = []

    if not str(data.get("reference_answer", "")).strip() or len(key_points) < 2:
        raise LLMError("꼬리질문의 참고 답안 또는 핵심 포인트가 부족합니다.")

    return {
        "question": str(data["question"]).strip(),
        "reference_answer": str(data.get("reference_answer", "")).strip(),
        "key_points": [str(x).strip() for x in key_points if str(x).strip()][:4],
    }


def generate_report(records: list[dict]) -> dict:
    compact = []
    for r in records:
        compact.append(
            {
                "question": r.get("question"),
                "answer": r.get("answer"),
                "score": r.get("evaluation", {}).get("score"),
                "reason": r.get("evaluation", {}).get("reason"),
            }
        )

    prompt = _read_prompt("report.txt").format(
        records=json.dumps(compact, ensure_ascii=False, indent=2)
    )
    raw = _call(
        prompt,
        "코드 이해도 인터뷰 기록을 근거 중심으로 요약하세요. AI 사용 여부를 단정하지 마세요.",
    )
    data = _parse_json(raw)
    if not isinstance(data, dict):
        raise LLMError("리포트 응답 형식이 올바르지 않습니다.")

    return {
        "summary": str(data.get("summary", "")).strip(),
        "strengths": [str(x) for x in data.get("strengths", [])][:5],
        "needs_review": [str(x) for x in data.get("needs_review", [])][:5],
    }
