import io
import tarfile
from pathlib import PurePosixPath

import docker
import requests
from docker.errors import DockerException, ImageNotFound
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

IMAGE_TAG = "zimworx-python-test-sandbox:latest"
MAX_SOURCE_BYTES = 256 * 1024
MAX_TEST_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 20 * 1024
EXECUTION_TIMEOUT_SECONDS = 45

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
docker_client = docker.from_env()


class TestRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=500)
    source_code: str = Field(min_length=1, max_length=MAX_SOURCE_BYTES)
    test_code: str = Field(min_length=1, max_length=MAX_TEST_BYTES)


def _validate_source_path(source_path: str) -> str:
    path = PurePosixPath(source_path)
    if (
        path.is_absolute()
        or path.suffix != ".py"
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in source_path
    ):
        raise HTTPException(status_code=400, detail="Invalid Python source path.")
    return path.as_posix()


def _make_archive(source_path: str, source_code: str, test_code: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, value in (
            (f"source/{source_path}", source_code),
            ("tests/test_generated.py", test_code),
        ):
            data = value.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o444
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _build_sandbox_image() -> None:
    try:
        docker_client.images.get(IMAGE_TAG)
    except ImageNotFound:
        docker_client.images.build(path="/app/sandbox", tag=IMAGE_TAG, rm=True)


@app.on_event("startup")
def ensure_sandbox_image() -> None:
    try:
        _build_sandbox_image()
    except DockerException as exc:
        raise RuntimeError("Unable to prepare the isolated Python test image.") from exc


@app.get("/health")
def health() -> dict:
    try:
        docker_client.ping()
        docker_client.images.get(IMAGE_TAG)
    except DockerException as exc:
        raise HTTPException(status_code=503, detail="Docker runner is unavailable.") from exc
    return {"status": "ok", "sandbox_image": IMAGE_TAG}


@app.post("/run")
def run_test(request: TestRequest) -> dict:
    source_path = _validate_source_path(request.source_path)
    if len(request.source_code.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise HTTPException(status_code=413, detail="Source file exceeds the size limit.")
    if len(request.test_code.encode("utf-8")) > MAX_TEST_BYTES:
        raise HTTPException(status_code=413, detail="Test file exceeds the size limit.")

    container = None
    try:
        container = docker_client.containers.create(
            IMAGE_TAG,
            command=["python", "-m", "pytest", "-p", "no:cacheprovider", "-q", "/work/tests/test_generated.py"],
            working_dir="/work",
            environment={"PYTHONPATH": "/work/source"},
            user="10001:10001",
            network_mode="none",
            read_only=False,
            tmpfs={
                "/tmp": "rw,noexec,nosuid,nodev,size=8m,uid=10001,gid=10001,mode=1777",
            },
            mem_limit="256m",
            nano_cpus=500_000_000,
            pids_limit=64,
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
        )
        if not container.put_archive(
            "/work",
            _make_archive(source_path, request.source_code, request.test_code),
        ):
            raise HTTPException(status_code=502, detail="Could not stage test files in the sandbox.")
        container.start()
        try:
            result = container.wait(timeout=EXECUTION_TIMEOUT_SECONDS)
        except requests.exceptions.ReadTimeout as exc:
            container.kill()
            raise HTTPException(status_code=504, detail="Generated test exceeded the 45 second limit.") from exc

        logs = container.logs(stdout=True, stderr=True)
        output = logs.decode("utf-8", errors="replace")[-MAX_OUTPUT_BYTES:]
        exit_code = result.get("StatusCode", 1)
        return {
            "status": "passed" if exit_code == 0 else "failed",
            "exit_code": exit_code,
            "output": output,
        }
    except HTTPException:
        raise
    except DockerException as exc:
        raise HTTPException(status_code=503, detail="Isolated Docker test execution failed.") from exc
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except DockerException:
                pass
