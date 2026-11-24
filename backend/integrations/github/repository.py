import re
from urllib.parse import urlparse

from django.core.exceptions import ValidationError

from .client import GitHubClient


def parse_github_repository(value: str) -> tuple[str, str]:
    parsed = urlparse(value)
    segments = [part for part in parsed.path.strip("/").split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or len(segments) != 2
    ):
        raise ValidationError("Enter a GitHub repository URL like https://github.com/owner/repository")

    owner, repository = segments
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", owner) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,100}", repository
    ):
        raise ValidationError("A GitHub owner and repository name are required")
    return owner, repository


def inspect_github_repository(repository_url: str, branch: str) -> dict:
    owner, repository = parse_github_repository(repository_url)
    client = GitHubClient()
    metadata_response = client.get_repository_resource(owner, repository)
    if metadata_response.status_code != 200:
        return {
            "accessible": False,
            "status_code": metadata_response.status_code,
            "default_branch": None,
            "language": None,
            "frameworks": [],
            "test_files": [],
            "message": "Repository unavailable to the configured GitHub connection.",
        }

    metadata = metadata_response.json()
    selected_branch = branch or metadata.get("default_branch", "")
    contents_response = client.get_repository_resource(
        owner,
        repository,
        "contents",
        params={"ref": selected_branch},
    )
    if contents_response.status_code != 200:
        return {
            "accessible": False,
            "status_code": contents_response.status_code,
            "default_branch": metadata.get("default_branch"),
            "language": metadata.get("language"),
            "frameworks": [],
            "test_files": [],
            "message": "Repository branch could not be inspected.",
        }

    contents = contents_response.json()
    names = [entry.get("name", "") for entry in contents if isinstance(entry, dict)]
    lower_names = {name.lower() for name in names}
    language = metadata.get("language")
    frameworks = []
    if {"pyproject.toml", "pytest.ini", "requirements.txt"} & lower_names:
        frameworks.append("Python")
    if "package.json" in lower_names:
        frameworks.append("Node.js")
    if "manage.py" in lower_names:
        frameworks.append("Django")
    if "pyproject.toml" in lower_names and "pytest.ini" in lower_names:
        frameworks.append("Pytest")
    test_files = [
        name for name in names
        if name.lower().startswith(("test_", "tests")) or ".test." in name.lower() or ".spec." in name.lower()
    ]
    return {
        "accessible": True,
        "status_code": contents_response.status_code,
        "default_branch": metadata.get("default_branch"),
        "selected_branch": selected_branch,
        "language": language,
        "frameworks": frameworks,
        "test_files": test_files[:50],
        "message": "Repository metadata and top-level test configuration inspected; repository code was not executed.",
    }
