from .base import ProcessorPlugin, ProcessResult

# TODO: JDL IBEXのCSVフォーマット仕様調査後に実装
class JDLCsvPlugin(ProcessorPlugin):
    processor_type = "jdl_csv"

    def process(self, file_id: str, extracted_data: dict) -> ProcessResult:
        raise NotImplementedError("jdl_csv plugin is not implemented yet")
