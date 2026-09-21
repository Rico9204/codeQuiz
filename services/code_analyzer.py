from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Tuple


SUPPORTED_EXTENSIONS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".jsx": "JavaScript/React",
    ".tsx": "TypeScript/React",
    ".java": "Java",
    ".c": "C",
    ".h": "C/C++ Header",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++ Header",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".php": "PHP",
    ".rb": "Ruby",
}

EXCLUDED_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".next",
    "coverage",
    "vendor",
}

EXCLUDED_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "credentials.json",
    "service-account.json",
}

SENSITIVE_NAME_PARTS = {
    "secret",
    "credential",
    "private_key",
    "id_rsa",
}

MAX_FILE_BYTES = 250_000
MAX_FILE_CHARS_FOR_LLM = 18_000
MAX_TOTAL_CHARS_FOR_LLM = 90_000


@dataclass
class CodeFile:
    path: str
    language: str
    chars: int
    content: str


def _safe_decode(data: bytes) -> str | None:
    if b"\x00" in data[:2048]:
        return None
    for enc in ("utf-8", "utf-8-sig", "cp949", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _is_excluded(path: str) -> bool:
    p = PurePosixPath(path.replace("\\", "/"))
    parts = set(p.parts)
    if parts & EXCLUDED_DIRS:
        return True

    lower_name = p.name.lower()
    if lower_name in EXCLUDED_FILENAMES:
        return True
    if any(part in lower_name for part in SENSITIVE_NAME_PARTS):
        return True
    return False


def analyze_files(raw_files: Dict[str, bytes | str]) -> Tuple[List[CodeFile], dict]:
    code_files: List[CodeFile] = []
    excluded = 0
    language_chars: Dict[str, int] = {}

    for path, raw in raw_files.items():
        normalized = path.replace("\\", "/").lstrip("/")
        suffix = PurePosixPath(normalized).suffix.lower()

        if _is_excluded(normalized) or suffix not in SUPPORTED_EXTENSIONS:
            excluded += 1
            continue

        if isinstance(raw, str):
            text = raw
            byte_len = len(raw.encode("utf-8", errors="ignore"))
        else:
            byte_len = len(raw)
            if byte_len > MAX_FILE_BYTES:
                excluded += 1
                continue
            text = _safe_decode(raw)
            if text is None:
                excluded += 1
                continue

        if not text.strip():
            continue

        language = SUPPORTED_EXTENSIONS[suffix]
        code_file = CodeFile(
            path=normalized,
            language=language,
            chars=len(text),
            content=text,
        )
        code_files.append(code_file)
        language_chars[language] = language_chars.get(language, 0) + len(text)

    # 파일이 너무 많을 때 핵심 코드 가능성이 높은 파일을 앞쪽에 둔다.
    priority_tokens = ("main", "app", "service", "controller", "api", "core", "model", "auth")
    code_files.sort(
        key=lambda f: (
            0 if any(t in f.path.lower() for t in priority_tokens) else 1,
            -f.chars,
            f.path,
        )
    )

    total_chars = sum(f.chars for f in code_files)
    total_lang_chars = sum(language_chars.values()) or 1
    language_percent = {
        lang: round(chars / total_lang_chars * 100, 1)
        for lang, chars in sorted(language_chars.items(), key=lambda x: -x[1])
    }

    stats = {
        "included_files": len(code_files),
        "excluded_files": excluded,
        "total_chars": total_chars,
        "languages": language_percent,
    }
    return code_files, stats


def build_llm_context(files: Iterable[CodeFile]) -> str:
    chunks: List[str] = []
    used = 0

    for file in files:
        remaining = MAX_TOTAL_CHARS_FOR_LLM - used
        if remaining <= 0:
            break

        content = file.content[: min(MAX_FILE_CHARS_FOR_LLM, remaining)]
        chunk = (
            f"\n===== FILE: {file.path} ({file.language}) =====\n"
            f"{content}\n"
        )
        chunks.append(chunk)
        used += len(chunk)

    return "".join(chunks)


def serializable_files(files: Iterable[CodeFile]) -> list[dict]:
    return [
        {
            "path": f.path,
            "language": f.language,
            "chars": f.chars,
        }
        for f in files
    ]
