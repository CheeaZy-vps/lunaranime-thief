import os
import re
import sys
import json
import base64
import hashlib

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from typing import Optional, Tuple, List, Dict, Any
from pathlib import Path
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

import requests
from requests.exceptions import HTTPError, ConnectionError, ReadTimeout, RequestException
try: from json.decoder import JSONDecodeError
except ImportError: JSONDecodeError = ValueError

class Unexpected(Exception):
    def __init__(self, message="Terjadi sesuatu yang tidak terduga"):
        self.message = message
        super().__init__(self.message)

class SmartGalleries:
    """docstring for SmartGallery"""
    def __init__(self, path: str = 'Galleries.json'):
        self.file_path = Path(path)

    def add(self, resp: dict):
        # Baca existing files
        existing = self._galleries()

        if resp.get('ok'):
            existing.update({
                resp['gallery_id']: {
                    "name": resp.get('gallery_title'),
                    "secret": resp.get('gallery_secret'),
                    "images": []
                }
            })

        elif resp.get('files'):
            for file in resp['files']:
                existing[file['gallery_id']]['images'].append(file)

        # Atomic replace
        self.file_path.write_text(
            json.dumps(existing, indent=None)
        )

    def _galleries(self) -> List[Dict]:
        if not self.file_path.exists(): return {}
        return json.loads(self.file_path.read_text())

    def exists(self, name: str) -> bool:
        for gallery_id, _gallery in self._galleries().items():
            if _gallery['name'] == name: return True

            if not _gallery.get('images'): continue
            for image in _gallery['images']:
                if image['name'] == name: return True

        return False

    def gallery_id_secret(self, name_or_id: str):
        for gallery_id, _gallery in self._galleries().items():
            if name_or_id in [gallery_id, _gallery['name']]:
                return gallery_id, _gallery['secret']

        return None, None

    def gallery(self, name: str) -> dict:
        for gallery in self._galleries():
            if gallery['name'] == name:
                return gallery
        return {}

