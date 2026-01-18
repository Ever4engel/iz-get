# -*- coding: utf-8 -*-
import asyncio
import re
from getpass import getpass
from typing import List, Optional

import requests

from ..book_infos import BookInfos, ReadDirection
from ..config import Config
from ..tools import async_http_get, clean_attribute, clean_name, get_image_type, http_post
from .site_processor import SiteProcessor


class MangasIo(SiteProcessor):
    URL_PATTERNS: List[str] = [
        r"https://www\.mangas\.io/lire/([^/]+)/([\d\.]+)",
    ]
    cache_file: str = "TOKEN_MANGAS_IO"
    bearer: str = ""
    
    slug: str = ""
    chapter_nb: float = 0.0
    sem = asyncio.Semaphore(1) 

    def __init__(self, url: str = "", config: Optional[Config] = None) -> None:
        super().__init__(url, config)
        self.headers.update({
            "Accept": "*/*",
            "Accept-Language": "fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://www.mangas.io",
            "DNT": "1",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
        })

    @staticmethod
    def is_valid_url(url: str) -> bool:
        return any(
            re.match(pattern, url) is not None for pattern in MangasIo.URL_PATTERNS
        )

    def authenticate(self) -> None:
        self.read_token()
        while not self.is_token_valid():
            self.bearer = self.get_bearer()
        self.write_token(self.bearer)
        self.headers["authorization"] = f"Bearer {self.bearer}"

    def read_token(self):
        """Lecture du token de session depuis un fichier de cache"""
        print("Lecture du token en cache...")
        cache_folder = self.config.cache_folder or "cache"
        file_token = f"{cache_folder}/{self.cache_file}"
        try:
            with open(file_token, "r") as f:
                self.bearer = f.read()
        except FileNotFoundError:
            pass

    def write_token(self, token):
        """Ecriture du token de session dans un fichier de cache"""
        cache_folder = self.config.cache_folder or "cache"
        file_token = f"{cache_folder}/{self.cache_file}"
        import os
        os.makedirs(os.path.dirname(file_token), exist_ok=True)
        with open(file_token, "w") as f:
            f.write(token)

    def is_token_valid(self):
        """Vérifie si le token de session est encore valide"""
        print("Vérification du token...", end=" ")
        if not self.bearer:
            print("Aucun token.")
            return False
        json_data = {
            "token": self.bearer,
        }
        try:
            response = requests.post(
                "https://api.mangas.io/auth/token_validation",
                headers=self.headers,
                json=json_data,
                allow_redirects=True,
                timeout=10
            )
        except requests.RequestException:
            print("Erreur de connexion.")
            return False

        if response.status_code != 200:
            print("Erreur :", response.status_code)
            return False
        is_valid = response.json().get("status") == "success"
        print("OK" if is_valid else "Expiré")
        return is_valid

    def get_bearer(self):
        """Récupère le token de session"""
        print("Récupération d'un nouveau token...", end=" ")
        login_email = input("Email de connexion : ")
        password = getpass("Mot de passe (ne sera pas affiché lors de la saisie) : ")

        json_data = {
            "email": login_email,
            "password": password,
        }

        response = requests.post(
            "https://api.mangas.io/auth/login",
            headers=self.headers,
            json=json_data,
            allow_redirects=True,
            timeout=10
        )
        if response.status_code != 200:
            print("Erreur :", response.status_code)
            return ""
        print("OK")
        return response.json()["token"]

    def _parse_url(self):
        if not self.slug:
            res = re.search(r"https://www\.mangas\.io/lire/([^/]+)/([\d\.]+)", self.url)
            if res:
                self.slug, self.chapter_nb = res.groups()
                self.chapter_nb = float(self.chapter_nb)

    def get_book_infos(self) -> BookInfos:
        self._parse_url()
        
        json_data = {
            "operationName": "getReadingChapter",
            "variables": {
                "chapterNb": self.chapter_nb,
                "slug": self.slug,
                "quality": "HD",
            },
            "query": "query getReadingChapter($slug: String, $chapterNb: Float) {\n  manga(slug: $slug) {\n    _id\n    title\n    direction\n    authors {\n      name\n      __typename\n    }\n    volumes {\n      _id\n      number\n      description\n      chapters {\n        _id\n        title\n        number\n        __typename\n      }\n      __typename\n    }\n    chapter(number: $chapterNb) {\n      _id\n      number\n      title\n      pageCount\n      pages {\n        _id\n        number\n        __typename\n      }\n      __typename\n    }\n    __typename\n  }\n}",
        }
        
        response = http_post(
            "https://api.mangas.io/api", 
            headers=self.headers, 
            json=json_data
        )
        
        if response.status_code != 200:
            print(f"Erreur : {response.status_code}")
            return BookInfos(title="", pages=0)

        data = response.json()
        if not data or not data.get("data"):
            return BookInfos(title="", pages=0)
            
        return self._fill_infos(data)

    def _fill_infos(self, data) -> BookInfos:
        manga_data = data["data"]["manga"]
        title = manga_data["title"]
        rtl = manga_data["direction"] == "rtl"
        authors = ", ".join([author["name"] for author in manga_data["authors"]])
        
        chapter_data = manga_data["chapter"]
        chapter_id = chapter_data["_id"]
        
        volume_number = ""
        volume_desc = ""
        for v in manga_data["volumes"]:
            for c in v["chapters"]:
                if c["_id"] == chapter_id:
                    volume_number = str(v["number"])
                    volume_desc = v["description"]
                    break
        
        chapter_number = str(chapter_data["number"])
        page_count = chapter_data["pageCount"]
        
        pages_map = {}
        if chapter_data["pages"]:
            pages_map = {page["number"]: page["_id"] for page in chapter_data["pages"]}
            
        page_urls = [""] * len(pages_map) 
        
        read_direction = ReadDirection.RTOL if rtl else ReadDirection.LTOR
        
        return BookInfos(
            title=title,
            pages=page_count,
            authors=authors,
            volume=volume_number,
            chapter=chapter_number,
            description=volume_desc,
            read_direction=read_direction,
            page_urls=page_urls,
            custom_fields={"pages_map": pages_map}
        )

    async def before_download_page(self, page_num: int, url: str) -> str:
        async with self.sem:
            book_infos = self.get_book_infos()
            if not book_infos.custom_fields or "pages_map" not in book_infos.custom_fields:
                 return ""
            
            pages_map = book_infos.custom_fields["pages_map"]
            page_id = pages_map.get(page_num + 1)
            if not page_id:
                 return ""

            json_data = {
                "operationName": "getPageById",
                "variables": {
                    "id": page_id,
                    "quality": "HD",
                },
                "query": "query getPageById($id: ID!, $quality: PageType) {\n  page(id: $id) {\n    image(type: $quality) {\n      url\n    }\n  }\n}",
            }
            
            try:
                # Use asyncio.to_thread with http_post to leverage project tools and retry logic
                def fetch_url():
                    return http_post(
                        "https://api.mangas.io/api", 
                        headers=self.headers, 
                        json=json_data
                    )
                
                resp = await asyncio.to_thread(fetch_url)
                
                if resp.status_code != 200:
                    return ""
                data = resp.json()
                return data["data"]["page"]["image"]["url"]
            except Exception as e:
                print(f"Error getting page info: {e}")
                return ""


def init(url: str = "", config: Optional[Config] = None) -> MangasIo:
    return MangasIo(url, config)
