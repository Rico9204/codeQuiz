from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv

from services.code_analyzer import analyze_files, build_llm_context, serializable_files
from services.github_service import GitHubError, fetch_public_repository
from services.llm_service import (
    LLMError,
    evaluate_answer,
    generate_followup,
    generate_questions,
    generate_report,
)
from services.storage import save_session
from services.difficulty import next_difficulty


load_dotenv()

st.set_page_config(
    page_title="CodeViva",
    page_icon="💬",
    layout="wide",
)


def init_state():
    defaults = {
        "step": "submit",
        "project_name": "",
        "source_type": "",
        "source_meta": {},
        "code_files": [],
        "stats": {},
        "code_context": "",
        "questions": [],
        "queue": [],
        "current_index": 0,
        "records": [],
        "report": None,
        "interview_difficulty": "보통",
        "current_difficulty": "보통",
        "adaptive_difficulty": True,
        "selected_paths": [],
        "answer_mode": "인터뷰 종료 후 표시",
        "session_id": uuid4().hex[:12],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_all():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()


def project_from_raw(raw_files, project_name: str, source_type: str, source_meta: dict):
    files, stats = analyze_files(raw_files)
    if not files:
        st.error("분석 가능한 소스코드를 찾지 못했습니다.")
        return

    st.session_state.project_name = project_name
    st.session_state.source_type = source_type
    st.session_state.source_meta = source_meta
    st.session_state.code_files = files
    st.session_state.stats = stats
    st.session_state.code_context = build_llm_context(files)
    st.session_state.step = "preview"
    st.rerun()


def score_to_100(score: float) -> int:
    return round(score / 4 * 100)


def aggregate_categories(records):
    grouped = defaultdict(list)
    for record in records:
        category = record.get("category", "logic")
        grouped[category].append(record["evaluation"]["score"])
    return {
        category: score_to_100(sum(scores) / len(scores))
        for category, scores in grouped.items()
        if scores
    }


init_state()

st.title("CodeViva")
st.caption("제출된 코드를 기반으로 이해도를 확인하는 LLM 코드 구술시험 프로토타입")

with st.sidebar:
    st.markdown("### 진행 단계")
    labels = {
        "submit": "1. 코드 제출",
        "preview": "2. 분석 대상 확인",
        "interview": "3. 인터뷰",
        "result": "4. 결과",
    }
    for key, label in labels.items():
        prefix = "▶" if st.session_state.step == key else "·"
        st.write(f"{prefix} {label}")

    st.divider()
    model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    st.caption(f"LLM: `{model}`")
    if st.button("처음부터 다시 시작", use_container_width=True):
        reset_all()


if st.session_state.step == "submit":
    st.subheader("코드 제출")

    if not os.getenv("OPENAI_API_KEY"):
        st.warning(
            "질문 생성에는 OpenAI API 키가 필요합니다. "
            "프로젝트 루트에 `.env` 파일을 만들고 `OPENAI_API_KEY`를 설정해주세요."
        )

    tab_github, tab_folder, tab_paste = st.tabs(
        ["GitHub Repository", "프로젝트 폴더", "코드 붙여넣기"]
    )

    with tab_github:
        st.markdown("#### 공개 GitHub Repository")
        github_url = st.text_input(
            "Repository URL",
            placeholder="https://github.com/owner/repository",
        )
        if st.button("Repository 분석", type="primary"):
            if not github_url.strip():
                st.error("GitHub Repository URL을 입력해주세요.")
            else:
                with st.spinner("Repository를 불러오고 코드를 분석하는 중입니다..."):
                    try:
                        raw_files, meta = fetch_public_repository(github_url)
                        project_from_raw(
                            raw_files,
                            f"{meta['owner']}/{meta['repo']}",
                            "github",
                            meta,
                        )
                    except GitHubError as exc:
                        st.error(str(exc))
                    except Exception as exc:
                        st.error(f"분석 중 오류가 발생했습니다: {exc}")

    with tab_folder:
        st.markdown("#### 프로젝트 폴더 업로드")
        st.caption("폴더를 그대로 선택할 수 있습니다. 소스코드 외 파일은 자동으로 제외합니다.")
        uploaded = st.file_uploader(
            "프로젝트 폴더 선택",
            accept_multiple_files="directory",
        )
        folder_name = st.text_input("프로젝트 이름", key="folder_project_name")

        if st.button("폴더 분석"):
            if not uploaded:
                st.error("프로젝트 폴더를 선택해주세요.")
            else:
                raw_files = {f.name: f.getvalue() for f in uploaded}
                inferred = folder_name.strip() or Path(uploaded[0].name).parts[0]
                project_from_raw(raw_files, inferred, "folder", {})

    with tab_paste:
        st.markdown("#### 간단한 코드 직접 입력")
        language = st.selectbox(
            "언어",
            ["Python", "JavaScript", "TypeScript", "Java", "C", "C++", "C#", "Go", "Rust"],
        )
        project_name = st.text_input("프로젝트 이름", key="paste_project_name")
        pasted_code = st.text_area(
            "코드",
            height=360,
            placeholder="여기에 코드를 붙여넣으세요.",
        )

        ext_map = {
            "Python": ".py",
            "JavaScript": ".js",
            "TypeScript": ".ts",
            "Java": ".java",
            "C": ".c",
            "C++": ".cpp",
            "C#": ".cs",
            "Go": ".go",
            "Rust": ".rs",
        }

        if st.button("코드 분석"):
            if not pasted_code.strip():
                st.error("코드를 입력해주세요.")
            else:
                name = project_name.strip() or "Pasted Code"
                raw_files = {f"main{ext_map[language]}": pasted_code}
                project_from_raw(raw_files, name, "paste", {"language": language})


elif st.session_state.step == "preview":
    st.subheader("분석 대상 확인")
    st.write(f"**프로젝트:** {st.session_state.project_name}")

    stats = st.session_state.stats
    c1, c2, c3 = st.columns(3)
    c1.metric("분석 파일", stats["included_files"])
    c2.metric("제외 파일", stats["excluded_files"])
    c3.metric("전체 코드 문자 수", f"{stats['total_chars']:,}")

    st.markdown("#### 주요 언어")
    if stats["languages"]:
        st.bar_chart(stats["languages"])

    st.markdown("#### 분석 대상 파일")
    file_rows = serializable_files(st.session_state.code_files)
    st.dataframe(file_rows, use_container_width=True, hide_index=True)

    st.info(
        "보안을 위해 `.env`, credential/secret 관련 파일과 의존성 폴더는 분석에서 제외됩니다. "
        "LLM에는 비용과 컨텍스트 크기를 고려해 핵심 코드 일부만 전달됩니다."
    )

    st.markdown("#### 인터뷰 설정")
    difficulty = st.radio(
        "난이도",
        ["기초", "보통", "심화"],
        index=["기초", "보통", "심화"].index(st.session_state.interview_difficulty),
        horizontal=True,
        help=(
            "기초: 코드 역할/실행 흐름 중심 · "
            "보통: 구현 이유/변경 영향 포함 · "
            "심화: 예외 상황/의존 관계/설계 trade-off 중심"
        ),
    )
    st.session_state.interview_difficulty = difficulty
    st.session_state.adaptive_difficulty = st.checkbox(
        "답변에 따라 난이도 자동 조절",
        value=st.session_state.adaptive_difficulty,
        help="3~4점은 한 단계 상승, 2점은 유지, 0~1점은 하락합니다. 시작 난이도보다 최대 한 단계만 낮아집니다.",
    )

    file_paths = [file.path for file in st.session_state.code_files]
    selected_paths = st.multiselect(
        "질문 대상 파일",
        file_paths,
        default=st.session_state.selected_paths or file_paths,
        help="대형 프로젝트에서는 이번 인터뷰에 포함할 파일만 선택하세요.",
    )
    st.session_state.selected_paths = selected_paths

    answer_mode = st.radio(
        "참고 답안 표시 방식",
        ["인터뷰 종료 후 표시", "각 질문 답변 후 표시"],
        index=["인터뷰 종료 후 표시", "각 질문 답변 후 표시"].index(
            st.session_state.answer_mode
        ),
        horizontal=True,
    )
    st.session_state.answer_mode = answer_mode

    if answer_mode == "각 질문 답변 후 표시":
        st.caption(
            "학습 모드: 답변을 제출한 질문의 참고 답안과 핵심 포인트를 바로 확인할 수 있습니다. "
            "점수는 최초 답변을 기준으로 고정됩니다."
        )
    else:
        st.caption(
            "평가 모드: 참고 답안은 전체 인터뷰가 끝난 뒤 결과 화면에서 확인할 수 있습니다."
        )

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("← 다시 제출"):
            st.session_state.step = "submit"
            st.rerun()

    with col2:
        if st.button("질문 생성 후 인터뷰 시작", type="primary", use_container_width=True):
            if not selected_paths:
                st.error("질문 대상 파일을 하나 이상 선택해주세요.")
                st.stop()
            selected_files = [
                file for file in st.session_state.code_files if file.path in selected_paths
            ]
            st.session_state.code_context = build_llm_context(selected_files)
            with st.spinner("제출 코드에 맞는 질문을 생성하고 있습니다..."):
                try:
                    questions = generate_questions(
                        st.session_state.code_context,
                        st.session_state.interview_difficulty,
                        count=1,
                    )
                    questions[0]["selected_difficulty"] = difficulty
                    st.session_state.questions = questions
                    st.session_state.queue = list(questions)
                    st.session_state.current_index = 0
                    st.session_state.records = []
                    st.session_state.current_difficulty = difficulty
                    st.session_state.step = "interview"
                    st.rerun()
                except LLMError as exc:
                    st.error(str(exc))


elif st.session_state.step == "interview":
    queue = st.session_state.queue
    idx = st.session_state.current_index

    if idx >= len(queue):
        completed_base_questions = sum(
            not record.get("is_followup") for record in st.session_state.records
        )
        if completed_base_questions >= 5:
            st.session_state.step = "result"
            st.rerun()
        try:
            with st.spinner("다음 질문을 생성하고 있습니다..."):
                questions = generate_questions(
                    st.session_state.code_context,
                    st.session_state.current_difficulty,
                    count=1,
                    previous_questions=[record["question"] for record in st.session_state.records],
                )
                next_question = questions[0]
                next_question["selected_difficulty"] = st.session_state.current_difficulty
                st.session_state.questions.append(next_question)
                st.session_state.queue.append(next_question)
                st.rerun()
        except LLMError as exc:
            st.error(str(exc))
            if st.button("다음 질문 다시 생성"):
                st.rerun()
            st.stop()

    question = queue[idx]
    total = len(queue)

    st.subheader("코드 인터뷰")
    st.progress(min(1.0, idx / max(1, total)))
    completed_base_questions = sum(
        not record.get("is_followup") for record in st.session_state.records
    )
    st.caption(
        f"기본 질문 {min(5, completed_base_questions + 1)} / 5 · "
        f"현재 난이도: {st.session_state.current_difficulty}"
    )

    # 이전 문답
    for record in st.session_state.records:
        with st.chat_message("assistant"):
            badge = "꼬리질문" if record.get("is_followup") else record.get("difficulty", "")
            st.markdown(f"**{badge} · {record.get('target', '')}**")
            st.write(record["question"])
        with st.chat_message("user"):
            st.write(record["answer"])
        with st.expander(f"평가: {record['evaluation']['score']} / 4"):
            st.write(record["evaluation"]["reason"])

            if st.session_state.answer_mode == "각 질문 답변 후 표시":
                st.markdown("---")
                st.markdown("**참고 답안**")
                st.caption(
                    "제출 코드를 근거로 생성된 예시 답안이며, 작성자의 실제 의도와 다를 수 있습니다."
                )
                st.write(record.get("reference_answer") or "참고 답안이 생성되지 않았습니다.")

                if record.get("key_points"):
                    st.markdown("**핵심 포인트**")
                    for point in record["key_points"]:
                        st.write(f"- {point}")

    with st.chat_message("assistant"):
        label = "꼬리질문" if question.get("is_followup") else question.get("difficulty", "medium")
        target = question.get("target", "")
        st.markdown(f"**{label} · {target}**")
        st.write(question["question"])

    answer = st.chat_input("핵심 이유나 흐름을 한두 문장으로 답해도 됩니다.")

    if answer:
        with st.spinner("답변을 평가하고 있습니다..."):
            try:
                evaluation = evaluate_answer(
                    st.session_state.code_context,
                    question["question"],
                    answer,
                    question.get("reference_answer", ""),
                    question.get("key_points", []),
                    question.get("selected_difficulty", st.session_state.interview_difficulty),
                )

                record = {
                    **question,
                    "answer": answer,
                    "evaluation": evaluation,
                }
                st.session_state.records.append(record)

                # 핵심 이해가 거의 보이지 않을 때만 기본 질문당 최대 한 번 꼬리질문.
                if (
                    not question.get("is_followup")
                    and evaluation["score"] <= 1
                    and evaluation.get("need_followup", True)
                ):
                    followup_data = generate_followup(
                        st.session_state.code_context,
                        question["question"],
                        answer,
                        evaluation,
                    )
                    followup = {
                        "question": followup_data["question"],
                        "difficulty": "follow-up",
                        "selected_difficulty": question.get("selected_difficulty"),
                        "category": question.get("category", "logic"),
                        "target": question.get("target", ""),
                        "intent": "이전 답변에서 부족했던 이해도 추가 확인",
                        "reference_answer": followup_data.get("reference_answer", ""),
                        "key_points": followup_data.get("key_points", []),
                        "is_followup": True,
                    }
                    st.session_state.queue.insert(idx + 1, followup)

                if not question.get("is_followup"):
                    st.session_state.current_difficulty = next_difficulty(
                        st.session_state.current_difficulty,
                        st.session_state.interview_difficulty,
                        evaluation["score"],
                        st.session_state.adaptive_difficulty,
                    )
                st.session_state.current_index += 1
                st.rerun()
            except LLMError as exc:
                st.error(str(exc))


elif st.session_state.step == "result":
    records = st.session_state.records

    if not records:
        st.warning("평가 기록이 없습니다.")
        if st.button("처음으로"):
            reset_all()
        st.stop()

    if st.session_state.report is None:
        with st.spinner("최종 결과를 정리하고 있습니다..."):
            try:
                st.session_state.report = generate_report(records)
            except LLMError as exc:
                # 최종 요약 실패가 전체 결과 확인을 막지는 않도록 한다.
                st.session_state.report = {
                    "summary": f"LLM 최종 요약 생성 실패: {exc}",
                    "strengths": [],
                    "needs_review": [],
                }

    report = st.session_state.report
    scores = [r["evaluation"]["score"] for r in records]
    overall = score_to_100(sum(scores) / len(scores))
    categories = aggregate_categories(records)

    st.subheader("코드 이해도 결과")
    st.metric("종합 이해도", f"{overall} / 100")
    st.caption(
        "이 점수는 제출 코드에 대한 답변 이해도를 나타내며, AI 사용 여부를 판정하는 수치가 아닙니다."
    )

    if categories:
        st.markdown("#### 영역별 점수")
        label_map = {
            "structure": "구조 이해",
            "logic": "구현 로직",
            "design": "설계 의도",
            "edge_case": "예외 상황",
            "modification": "코드 수정 이해",
        }
        display_categories = {
            label_map.get(k, k): v for k, v in categories.items()
        }
        st.bar_chart(display_categories)

    st.markdown("#### 종합 요약")
    st.write(report.get("summary", ""))

    left, right = st.columns(2)
    with left:
        st.markdown("##### 확인된 강점")
        strengths = report.get("strengths", [])
        if strengths:
            for item in strengths:
                st.write(f"- {item}")
        else:
            st.caption("별도 요약 없음")

    with right:
        st.markdown("##### 추가 확인 필요")
        needs = report.get("needs_review", [])
        if needs:
            for item in needs:
                st.write(f"- {item}")
        else:
            st.caption("별도 요약 없음")

    st.markdown("#### 질문 / 답변 기록")
    for i, record in enumerate(records, 1):
        with st.expander(
            f"Q{i}. {record['question']} — {record['evaluation']['score']}/4"
        ):
            st.markdown("**답변**")
            st.write(record["answer"])
            st.markdown("**평가 이유**")
            st.write(record["evaluation"]["reason"])

            if record["evaluation"].get("understood"):
                st.markdown("**잘 이해한 내용**")
                for item in record["evaluation"]["understood"]:
                    st.write(f"- {item}")

            if record["evaluation"].get("missing"):
                st.markdown("**부족한 내용**")
                for item in record["evaluation"]["missing"]:
                    st.write(f"- {item}")

            st.markdown("---")
            st.markdown("**참고 답안**")
            st.caption(
                "제출 코드를 근거로 생성된 예시 답안입니다. "
                "하나의 절대적인 정답을 의미하지 않습니다."
            )
            st.write(record.get("reference_answer") or "참고 답안이 생성되지 않았습니다.")

            if record.get("key_points"):
                st.markdown("**핵심 포인트**")
                for point in record["key_points"]:
                    st.write(f"- {point}")

    payload = {
        "session_id": st.session_state.session_id,
        "project_name": st.session_state.project_name,
        "source_type": st.session_state.source_type,
        "source_meta": st.session_state.source_meta,
        "interview_difficulty": st.session_state.interview_difficulty,
        "adaptive_difficulty": st.session_state.adaptive_difficulty,
        "selected_paths": st.session_state.selected_paths,
        "answer_mode": st.session_state.answer_mode,
        "stats": st.session_state.stats,
        "files": serializable_files(st.session_state.code_files),
        "overall_score": overall,
        "category_scores": categories,
        "records": records,
        "report": report,
    }

    st.download_button(
        "결과 JSON 다운로드",
        data=json.dumps(payload, ensure_ascii=False, indent=2),
        file_name=f"codeviva-{st.session_state.session_id}.json",
        mime="application/json",
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("서버에 결과 저장", use_container_width=True):
            path = save_session(payload)
            st.success(f"저장 완료: {path.name}")

    with col2:
        if st.button("새 인터뷰 시작", use_container_width=True):
            reset_all()
