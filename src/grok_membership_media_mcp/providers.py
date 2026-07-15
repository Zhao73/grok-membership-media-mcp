from __future__ import annotations

import json
import fcntl
import os
import re
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import Settings, membership_env
from .paths import sha256_file, sniff_image_mime


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_GPT_CONFIRMED_RE = re.compile(
    r"^\[\s*\d+\.\d+s\]\s+(generating|receiving image|downloading image bytes)\b",
    re.MULTILINE,
)
_ACTIVE_PROCESS: subprocess.Popen[str] | None = None


def strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)


def diagnostic_tail(value: str, lines: int = 40) -> list[str]:
    cleaned = strip_ansi(value)
    # Redact common signed URL/query fragments before diagnostics reach a model.
    cleaned = re.sub(r"https://\S+", "<redacted-url>", cleaned)
    return cleaned.splitlines()[-lines:]


@dataclass
class ProviderFailure(RuntimeError):
    message: str
    phase: str
    submission: str = "unknown"
    retry_safe: bool = False
    code: str = "PROVIDER_FAILED"
    diagnostics: list[str] | None = None

    def __str__(self) -> str:
        return self.message


def _run(
    args: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    global _ACTIVE_PROCESS
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env or membership_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _ACTIVE_PROCESS = process
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            stdout, stderr = process.communicate()
        raise ProviderFailure(
            f"subprocess exceeded {timeout} seconds",
            phase="timeout",
            submission="unknown",
            retry_safe=False,
            code="SUBMITTED_UNKNOWN",
            diagnostics=diagnostic_tail(stderr),
        )
    finally:
        _ACTIVE_PROCESS = None
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def terminate_active_process(grace_seconds: float = 5.0) -> None:
    process = _ACTIVE_PROCESS
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=5)


@contextmanager
def _grok_membership_slot(settings: Settings):
    lock_path = settings.state_dir / "grok-membership.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def grok_membership_doctor(settings: Settings) -> dict[str, Any]:
    binary = Path(settings.grok_bin)
    result: dict[str, Any] = {
        "binary": str(binary),
        "detected": binary.is_file(),
        "logged_in": False,
        "auth_mode": "unknown",
        "api_key_auth_disabled": False,
        "developer_api_enabled": False,
    }
    if not binary.is_file():
        result["reason"] = "Grok Build binary not found"
        return result
    try:
        models = _run(
            [str(binary), "models"], cwd=Path.home(), timeout=30, env=membership_env()
        )
    except ProviderFailure as error:
        result["reason"] = str(error)
        return result
    if models.returncode != 0:
        result["reason"] = "grok models failed"
        return result
    text = strip_ansi(models.stdout + "\n" + models.stderr)
    result["logged_in"] = "logged in with grok.com" in text.lower()
    result["auth_mode"] = "grok.com_membership" if result["logged_in"] else "login_required"
    try:
        version = _run(
            [str(binary), "--version"],
            cwd=Path.home(),
            timeout=15,
            env=membership_env(),
        )
        result["version"] = strip_ansi(version.stdout).strip()
    except ProviderFailure as error:
        result["version"] = "unknown"
        result["version_error"] = str(error)
    try:
        inspect = _run(
            [str(binary), "inspect", "--json"],
            cwd=Path.home(),
            timeout=30,
            env=membership_env(),
        )
        if inspect.returncode != 0:
            raise ProviderFailure(
                "grok inspect failed",
                phase="preflight",
                submission="not_submitted",
                retry_safe=True,
                code="API_KEY_POLICY_UNVERIFIED",
            )
        payload = json.loads(inspect.stdout)
        policy = payload.get("loginPolicy") or {}
        # JSON booleans only. Strings such as "false" are truthy in Python and
        # must never make this fail-closed membership policy appear ready.
        result["api_key_auth_disabled"] = bool(
            policy.get("disableApiKeyAuth") is True
            and policy.get("apiKeyAuthDisabled") is True
        )
    except (ProviderFailure, json.JSONDecodeError) as error:
        # Fail closed: the environment requests disabled API-key auth, but the
        # doctor only reports ready when Grok itself confirms both policy bits.
        result["api_key_auth_disabled"] = False
        result["inspect_error"] = str(error)
    result["ready"] = bool(result["logged_in"] and result["api_key_auth_disabled"])
    return result


