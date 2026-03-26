import logging
import os
import tempfile
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from google_auth import get_credentials
from metadata_store import get_watcher_state, is_processed, set_watcher_state, upsert_file

logger = logging.getLogger(__name__)

MIME_SKIP = {
    "application/vnd.google-apps.folder",
    "application/vnd.google-apps.shortcut",
}


class Watcher:
    def __init__(self, config: dict):
        self.folder_id = config["drive"]["folder_id"]
        self.include_subfolders = config["drive"].get("include_subfolders", True)
        self._drive = build("drive", "v3", credentials=get_credentials())

    def poll(self) -> list[dict]:
        """新規・更新ファイルを返す。ファイルはローカルにダウンロード済み。"""
        page_token = get_watcher_state("page_token")

        if page_token is None:
            page_token = self._get_start_token()
            set_watcher_state("page_token", page_token)
            logger.info("Initialized page token.")
            return []

        new_files = []
        while True:
            response = self._drive.changes().list(
                pageToken=page_token,
                fields="nextPageToken,newStartPageToken,changes(fileId,file(id,name,mimeType,parents,modifiedTime,trashed))",
                includeRemoved=False,
                spaces="drive",
            ).execute()

            for change in response.get("changes", []):
                file = change.get("file")
                if not file or file.get("trashed"):
                    continue
                if file["mimeType"] in MIME_SKIP:
                    continue
                if not self._is_in_target_folder(file):
                    continue
                if is_processed(file["id"]):
                    continue

                shared_at = file.get("modifiedTime", datetime.now(timezone.utc).isoformat())
                upsert_file(file["id"], file_name=file["name"], shared_at=shared_at)

                local_path = self._download(file)
                if local_path:
                    new_files.append({
                        "file_id": file["id"],
                        "file_name": file["name"],
                        "mime_type": file["mimeType"],
                        "shared_at": shared_at,
                        "local_path": local_path,
                    })

            if "newStartPageToken" in response:
                set_watcher_state("page_token", response["newStartPageToken"])
                break
            page_token = response["nextPageToken"]
            set_watcher_state("page_token", page_token)

        return new_files

    def _get_start_token(self) -> str:
        resp = self._drive.changes().getStartPageToken().execute()
        return resp["startPageToken"]

    def _is_in_target_folder(self, file: dict) -> bool:
        parents = file.get("parents", [])
        if self.folder_id in parents:
            return True
        if self.include_subfolders:
            return self._is_descendant(parents)
        return False

    def _is_descendant(self, parents: list[str]) -> bool:
        visited = set()
        queue = list(parents)
        while queue:
            parent_id = queue.pop()
            if parent_id in visited:
                continue
            visited.add(parent_id)
            if parent_id == self.folder_id:
                return True
            try:
                meta = self._drive.files().get(
                    fileId=parent_id, fields="parents"
                ).execute()
                queue.extend(meta.get("parents", []))
            except Exception:
                pass
        return False

    def _download(self, file: dict) -> str | None:
        try:
            suffix = self._ext(file["mimeType"])
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            request = self._drive.files().get_media(fileId=file["id"])
            downloader = MediaIoBaseDownload(tmp, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            tmp.close()
            logger.info(f"Downloaded: {file['name']} -> {tmp.name}")
            return tmp.name
        except Exception as e:
            logger.error(f"Download failed for {file['name']}: {e}")
            return None

    def _ext(self, mime_type: str) -> str:
        mapping = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/heic": ".heic",
            "application/pdf": ".pdf",
            "text/plain": ".txt",
            "text/csv": ".csv",
        }
        return mapping.get(mime_type, "")

    def get_folder_path(self, file_id: str) -> str:
        """ファイルのフォルダパスを文字列で返す（分類ヒントとして使用）"""
        try:
            meta = self._drive.files().get(
                fileId=file_id, fields="parents"
            ).execute()
            parts = []
            parent_id = meta.get("parents", [None])[0]
            while parent_id and parent_id != self.folder_id:
                p = self._drive.files().get(
                    fileId=parent_id, fields="name,parents"
                ).execute()
                parts.insert(0, p["name"])
                parent_id = p.get("parents", [None])[0]
            return "/".join(parts) if parts else "/"
        except Exception:
            return "/"
