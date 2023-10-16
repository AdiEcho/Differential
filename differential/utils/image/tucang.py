import json
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

from differential.utils.image import ImageUploaded


def tucang_upload(img: Path, token: str) -> Optional[ImageUploaded]:
    data = {'token': token, "folderId": 2128}
    files = {'file': open(img, 'rb')}
    req = requests.post(f'https://tucang.cc/api/v1/upload', data=data, files=files)

    try:
        res = req.json()
        logger.trace(res)
    except json.decoder.JSONDecodeError:
        res = {}
    if not req.ok:
        logger.trace(req.content)
        logger.warning(
            f"上传图片失败: HTTP {req.status_code}, reason: {req.reason} "
            f"{res.get('msg') if 'msg' in res else ''}")
        return None
    if int(res.get('code')) > 200:
        logger.warning(f"上传图片失败: [{res.get('code')}]{res.get('msg')}")
        return None
    return ImageUploaded(res.get('data', {}).get('url'))