def chatgpt_membership_doctor(settings: Settings) -> dict[str, Any]:
    binary = Path(settings.chatgpt_imagegen_bin)
    result: dict[str, Any] = {
        "binary": str(binary),
        "detected": binary.is_file(),
        "backend": "web_only",
        "developer_api_enabled": False,
        "ready": False,
        "verified": False,
    }
    if not binary.is_file():
        result["reason"] = "chatgpt-imagegen binary not found"
        return result
    completed = _run(
        [str(binary), "doctor"],
        cwd=Path.home(),
        timeout=45,
        env=membership_env(),
    )
    text = strip_ansi(completed.stdout + "\n" + completed.stderr)
    version_match = re.search(r"\[(?:ok|warn)\]\s+version\s+([^\s]+)", text)
    result["version"] = version_match.group(1) if version_match else "unknown"
    web_section = text.split("web backend:", 1)[1] if "web backend:" in text else ""
    browser_driver = bool(re.search(r"\[(?:ok|warn)\]\s+chrome-use\b", web_section))
    relay = bool(re.search(r"\[ok\]\s+relay\b", web_section))
    profile = bool(re.search(r"\[ok\]\s+profiles\b", web_section))
    result.update(
        {
            "browser_driver_detected": browser_driver,
            "relay_connected": relay,
            "logged_in_profile_detected": profile,
            # A visible logged-in profile is not enough for automation. Require
            # the native relay so auto-routing can fall back before submission.
            "ready": bool(browser_driver and relay and profile),
            "reason": "preflight detection only; a real generation is the final check",
        }
    )
    return result


def generate_chatgpt_membership_image(
    settings: Settings,
    *,
    prompt: str,
    output_path: Path,
    size: str,
    refs: list[Path] | None = None,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        settings.chatgpt_imagegen_bin,
        "--backend",
        "web",
        "--profile",
        "auto",
        "--project",
        "hybrid-media-mcp",
        "--no-style",
        "--format",
        "png",
        "--size",
        size,
        "--timeout",
        str(settings.chatgpt_timeout_seconds),
        "--stall-timeout",
        str(min(120, settings.chatgpt_timeout_seconds)),
        "--quiet",
        "-o",
        str(output_path),
    ]
    for ref in refs or []:
        args.extend(["--ref", str(ref)])
    args.extend(["--", prompt])
    try:
        completed = _run(
            args,
            cwd=output_path.parent,
            timeout=settings.chatgpt_timeout_seconds + 30,
            env=membership_env(),
        )
    except FileNotFoundError as error:
        raise ProviderFailure(
            str(error),
            phase="preflight",
            submission="not_submitted",
            retry_safe=True,
            code="GPT_BROWSER_UNAVAILABLE",
        ) from error

    diagnostics = strip_ansi(completed.stderr)
    if completed.returncode != 0:
        confirmed = bool(
            _GPT_CONFIRMED_RE.search(diagnostics)
            or "The prompt was already submitted" in diagnostics
        )
        safe_phrases = (
            "`chrome-use` is not installed",
            "no logged-in ChatGPT browser available",
            "rate-limited this account ('Too many requests')",
        )
        safe = any(phrase in diagnostics for phrase in safe_phrases) and not confirmed
        raise ProviderFailure(
            "ChatGPT membership browser generation failed",
            phase="gpt_image",
            submission="confirmed" if confirmed else ("not_submitted" if safe else "unknown"),
            retry_safe=safe,
            code="GPT_BROWSER_UNAVAILABLE" if safe else "GPT_SUBMITTED_UNKNOWN",
            diagnostics=diagnostic_tail(completed.stderr),
        )

    stdout_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if stdout_lines != [str(output_path)]:
        raise ProviderFailure(
            "chatgpt-imagegen stdout/path contract changed",
            phase="gpt_validation",
            submission="confirmed",
            retry_safe=False,
            code="CLI_PROTOCOL_CHANGED",
            diagnostics=diagnostic_tail(completed.stdout + "\n" + completed.stderr),
        )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ProviderFailure(
            "chatgpt-imagegen reported success but no image was saved",
            phase="gpt_validation",
            submission="confirmed",
            retry_safe=False,
            code="NO_MEDIA_RESULT",
        )
    mime = sniff_image_mime(output_path)
    if mime != "image/png":
        raise ProviderFailure(
            "chatgpt-imagegen returned an invalid PNG",
            phase="gpt_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    return {
        "provider": "chatgpt_membership",
        "transport": "browser",
        "path": str(output_path),
        "mime_type": mime,
        "bytes": output_path.stat().st_size,
        "sha256": sha256_file(output_path),
        "submission": "confirmed",
    }