class ImgboxUploader:
    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://imgbox.com"
        self.setup_session()

    def setup_session(self):
        """Setup session dengan headers dan cookies dasar"""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Origin': self.base_url,
            'Pragma': 'no-cache',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'X-Requested-With': 'XMLHttpRequest',
            'sec-ch-ua': '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'cookie': '_ga=GA1.1.1356635916.1775385055; _ga_C5RRS71CJH=GS2.1.s1775385056$o1$g1$t1775387755$j60$l0$h0; _ga_07EBSZY3NQ=GS2.1.s1775385055$o1$g1$t1775387755$j32$l0$h0; request_method=POST; _imgbox_session=a1JNSlBNcHB6MHZka2VMSzEyeFdQYlpZWnVEaDY2RVZBaDJuSTlEWGhJRjlwN3BVU0VsbWdKZWUxT0ZpQmRDK0ZhRUorSWlHVmlGbk1kMUdleDV0UldGd1Ard3JtOFVMdkZSck5TcDFYdGo1eE9mQlpwam5uYmF5ZC91RFAwaXYvTGRoRGJoTllvamVQclhOOUFvV2RTem5YMTliWUNpOHRGUW0vd1hlL0U0YW1adzVRUThLZHJkVWtCTHBmRFdscjBnV2lUUFk2bXVicHpaOGFrTzMycHZCTERrc3pnNy91cFN4SEtCMmF3bz0tLXNxQ001SUFNNDN6WnlrLzRNVit2Tnc9PQ%3D%3D--dd2857359309da094415aa5d03c22c00107c5222'
        })
        self.csrf_token = self.get_csrf_token()
        if not self.csrf_token: raise Exception("CSRF token tidak ditemukan")
        self.session.headers['X-CSRF-Token'] = self.csrf_token

    def get_csrf_token(self):
        """Ambil CSRF token dari halaman utama"""
        response = self.session.get(self.base_url)
        soup = BeautifulSoup(response.text, 'html.parser')

        # Cari meta tag CSRF token
        csrf_token = soup.find('meta', {'name': 'csrf-token'})
        if csrf_token: return csrf_token.get('content')

        # Fallback: cari di script tags
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'csrf' in script.string.lower():
                match = re.search(r'"([A-Za-z0-9+/=]{44})"', script.string)
                if match: return match.group(1)
        return None

    def create_new_gallery(self, gallery_title: str, comments_enabled: int = 0) -> Tuple[str, str]:
        """Buat gallery baru dan return gallery_id, gallery_secret"""
        if galleries.exists(gallery_title):
            return galleries.gallery_id_secret(gallery_title)

        response = self.session.post(
            urljoin(self.base_url, '/ajax/token/generate'),
            data={
                'gallery': 'true',
                'gallery_title': gallery_title,
                'comments_enabled': str(comments_enabled)
            }
        )

        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                result['gallery_title'] = gallery_title
                galleries.add(result)
                return result['gallery_id'], result['gallery_secret']
        raise Exception(f"Gagal membuat gallery: {response.text}")

    def get_upload_token(self) -> Tuple[int, str]:
        """Dapatkan upload token untuk single upload"""
        response = self.session.post(
            urljoin(self.base_url, '/ajax/token/generate'),
            headers={'Content-Length': '0'}
        )

        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                return data['token_id'], data['token_secret']
        raise Exception(f"Gagal mendapatkan token: {response.text}")

    def upload_bytes(self, file_name: str, _bytes: bytes, gallery_id: Optional[str] = None, 
                   gallery_secret: Optional[str] = None, comments_enabled: int = 0, 
                   thumbnail_size: str = "300r") -> Dict[str, Any]:
        """
        Upload file ke imgbox
        """

        # Step 1: Dapatkan token
        token_id, token_secret = self.get_upload_token()

        # Step 2: Upload file
        files = {'files[]': (file_name, _bytes, 'image/jpeg')}

        data = {
            'token_id': str(token_id),
            'token_secret': token_secret,
            'content_type': '1',
            'thumbnail_size': thumbnail_size,
            'comments_enabled': str(comments_enabled)
        }

        # Tambahkan gallery info jika ada
        if gallery_id: data['gallery_id'] = gallery_id
        if gallery_secret: data['gallery_secret'] = gallery_secret

        response = self.session.post(
            urljoin(self.base_url, '/upload/process'),
            files=files,
            data=data
        )

        if response.status_code == 200:
            result = response.json()
            if result.get('files'):
                galleries.add(result)
                return result['files'][0]

        raise Exception(f"Upload gagal: {response.text}")

