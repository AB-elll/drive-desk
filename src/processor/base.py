from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProcessResult:
    success: bool
    processor_type: str
    refs: list[str]       # 登録先での参照ID群
    error: str | None = None


class ProcessorPlugin(ABC):
    processor_type: str = ""   # サブクラスで定義

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def process(self, file_id: str, extracted_data: dict) -> ProcessResult:
        """抽出データを外部ツールへ登録する"""
        ...

    def validate(self, extracted_data: dict) -> tuple[bool, str | None]:
        """登録前バリデーション。(ok, error_message) を返す"""
        return True, None
