#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive"]


def service():
    info = json.loads(os.environ["GDRIVE_SERVICE_ACCOUNT_JSON"])
    credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def create_folder(api, name: str, parent_id: str) -> str:
    result = api.files().create(
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        },
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return result["id"]


def upload_tree(api, root: Path, parent_id: str) -> None:
    folders = {Path("."): parent_id}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        parent = folders[relative.parent]
        if path.is_dir():
            folders[relative] = create_folder(api, path.name, parent)
            continue
        media = MediaFileUpload(
            str(path),
            mimetype=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            resumable=True,
        )
        api.files().create(
            body={"name": path.name, "parents": [parent]},
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        ).execute()


def download(api, file_id: str, destination: Path) -> None:
    request = api.files().get_media(fileId=file_id, supportsAllDrives=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("file_id")
    download_parser.add_argument("destination", type=Path)
    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument("directory", type=Path)
    upload_parser.add_argument("--parent-id", required=True)
    upload_parser.add_argument("--folder-name", required=True)
    args = parser.parse_args()
    api = service()
    if args.command == "download":
        download(api, args.file_id, args.destination)
    else:
        release_folder = create_folder(api, args.folder_name, args.parent_id)
        upload_tree(api, args.directory, release_folder)
        print(release_folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
