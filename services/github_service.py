from __future__ import annotations

import io
import os
import re
import zipfile
from typing import Dict, Tuple
from urllib.parse import urlparse

import requests


GITHUB_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_ARCHIVE_BYTES = int(os.getenv("CODEVIVA_MAX_GITHUB_ARCHIVE_BYTES", "20000000"))
MAX_ARCHIVE_FILES = int(os.getenv("CODEVIVA_MAX_GITHUB_ARCHIVE_FILES", "1000"))
MAX_UNCOMPRESSED_BYTES = int(os.getenv("CODEVIVA_MAX_GITHUB_UNCOMPRESSED_BYTES", "50000000"))


class GitHubError(RuntimeError):
    pass


def _download_archive(url: str, headers: dict, timeout: int) -> bytes:
    response = requests.get(url, headers=headers, timeout=timeout, stream=True)
    if response.status_code != 200:
        raise GitHubError("Repository 파일을 내려받지 못했습니다.")
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_ARCHIVE_BYTES:
        raise GitHubError("Repository 압축 파일이 허용 크기를 초과합니다.")

    chunks = []
    downloaded = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        downloaded += len(chunk)
        if downloaded > MAX_ARCHIVE_BYTES:
            raise GitHubError("Repository 압축 파일이 허용 크기를 초과합니다.")
        chunks.append(chunk)
    return b"".join(chunks)


def parse_github_url(url: str) -> Tuple[str, str]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "github.com",
        "www.github.com",
    }:
        raise GitHubError("github.com의 공개 Repository URL만 입력할 수 있습니다.")

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise GitHubError("Repository URL 형식이 올바르지 않습니다.")

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    if not GITHUB_RE.fullmatch(owner) or not GITHUB_RE.fullmatch(repo):
        raise GitHubError("Repository 이름을 확인해주세요.")

    return owner, repo


def fetch_public_repository(url: str, timeout: int = 20) -> Tuple[Dict[str, bytes], dict]:
    owner, repo = parse_github_url(url)

    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CodeViva-Prototype",
    }

    response = requests.get(api_url, headers=headers, timeout=timeout)
    if response.status_code == 404:
        raise GitHubError("공개 Repository를 찾을 수 없습니다.")
    if response.status_code != 200:
        raise GitHubError(f"GitHub 조회 실패: HTTP {response.status_code}")

    info = response.json()
    default_branch = info.get("default_branch") or "main"

    zip_url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{default_branch}"
    archive_bytes = _download_archive(zip_url, headers, timeout)

    result: Dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if len(names) > MAX_ARCHIVE_FILES:
            raise GitHubError("Repository 파일 수가 허용 범위를 초과합니다.")
        if sum(info.file_size for info in zf.infolist()) > MAX_UNCOMPRESSED_BYTES:
            raise GitHubError("Repository 압축 해제 크기가 허용 범위를 초과합니다.")
        root_prefix = names[0].split("/", 1)[0] + "/" if names else ""

        for name in names:
            # 압축을 디스크에 풀지 않고 메모리에서만 읽는다.
            relative = name[len(root_prefix):] if name.startswith(root_prefix) else name
            if not relative or relative.startswith("../"):
                continue
            try:
                result[relative] = zf.read(name)
            except Exception:
                continue

    meta = {
        "owner": owner,
        "repo": repo,
        "default_branch": default_branch,
        "html_url": info.get("html_url", url),
        "description": info.get("description") or "",
    }
    return result, meta
