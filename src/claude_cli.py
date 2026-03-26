"""
Claude CLI ラッパー。
ANTHROPIC_API_KEY未設定の場合、claude CLIをサブプロセスで呼び出す。
"""
import json
import subprocess
import tempfile
import os


def call_claude(system: str, user_text: str, model: str = "claude-sonnet-4-6",
                max_tokens: int = 2048) -> str:
    """テキストプロンプトをClaudeに送り、レスポンステキストを返す。"""
    prompt = f"<system>\n{system}\n</system>\n\n{user_text}"
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "text"],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI error: {result.stderr}")
    return result.stdout.strip()


def call_claude_with_file(system: str, user_text: str, file_path: str,
                          mime_type: str, model: str = "claude-sonnet-4-6") -> str:
    """ファイル（画像/PDF）付きプロンプトをClaudeに送る。"""
    prompt = f"<system>\n{system}\n</system>\n\n{user_text}"
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", model,
         "--output-format", "text", file_path],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI error: {result.stderr}")
    return result.stdout.strip()
