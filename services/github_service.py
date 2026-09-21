from __future__ import annotations

import io
import re
import zipfile
from typing import Dict, Tuple
from urllib.parse import urlparse

import requests


GITHUB_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class GitHubError(RuntimeError):
    pass


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
    archive_response = requests.get(zip_url, headers=headers, timeout=timeout)
    if archive_response.status_code != 200:
        raise GitHubError("Repository 파일을 내려받지 못했습니다.")

    result: Dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(archive_response.content)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
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
