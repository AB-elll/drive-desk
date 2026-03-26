from .base import ProcessorPlugin, ProcessResult


def load_processors(config: dict) -> list[ProcessorPlugin]:
    """設定ファイルからプロセッサーを動的にロードする"""
    from .freee import FreeePlugin
    from .jdl_csv import JDLCsvPlugin

    plugin_map = {
        "freee": FreeePlugin,
        "jdl_csv": JDLCsvPlugin,
    }

    plugins = []
    for proc_config in config.get("processors", []):
        proc_type = proc_config.get("type")
        cls = plugin_map.get(proc_type)
        if cls:
            plugins.append(cls(proc_config))
        else:
            raise ValueError(f"Unknown processor type: {proc_type}")
    return plugins


__all__ = ["ProcessorPlugin", "ProcessResult", "load_processors"]
