"""Bounded HTTP requests for Eitaayar and Eitaa public pages.

This is distinct from the future authenticated account transport.
"""
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

TIMEOUT = (10, 30)
PUBLIC_BASE = "https://eitaa.com/"


class EitaaBackend:
    def __init__(self, token):
        self.token = token

    @staticmethod
    def _page(username, before=None):
        response = requests.get(urljoin(PUBLIC_BASE, username),
                                params={"before": before} if before is not None else None,
                                timeout=TIMEOUT)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    @staticmethod
    def _parse_messages(soup):
        messages = []
        for bubble in soup.select(".etme_widget_message_bubble"):
            meta = bubble.select_one(".etme_widget_message_meta a[href]")
            if meta is None:
                continue
            match = re.search(r"/(\d+)(?:[?#].*)?$", meta["href"])
            if match is None:
                continue
            text = bubble.select_one(".etme_widget_message_text")
            photo = bubble.select_one(".etme_widget_message_photo_wrap")
            image = None
            if photo:
                image_match = re.search(r"url\(['\"]?([^)'\"]+)['\"]?\)", photo.get("style", ""))
                if image_match:
                    image = urljoin(PUBLIC_BASE, image_match.group(1))
            views = bubble.select_one(".etme_widget_message_views")
            timestamp = meta.find("time")
            messages.append({
                "message_number": int(match.group(1)),
                "text": text.get_text(separator="\n", strip=True) if text else "",
                "image_link": image,
                "views": views.get_text(strip=True) if views else None,
                "iso_time": timestamp.get("datetime") if timestamp else None,
            })
        return messages

    @classmethod
    def get_latest_messages(cls, username):
        return cls._parse_messages(cls._page(username))

    @classmethod
    def get_message(cls, username, message_id):
        for message in cls._parse_messages(cls._page(username, before=message_id + 1)):
            if message["message_number"] == message_id:
                return message
        return None

    @classmethod
    def get_info(cls, username):
        soup = cls._page(username)
        title = soup.select_one(".etme_channel_info_header_title")
        is_channel = title is not None
        title = title if title is not None else soup.select_one(".etme_page_title")
        if title is None:
            return None
        image = soup.select_one("img.etme_page_photo_image, .etme_page_photo_image img")
        description = soup.select_one(".etme_channel_info_description, .etme_page_description")
        users = soup.select_one(".counter_value")
        return {
            "name": " ".join(title.get_text(" ", strip=True).split()),
            "image_url": urljoin(PUBLIC_BASE, image["src"]) if image and image.get("src") else None,
            "is_verified": soup.select_one(".verified-icon") is not None,
            "is_channel": is_channel,
            "users": users.get_text(strip=True) if users else None,
            "description": description.get_text("\n", strip=True) if description else None,
        }

    def _post(self, method, data, files=None):
        response = requests.post(f"https://eitaayar.ir/api/{self.token}/{method}",
                                 data=data, files=files, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()

    def send_message(self, chat_id, text, reply_to_message_id=None):
        return self._post("sendMessage", {
            "chat_id": chat_id, "text": text,
            "reply_to_message_id": reply_to_message_id if reply_to_message_id is not None else "",
        })

    def send_file(self, chat_id, file, caption="", reply_to_message_id=None):
        # Keep the handle alive until the request completes, close it on all exits.
        with open(file, "rb") as stream:
            return self._post("sendFile", {
                "chat_id": chat_id, "caption": caption,
                "reply_to_message_id": reply_to_message_id if reply_to_message_id is not None else "",
            }, files={"file": stream})