def _find_session_dir(session_id: str) -> Path | None:
    sessions = Path.home() / ".grok/sessions"
    if not sessions.is_dir():
        return None
    for candidate in sessions.glob(f"**/{session_id}"):
        if candidate.is_dir():
            return candidate
    return None


def _read_tool_result(
    session_dir: Path,
    tool_name: str,
    expected_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updates_path = session_dir / "updates.jsonl"
    if not updates_path.is_file():
        raise ProviderFailure(
            "Grok session has no updates.jsonl",
            phase="grok_result",
            submission="unknown",
            retry_safe=False,
            code="CLI_PROTOCOL_CHANGED",
        )
    calls: dict[str, dict[str, Any]] = {}
    with updates_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            update = (event.get("params") or {}).get("update") or {}
            if update.get("sessionUpdate") in {"tool_call", "tool_call_update"}:
                meta = (update.get("_meta") or {}).get("x.ai/tool") or {}
                if meta.get("name") == tool_name:
                    discovered_id = update.get("toolCallId")
                    if isinstance(discovered_id, str) and discovered_id:
                        calls.setdefault(discovered_id, {})
                        if update.get("rawInput"):
                            calls[discovered_id]["raw_input"] = update["rawInput"]
            event_call_id = update.get("toolCallId")
            if (
                isinstance(event_call_id, str)
                and event_call_id in calls
                and update.get("sessionUpdate") == "tool_call_update"
            ):
                call = calls[event_call_id]
                if update.get("rawInput"):
                    call["raw_input"] = update["rawInput"]
                if update.get("status") == "completed" and isinstance(
                    update.get("rawOutput"), dict
                ):
                    call["completed_output"] = update["rawOutput"]
                if update.get("status") == "failed":
                    call["failed_message"] = json.dumps(
                        update, ensure_ascii=False
                    )[-2000:]
    if len(calls) > 1:
        raise ProviderFailure(
            f"Grok called {tool_name} more than once",
            phase=tool_name,
            submission="confirmed",
            retry_safe=False,
            code="MULTIPLE_TOOL_CALLS",
        )
    if not calls:
        # Missing events do not prove that the membership request was never
        # submitted; a CLI/logging protocol change could hide the tool call.
        raise ProviderFailure(
            f"Grok has no verifiable {tool_name} tool event",
            phase="grok_dispatch",
            submission="unknown",
            retry_safe=False,
            code="SUBMITTED_UNKNOWN",
        )
    call_id, call = next(iter(calls.items()))
    completed_output = call.get("completed_output")
    raw_input = call.get("raw_input") or {}
    if completed_output:
        if expected_arguments is not None:
            mismatches = {
                key: {"expected": expected, "actual": raw_input.get(key)}
                for key, expected in expected_arguments.items()
                if key not in raw_input or raw_input.get(key) != expected
            }
            if mismatches:
                raise ProviderFailure(
                    "Grok media tool arguments differ from the requested arguments",
                    phase=tool_name,
                    submission="confirmed",
                    retry_safe=False,
                    code="CLI_ARGUMENT_MISMATCH",
                    diagnostics=[json.dumps(mismatches, ensure_ascii=False)],
                )
        return {
            "tool_call_id": call_id,
            "raw_input": raw_input,
            "raw_output": completed_output,
        }
    raise ProviderFailure(
        call.get("failed_message") or f"{tool_name} did not complete",
        phase=tool_name,
        submission="confirmed",
        retry_safe=False,
        code="GENERATION_FAILED",
    )


def run_grok_membership_tool(
    settings: Settings,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    cwd: Path,
) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    dispatcher_prompt = (
        f"Call {tool_name} exactly once with the following exact JSON arguments. "
        "Do not call any other tool and do not change any value. After it finishes, "
        f"output only the tool result JSON.\n{json.dumps(arguments, ensure_ascii=False)}"
    )
    system_prompt = (
        "You are a deterministic media tool dispatcher. Call the single allowed "
        "media tool exactly once with the exact JSON arguments in the user message. "
        "Do not reinterpret the request and do not use any other tool."
    )
    args = [
        settings.grok_bin,
        "--model",
        "grok-4.5",
        "--cwd",
        str(cwd),
        "--session-id",
        session_id,
        "--single",
        dispatcher_prompt,
        "--system-prompt-override",
        system_prompt,
        "--tools",
        tool_name,
        "--permission-mode",
        "bypassPermissions",
        "--no-subagents",
        "--no-memory",
        "--no-plan",
        "--disable-web-search",
        "--max-turns",
        "3",
        "--output-format",
        "streaming-json",
    ]
    try:
        with _grok_membership_slot(settings):
            completed = _run(
                args,
                cwd=cwd,
                timeout=settings.grok_timeout_seconds,
                env=membership_env(),
            )
    except FileNotFoundError as error:
        raise ProviderFailure(
            str(error),
            phase="preflight",
            submission="not_submitted",
            retry_safe=True,
            code="GROK_NOT_INSTALLED",
        ) from error

    session_dir = _find_session_dir(session_id)
    if session_dir is None:
        auth_text = strip_ansi(completed.stdout + "\n" + completed.stderr)
        lowered = auth_text.lower()
        auth_failure = "session has expired" in lowered or "run `grok login`" in lowered
        raise ProviderFailure(
            "Grok session directory was not created",
            phase="grok_dispatch",
            submission="not_submitted" if auth_failure else "unknown",
            retry_safe=auth_failure,
            code="AUTH_REQUIRED" if auth_failure else "SUBMITTED_UNKNOWN",
            diagnostics=diagnostic_tail(auth_text),
        )

    try:
        result = _read_tool_result(
            session_dir, tool_name, expected_arguments=arguments
        )
    except ProviderFailure as error:
        combined = strip_ansi(completed.stdout + "\n" + completed.stderr)
        lowered = combined.lower()
        if "rate limit for your plan" in lowered:
            error.code = "SUBSCRIPTION_QUOTA_EXHAUSTED"
        elif "session has expired" in lowered or "run `grok login`" in lowered:
            error.code = "AUTH_REQUIRED"
        error.diagnostics = diagnostic_tail(combined)
        raise

    if completed.returncode != 0 and not result.get("raw_output"):
        raise ProviderFailure(
            "Grok exited before returning a media result",
            phase=tool_name,
            submission="confirmed",
            retry_safe=False,
            code="SUBMITTED_UNKNOWN",
            diagnostics=diagnostic_tail(completed.stdout + "\n" + completed.stderr),
        )

    output = result["raw_output"]
    raw_path = output.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ProviderFailure(
            "Grok completed without a local media path",
            phase="grok_result",
            submission="confirmed",
            retry_safe=False,
            code="NO_MEDIA_RESULT",
        )
    media_path = Path(raw_path).expanduser().resolve()
    resolved_session_dir = session_dir.resolve()
    if not media_path.is_relative_to(resolved_session_dir):
        raise ProviderFailure(
            "Grok media result escaped its session directory",
            phase="grok_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    if not media_path.is_file() or media_path.stat().st_size == 0:
        raise ProviderFailure(
            f"Grok media path is missing or empty: {media_path}",
            phase="grok_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    expected_types = {
        "image_gen": "ImageGen",
        "image_to_video": "ImageToVideo",
        "reference_to_video": "ReferenceToVideo",
    }
    expected_type = expected_types.get(tool_name)
    if expected_type and output.get("type") != expected_type:
        raise ProviderFailure(
            f"Grok returned {output.get('type')!r} for {tool_name}",
            phase="grok_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    if tool_name == "image_gen" and sniff_image_mime(media_path) is None:
        raise ProviderFailure(
            "Grok image_gen returned a file that is not PNG, JPEG, or WebP",
            phase="grok_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    result.update(
        {
            "provider": "grok_membership",
            "transport": "grok_build_cli",
            "session_id": session_id,
            "session_dir": str(session_dir),
            "path": str(media_path),
            "bytes": media_path.stat().st_size,
            "sha256": sha256_file(media_path),
            "submission": "confirmed",
            "api_key_auth_disabled": True,
        }
    )
    return result
