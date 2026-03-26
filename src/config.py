import os
import yaml
from dotenv import load_dotenv
from pathlib import Path


def load_config(client_id: str) -> dict:
    client_dir = Path(__file__).parent.parent / "clients" / client_id
    env_path = client_dir / ".env"
    config_path = client_dir / "drivedesk.config.yml"

    if env_path.exists():
        load_dotenv(env_path)

    with open(config_path) as f:
        raw = f.read()

    # 環境変数を展開
    for key, value in os.environ.items():
        raw = raw.replace(f"${{{key}}}", value)

    return yaml.safe_load(raw)
