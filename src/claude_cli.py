"""
Claude CLI ラッパー。
ANTHROPIC_API_KEY未設定の場合、claude CLIをサブプロセスで呼び出す。
"""
import logging
import subprocess
import time

logger = logging.getLogger(__name__)


def call_claude(system: str, user_text: str, model: str = "claude-sonnet-4-6",
                max_tokens: int = 2048) -> tuple[str, dict]:
    """
    テキストプロンプトをClaudeに送り (レスポンス, メタ) を返す。
    メタ: {"duration_ms": int, "prompt_preview": str, "raw_response": str}
    """
    prompt = f"<system>\n{system}\n</system>\n\n{user_text}"
    t0 = time.monotonic()
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "text"],
        capture_output=True, text=True, timeout=120,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)

    if result.returncode != 0:
        raise RuntimeError(f"claude CLI error: {result.stderr}")

    output = result.stdout.strip()
    meta = {
        "duration_ms": duration_ms,
        "prompt_preview": user_text[:300],
        "raw_response": output,
    }
    return output, meta


def call_claude_with_file(system: str, user_text: str, file_path: str,
                          mime_type: str, model: str = "claude-sonnet-4-6") -> tuple[str, dict]:
    """
    ファイル（画像/PDF）付きプロンプトをClaudeに送る。
    戻り値: (レスポンス, メタ)
    """
    prompt = (
        f"<system>\n{system}\n</system>\n\n"
        f"Read the file at {file_path} first, then respond based on its content.\n\n"
        f"{user_text}"
    )
    t0 = time.monotonic()
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", model,
         "--output-format", "text", "--allowedTools", "Read"],
        capture_output=True, text=True, timeout=120,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)

    if result.returncode != 0:
        raise RuntimeError(f"claude CLI error: {result.stderr}")

    output = result.stdout.strip()
    meta = {
        "duration_ms": duration_ms,
        "file_path": file_path,
        "mime_type": mime_type,
        "prompt_preview": user_text[:300],
        "raw_response": output,
    }
    return output, meta
