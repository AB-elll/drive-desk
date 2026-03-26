from .base import ProcessorPlugin, ProcessResult

# TODO: freeeアカウント・APIトークン取得後に実装
class FreeePlugin(ProcessorPlugin):
    processor_type = "freee"

    def process(self, file_id: str, extracted_data: dict) -> ProcessResult:
        raise NotImplementedError("freee plugin is not implemented yet")
