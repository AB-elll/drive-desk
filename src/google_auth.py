import json
import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


def get_credentials() -> Credentials:
    tokens_path = os.environ["GOOGLE_OAUTH_TOKENS_PATH"]
    keys_path = os.environ["GOOGLE_OAUTH_KEYS_PATH"]

    token = json.load(open(tokens_path))
    keys = json.load(open(keys_path))["installed"]

    creds = Credentials(
        token=token["access_token"],
        refresh_token=token["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=keys["client_id"],
        client_secret=keys["client_secret"],
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token["access_token"] = creds.token
        with open(tokens_path, "w") as f:
            json.dump(token, f)

    return creds