class Lunaranime:
    """docstring for Lunaranime"""
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass
    def __init__(self, auth_token:str = None, secret_key:str = None):
        self.base_url = "https://api.lunaranime.ru/api"
        self.session = requests.Session()
        self.session.headers.update({
            'accept': '*/*',
            'accept-language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
            'cache-control': 'no-cache',
            'dnt': '1',
            'origin': 'https://lunaranime.ru',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://lunaranime.ru/',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
        })
    
        # Tambahkan Authorization header jika token disediakan
        if auth_token: self.session.headers['authorization'] = auth_token
        self.secret_key = secret_key

        self.resp_path = Path('response-body.txt')
    
    def fetch(self, endpoint, post=None, timeout=(30, 30), get_content=False):
        href = self.base_url + endpoint

        max_retries = 3
        for attempt in range(max_retries):
            try:
                if post: response = self.session.post(href, data=post, timeout=timeout)
                else: response = self.session.get(href, timeout=timeout)

                if response.status_code == 200:
                    if get_content: return response.content
                    try: return response.json()
                    except JSONDecodeError: return response.text
                    return {"raw": response.text}
                else: return {"error": f"HTTP {response.status_code}", "status": response.status_code}

            except (ConnectionError, ReadTimeout) as e:
                if attempt < max_retries - 1: time.sleep(2)
                else: raise Unexpected(f"Connection failed after {max_retries} attempts: {e}")
            except Exception as e: raise Unexpected(f"An unexpected error occurred: {e}")
        return {"error": "Max retries reached"}

    def get_manga_chapters(self, slug:str = None):
        resp = self.fetch(f'/manga/{slug}')
        self.resp_path.write_bytes(json.dumps(resp, indent=None).encode('utf-8'))

        print(f"Total chapters: {resp['count']}")
        print(f"Slug: {resp['slug']}")
        print(f"Message: {resp['message']}")
        return resp

    def get_manga_chapter_detail(self, slug:str, chapter:str, language:str = 'id'):
        resp = self.fetch(f'/manga/{slug}/{chapter}?language={language}')
        self.resp_path.write_bytes(json.dumps(resp, indent=None).encode('utf-8'))

        if not resp.get('data'): return None

        decrypted = self.decrypt_session_data(resp['data']['session_data'])
        resp.update(decrypted)
        chapter_data = resp['data']

        print(f"📖 Chapter: {resp['chapter']}")
        print(f"📚 Slug: {resp['slug']}")
        print(f"💬 Message: {resp['message']}\n")
        print(f"Title: {chapter_data['chapter_title']} | View Count: {chapter_data['view_count']}")
        return chapter_data

    def decrypt_session_data(self, session_data:str):
        """Enhanced decryption with debugging"""
        key = hashlib.sha256(self.secret_key.encode()).digest()
        try: raw_data = base64.b64decode(session_data)
        except:
            print("❌ Base64 decode FAILED")
            raise Unexpected("Invalid base64 data")

        # Decrypt
        cipher = AES.new(key, AES.MODE_CBC, b'\x00'*16)
        decrypted_padded = cipher.decrypt(raw_data)
        decrypted = unpad(decrypted_padded, AES.block_size)

        # Parse JSON
        decrypted_text = decrypted.decode('utf-8')
        return json.loads(decrypted_text)
    
    def download_image(self, endpoint):
        response = requests.get(endpoint, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://lunaranime.ru/',
            'Accept': 'image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        })
        if response.status_code == 200:
            return response.content
        return None

    def download_chapter_images(self, slug:str, chapter_data:dict):
        """
        Download semua gambar chapter ke folder lokal
        """
        if not chapter_data or not chapter_data.get('images'):
            print("❌ No image data available")
            return

        chapter_num = chapter_data['chapter_number']
        language = chapter_data['language']

        gallery_id, gallery_secret = uploader.create_new_gallery(
            gallery_title=f'{slug}-{chapter_num:0>3}.{language}',
            comments_enabled=0
        )

        success_count = 0
        for i, image_url in enumerate(chapter_data['images'], start=1):
            filename = Path(image_url).name
            if galleries.exists(filename): continue

            try:
                # Download image
                img_content = self.download_image(image_url)
                if isinstance(img_content, bytes):
                    
                    uploader.upload_bytes(
                        file_name=filename,
                        _bytes=img_content,
                        gallery_id=gallery_id,
                        gallery_secret=gallery_secret,
                        comments_enabled=0
                    )

                    print(f"  ✅ {i:2d}/{len(chapter_data['images'])} {filename}")
                    success_count += 1

            except Exception as e: print(f"  ❌ Error downloading {image_url}: {e}")
            break

        print(f"\n🎉 Download completed: {success_count}/{len(chapter_data['images'])} images")



uploader = ImgboxUploader()
galleries = SmartGalleries()

auth_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMzVmMWFjMS00ZmQyLTQ2ODktODhjZi1mZWI0YzMxNWVmYjUiLCJleHAiOjE3NzU4NjI2OTQsImlhdCI6MTc3MzI3MDY5NCwicm9sZSI6InVzZXIifQ.nhhA_urTLhVnQwjNPLJxK-B28vXREq5Li3D1uz2zHrI"
secret_key = "QQaIqW9NMZ03SftZVQJqcSdQEkAbZ3jPiAVhPI9wGqN5Oc8Fc6HOK6iu856GZ9hUdzxgAgc02XtqRVj4k5tICZYc2udYr"
lunar = Lunaranime(auth_token, secret_key)

slug = 'no-longer-a-heroine'

file_path = Path(f'chapters-{slug}.json')
try: _chapters = json.load(open(file_path, 'r'))
except: _chapters = {}
__import__('atexit').register(lambda:json.dump(_chapters, open(file_path, 'w'), indent=None))

for ch in lunar.get_manga_chapters(slug)['data']:
    if ch['language'] != 'id': continue
    chapter_data = lunar.get_manga_chapter_detail(slug, ch['chapter_number'])
    _chapters.update({ch['chapter']: chapter_data})

    lunar.download_chapter_images(slug, chapter_data)
    break