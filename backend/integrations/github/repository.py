import base64
import binascii
import re
from urllib.parse import urlparse

from django.core.exceptions import ValidationError

from .client import GitHubClient

MAX_SOURCE_FILE_BYTES = 256 * 1024
MAX_SOURCE_FILE_PATH_LENGTH = 500
MAX_DETECTED_TEST_FILES = 100


class GitHubAPIError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        if status_code == 404:
            message = "The requested GitHub repository resource was not found."
        elif status_code in {401, 403}:
            message = "GitHub denied access to the requested repository resource."
        elif status_code == 429:
            message = "GitHub rate limit reached while retrieving the repository resource."
        else:
            message = "GitHub could not provide the requested repository resource."
        super().__init__(message)


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


def _detect_test_frameworks(paths: list[str]) -> list[str]:
    lower_paths = {path.lower() for path in paths}
    basenames = {path.rsplit("/", 1)[-1] for path in lower_paths}
    frameworks = []
    if "pytest.ini" in basenames or "conftest.py" in basenames:
        frameworks.append("Pytest")
    if any(re.fullmatch(r"jest\.config\.(?:[cm]?js|[cm]?ts|json)", name) for name in basenames):
        frameworks.append("Jest")
    if any(re.fullmatch(r"vitest\.config\.(?:[cm]?js|[cm]?ts|json)", name) for name in basenames):
        frameworks.append("Vitest")
    if any(re.fullmatch(r"playwright\.config\.(?:[cm]?js|[cm]?ts)", name) for name in basenames):
        frameworks.append("Playwright")
    if any(re.fullmatch(r"cypress\.config\.(?:[cm]?js|[cm]?ts)", name) for name in basenames):
        frameworks.append("Cypress")
    return frameworks


def _is_test_file(path: str) -> bool:
    filename = path.lower().rsplit("/", 1)[-1]
    return bool(
        filename.startswith(("test_", "test.", "spec."))
        or re.search(r"(?:\.test\.|\.spec\.|_test\.|_spec\.)", filename)
    )


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
    branch_response = client.get_repository_resource(
        owner,
        repository,
        f"branches/{selected_branch}",
    )
    if branch_response.status_code != 200:
        return {
            "accessible": False,
            "status_code": branch_response.status_code,
            "default_branch": metadata.get("default_branch"),
            "language": metadata.get("language"),
            "frameworks": [],
            "testing_frameworks": [],
            "languages": [],
            "test_files": [],
            "message": "Repository branch could not be inspected.",
        }

    branch_data = branch_response.json()
    tree_sha = branch_data.get("commit", {}).get("commit", {}).get("tree", {}).get("sha")
    if not tree_sha:
        return {
            "accessible": False,
            "status_code": branch_response.status_code,
            "default_branch": metadata.get("default_branch"),
            "language": metadata.get("language"),
            "frameworks": [],
            "testing_frameworks": [],
            "languages": [],
            "test_files": [],
            "message": "GitHub did not return a repository tree for the selected branch.",
        }

    tree_response = client.get_repository_resource(
        owner,
        repository,
        f"git/trees/{tree_sha}",
        params={"recursive": "1"},
    )
    if tree_response.status_code != 200:
        return {
            "accessible": False,
            "status_code": tree_response.status_code,
            "default_branch": metadata.get("default_branch"),
            "language": metadata.get("language"),
            "frameworks": [],
            "testing_frameworks": [],
            "languages": [],
            "test_files": [],
            "message": "GitHub repository file tree could not be inspected.",
        }

    tree = tree_response.json()
    paths = [
        entry["path"]
        for entry in tree.get("tree", [])
        if isinstance(entry, dict) and entry.get("type") == "blob" and isinstance(entry.get("path"), str)
    ]
    lower_paths = [path.lower() for path in paths]
    languages_response = client.get_repository_resource(owner, repository, "languages")
    language_data = languages_response.json() if languages_response.status_code == 200 else {}
    languages = (
        sorted(language_data, key=lambda name: language_data[name], reverse=True)
        if isinstance(language_data, dict)
        else []
    )
    testing_frameworks = _detect_test_frameworks(paths)
    test_files = [path for path in paths if _is_test_file(path)]
    if any(path.rsplit("/", 1)[-1] == "package.json" for path in lower_paths) and not any(
        framework in testing_frameworks for framework in ("Jest", "Vitest", "Playwright", "Cypress")
    ):
        if any(path.endswith((".js", ".jsx", ".ts", ".tsx")) and _is_test_file(path) for path in lower_paths):
            testing_frameworks.append("Node.js tests (framework not identified)")
    if any(path.endswith(".py") for path in lower_paths) and not any(
        framework in testing_frameworks for framework in ("Pytest",)
    ):
        if any(path.endswith(".py") and _is_test_file(path) for path in lower_paths):
            testing_frameworks.append("Python tests (framework not identified)")
    frameworks = list(testing_frameworks)
    if "manage.py" in {path.rsplit("/", 1)[-1] for path in lower_paths}:
        frameworks.append("Django")

    return {
        "accessible": True,
        "status_code": tree_response.status_code,
        "default_branch": metadata.get("default_branch"),
        "selected_branch": selected_branch,
        "language": metadata.get("language"),
        "languages": languages,
        "languages_status_code": languages_response.status_code,
        "languages_error": (
            "" if languages_response.status_code == 200
            else "GitHub language statistics could not be retrieved."
        ),
        "frameworks": frameworks,
        "testing_frameworks": testing_frameworks,
        "test_files": test_files[:MAX_DETECTED_TEST_FILES],
        "tree_truncated": bool(tree.get("truncated", False)),
        "message": "GitHub repository tree and language metadata inspected; repository code was not executed.",
    }


def retrieve_repository_file(repository_url: str, branch: str, file_path: str) -> dict:
    if (
        not file_path
        or len(file_path) > MAX_SOURCE_FILE_PATH_LENGTH
        or file_path.startswith("/")
        or "\\" in file_path
        or any(part in {"", ".", ".."} for part in file_path.split("/"))
        or "\x00" in file_path
    ):
        raise ValidationError("Enter a valid repository-relative file path.")

    owner, repository = parse_github_repository(repository_url)
    response = GitHubClient().get_repository_resource(
        owner,
        repository,
        f"contents/{file_path}",
        params={"ref": branch},
    )
    if response.status_code != 200:
        raise GitHubAPIError(response.status_code)

    data = response.json()
    if not isinstance(data, dict) or data.get("type") != "file":
        raise ValidationError("The requested repository path is not a file.")
    if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        raise ValidationError("GitHub did not return a supported text-file representation.")

    encoded_content = data["content"].replace("\n", "").replace("\r", "")
    if len(encoded_content) > ((MAX_SOURCE_FILE_BYTES + 2) // 3) * 4:
        raise ValidationError("The requested file exceeds the 256 KiB source retrieval limit.")
    try:
        content = base64.b64decode(encoded_content, validate=True)
        decoded_content = content.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationError("The requested file is not valid UTF-8 text.") from exc
    if len(content) > MAX_SOURCE_FILE_BYTES:
        raise ValidationError("The requested file exceeds the 256 KiB source retrieval limit.")

    return {
        "path": data.get("path", file_path),
        "sha": data.get("sha"),
        "size_bytes": len(content),
        "content": decoded_content,
    }
