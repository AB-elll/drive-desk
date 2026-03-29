"""
Organizer — 処理済みファイルをカテゴリ別フォルダへ自動移動

移動先構造:
  <監視フォルダ>/
    ✅ 処理済み/
      経理/
      労務/
      法務/
      未分類/
"""
import logging

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_auth import get_credentials

logger = logging.getLogger(__name__)

# DriveDesk category → フォルダ名
CATEGORY_FOLDER = {
    "accounting": "経理",
    "labor":      "労務",
    "legal":      "法務",
    "management": "管理書類",
    "other":      "未分類",
}

DONE_FOLDER_NAME = "✅ 処理済み"


class Organizer:
    def __init__(self, root_folder_id: str):
        self.root_folder_id = root_folder_id
        self._drive = build("drive", "v3", credentials=get_credentials())
        self._folder_cache: dict[str, str] = {}  # "name/parent_id" → folder_id

    def move(self, file_id: str, category: str):
        """ファイルを処理済みフォルダへ移動する"""
        try:
            folder_name = CATEGORY_FOLDER.get(category, "未分類")
            dest_id = self._ensure_folder(folder_name)

            # 現在の親フォルダを取得
            meta = self._drive.files().get(
                fileId=file_id, fields="parents"
            ).execute()
            current_parents = ",".join(meta.get("parents", []))

            # 移動（親を差し替え）
            self._drive.files().update(
                fileId=file_id,
                addParents=dest_id,
                removeParents=current_parents,
                fields="id,parents",
            ).execute()

            logger.info(f"Moved {file_id} → {DONE_FOLDER_NAME}/{folder_name}")

        except HttpError as e:
            logger.warning(f"Organizer move failed for {file_id}: {e}")
        except Exception as e:
            logger.warning(f"Organizer error for {file_id}: {e}")

    def _ensure_folder(self, category_folder_name: str) -> str:
        """✅ 処理済み/<category> フォルダのIDを返す（なければ作成）"""
        done_id = self._get_or_create_folder(DONE_FOLDER_NAME, self.root_folder_id)
        cat_id  = self._get_or_create_folder(category_folder_name, done_id)
        return cat_id

    def _get_or_create_folder(self, name: str, parent_id: str) -> str:
        cache_key = f"{name}/{parent_id}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        # 既存フォルダを検索
        resp = self._drive.files().list(
            q=(f"name='{name}' and '{parent_id}' in parents"
               " and mimeType='application/vnd.google-apps.folder'"
               " and trashed=false"),
            fields="files(id)",
        ).execute()

        files = resp.get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            # フォルダ作成
            folder = self._drive.files().create(
                body={
                    "name": name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                },
                fields="id",
            ).execute()
            folder_id = folder["id"]
            logger.info(f"Created folder: {name} (id={folder_id})")

        self._folder_cache[cache_key] = folder_id
        return folder_id
