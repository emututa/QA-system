import os
from urllib.parse import quote

import httpx


class GitHubClient:
    def __init__(self) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._headers = headers

    def get_repository_resource(
        self,
        owner: str,
        repository: str,
        resource: str = "",
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        encoded_resource = quote(resource.strip("/"), safe="/")
        url = f"https://api.github.com/repos/{owner}/{repository}/{encoded_resource}".rstrip("/")
        with httpx.Client(
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            headers=self._headers,
        ) as client:
            return client.get(url, params=params)
