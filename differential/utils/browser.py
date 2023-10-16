import requests
import webbrowser

from loguru import logger
from differential.constants import URL_SHORTENER_PATH


def b4gs_short(link: str):
    data = {
        "cmd": "add",
        "keyPhrase": "",
        "password": "s",
        "url": link,
    }
    req = requests.post(f"{URL_SHORTENER_PATH}", json=data)
    if req.ok:
        return f"{URL_SHORTENER_PATH}/{req.json().get('key')}"
    return link


def l2gs_short(link: str):
    data = {
        "url": link,
    }
    req = requests.post(f"{URL_SHORTENER_PATH}/create", json=data)
    if req.ok:
        return req.json().get("link")
    return link


def open_link(link: str, use_short_url: bool = False):
    if use_short_url:
        b4gs_short(link)

    try:
        browser = webbrowser.get()
    except webbrowser.Error:
        browser = None

    if browser is None or isinstance(browser, webbrowser.GenericBrowser):
        logger.info(f"未找到浏览器，请直接复制以下链接：{link}")
    else:
        browser.open(link, new=1)
        logger.info(f"如果浏览器未打开，请直接复制以下链接：{link}")
