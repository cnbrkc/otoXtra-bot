"""
platforms/instagram.py - Instagram Platform (Post + Story)
"""

import os
import logging
import requests
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class InstagramAPIError(Exception):
    pass


class InstagramPlatform:
    def __init__(self):
        self.access_token = os.getenv("IG_ACCESS_TOKEN")
        self.ig_user_id = os.getenv("IG_USER_ID")
        self.graph_api_version = "v20.0"
        self.base_url = f"https://graph.facebook.com/{self.graph_api_version}"

        if not self.access_token or not self.ig_user_id:
            raise InstagramAPIError("IG_ACCESS_TOKEN veya IG_USER_ID eksik!")

    def _handle_response(self, response, operation):
        try:
            data = response.json()
        except:
            data = {"raw": response.text}

        if response.status_code >= 400:
            error_msg = data.get("error", {})
            raise InstagramAPIError(f"Instagram {operation} başarısız! {error_msg}")

        return data

    def create_media_container(self, image_url, caption, is_story=False):
        url = f"{self.base_url}/{self.ig_user_id}/media"
        params = {
            "image_url": image_url,
            "caption": caption if not is_story else "",
            "access_token": self.access_token
        }
        if is_story:
            params["media_type"] = "STORIES"

        response = requests.post(url, data=params, timeout=30)
        data = self._handle_response(response, "create_media_container")
        return data["id"]

    def publish_media(self, creation_id):
        url = f"{self.base_url}/{self.ig_user_id}/media_publish"
        params = {"creation_id": creation_id, "access_token": self.access_token}
        response = requests.post(url, data=params, timeout=30)
        data = self._handle_response(response, "publish_media")
        return data["id"]

    def post_to_instagram(self, image_url, caption):
        container_id = self.create_media_container(image_url, caption, is_story=False)
        return self.publish_media(container_id)

    def post_story(self, image_url):
        container_id = self.create_media_container(image_url, "", is_story=True)
        return self.publish_media(container_id)

    def post_with_story(self, image_url, post_caption):
        post_id = self.post_to_instagram(image_url, post_caption)
        try:
            story_id = self.post_story(image_url)
            return {"post_id": post_id, "story_id": story_id}
        except Exception as e:
            return {"post_id": post_id, "story_error": str(e)}
