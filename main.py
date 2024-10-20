import asyncio
import logging
import os
import re
from typing import Optional, Tuple, List, Dict
from urllib.parse import urlparse, parse_qs

import yt_dlp
from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile
from dotenv import load_dotenv
from aiogram.client.session.aiohttp import AiohttpSession

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')

session = AiohttpSession(
    api=TelegramAPIServer.from_base('http://localhost:8081')
)
LOCAL_API_URL = 'http://localhost:8081/bot'

logging.basicConfig(level=logging.INFO)

dp = Dispatcher()
bot = Bot(token=TOKEN, base_url=LOCAL_API_URL,session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# Output directory for downloads
OUTPUT_PATH = "downloads"
os.makedirs(OUTPUT_PATH, exist_ok=True)

# Store video information between callbacks
video_info_cache: Dict[str, dict] = {}


def extract_video_id(url: str) -> Optional[str]:
    """Extract video ID from various YouTube URL formats"""
    logger.debug(f"Extracting video ID from URL: {url}")
    url = re.sub(r'\?si=.*', '', url)
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)

    if 'v' in query_params:
        return query_params['v'][0]

    patterns = [r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
                r'(?:youtube\.com\/embed\/)([0-9A-Za-z_-]{11})']

    for pattern in patterns:
        if match := re.search(pattern, parsed_url.path):
            return match.group(1)

    return None


def format_size(bytes: int) -> str:
    """Convert bytes to human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024
    return f"{bytes:.1f} GB"


class VideoDownloader:
    def __init__(self, url: str, output_path: str = OUTPUT_PATH):
        self.url = url
        self.output_path = output_path
        self.title = None
        self.video_id = None
        self.best_format = None
        self.ydl_opts = {
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [self.progress_hook],
            'outtmpl': os.path.join(self.output_path, '%(title)s.%(ext)s'),
            'merge_output_format': 'mp4',
        }

    def progress_hook(self, d):
        if d['status'] == 'downloading':
            if 'total_bytes' in d and 'downloaded_bytes' in d:
                progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                logger.debug(f"Download progress: {progress:.1f}%")
        elif d['status'] == 'finished':
            logger.debug('Download completed')

    async def get_video_info(self) -> Tuple[bool, str, Optional[dict]]:
        try:
            self.video_id = extract_video_id(self.url)
            if not self.video_id:
                return False, "Video ID ni ajratishda xatolik", None

            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                self.title = info.get('title')

                # Select a mid-range resolution format (720p preferred, fall back if not available)
                self.best_format = next((f for f in info.get('formats', [])
                                         if f.get('resolution') == '720p'
                                         and f.get('vcodec') != 'none'
                                         and f.get('acodec') != 'none'), None)

                if not self.best_format:
                    # Fall back to other available formats if 720p is not found
                    self.best_format = next((f for f in info.get('formats', [])
                                             if f.get('vcodec') != 'none'
                                             and f.get('acodec') != 'none'), None)

                return True, "", {'title': self.title, 'url': self.url}

        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            return False, f"Video ma'lumotlarini olishda xatolik: {str(e)}", None

    async def download(self) -> Tuple[bool, str, Optional[str]]:
        """Download the video in the selected best format"""
        try:
            if not self.best_format:
                return False, "Eng yaxshi format topilmadi", None

            self.ydl_opts.update({
                'format': f'{self.best_format.get("format_id", "best")}',
                'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
            })

            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
                file_path = ydl.prepare_filename(info)

                if not file_path.endswith('.mp4'):
                    file_path = os.path.splitext(file_path)[0] + '.mp4'

                if not os.path.exists(file_path):
                    return False, "Video yuklab olinmadi", None

                return True, "Success", file_path

        except Exception as e:
            logger.error(f"Download error: {str(e)}")
            return False, f"Yuklab olishda xatolik: {str(e)}", None


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    """Handle the /start command"""
    await message.answer(f"Salom, {html.bold(message.from_user.full_name)}!\n"
                         "YouTube video havolasini yuboring.")


@dp.message(lambda message: message.text and "youtu" in message.text)
async def handle_youtube_url(message: Message):
    url = message.text.strip()
    video_id = extract_video_id(url)

    if not video_id:
        await message.answer("❌ Yaroqsiz URL")
        return

    await message.answer("🔍 Video ma'lumotlari olinmoqda...")

    downloader = VideoDownloader(url)
    success, error_message, video_info = await downloader.get_video_info()

    if not success:
        await message.answer(f"❌ Xatolik: {error_message}")
        return

    video_info_cache[video_id] = video_info

    await message.answer(f"📹 {video_info['title']}\n⏳ Video yuklab olinmoqda...")

    # Automatically download the best format
    success, message_text, file_path = await downloader.download()

    if not success or not file_path:
        await message.answer(f"❌ Xatolik: {message_text}")
        return

    try:
        await message.answer("📤 Telegram'ga yuklanmoqda...")

        # Use FSInputFile for direct upload from local server
        video = FSInputFile(file_path)
        await bot.send_video(
            chat_id=message.chat.id,
            video=video,
            caption=f"📹 {video_info['title']}",
        )

    except Exception as e:
        error_message = f"Video yuborishda xatolik: {str(e)}"
        logger.error(f"Error sending video: {str(e)}")
        await message.answer(f"❌ {error_message}")
    finally:
        os.remove(file_path)  # Clean up the file


async def main():
    # result:bool = await bot.log_out()
    # print(result)
    # result = await bot.close()
    # print(result)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
