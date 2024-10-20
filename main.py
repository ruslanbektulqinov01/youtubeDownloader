import asyncio
import logging
import os
import re
from typing import Optional, Tuple, List, Dict
from urllib.parse import urlparse, parse_qs

import yt_dlp
from aiogram import Bot, Dispatcher, html
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import CommandStart
from aiogram.types import Message, InputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
LOCAL_API_URL = os.getenv('LOCAL_API_URL', 'http://localhost:8081')

# Initialize bot with local server
local_server = TelegramAPIServer.from_base(LOCAL_API_URL)
bot = Bot(TOKEN, server=local_server)
dp = Dispatcher()

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


def create_format_button(format_info: dict) -> str:
    """Create format button text"""
    format_id = format_info.get('format_id', 'N/A')
    ext = format_info.get('ext', 'N/A')
    resolution = format_info.get('resolution', 'N/A')
    filesize = format_info.get('filesize', 0)
    filesize_str = format_size(filesize) if filesize else 'N/A'
    has_audio = "🔊" if format_info.get('acodec') != 'none' else "🔇"

    return f"{resolution} ({ext}) {has_audio} - {filesize_str}"


class VideoDownloader:
    def __init__(self, url: str, output_path: str = OUTPUT_PATH):
        self.url = url
        self.output_path = output_path
        self.title = None
        self.video_id = None
        self.formats = None
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
        """Get video information and available formats"""
        try:
            self.video_id = extract_video_id(self.url)
            if not self.video_id:
                return False, "Video ID ni ajratishda xatolik", None

            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                self.title = info.get('title')

                # Filter and sort formats
                formats = []
                seen_resolutions = set()

                for f in info.get('formats', []):
                    if f.get('vcodec') == 'none' or f.get('acodec') == 'none':
                        continue

                    resolution = f.get('resolution', 'N/A')
                    format_id = f.get('format_id', 'N/A')

                    format_key = f"{resolution}_{f.get('ext', '')}"

                    if format_key in seen_resolutions:
                        continue

                    seen_resolutions.add(format_key)
                    formats.append(f)

                formats.sort(key=lambda x: (x.get('height', 0) or 0), reverse=True)
                self.formats = formats

                return True, "", {'title': self.title, 'formats': formats, 'url': self.url}

        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            return False, f"Video ma'lumotlarini olishda xatolik: {str(e)}", None

    async def download(self, format_id: str) -> Tuple[bool, str, Optional[str]]:
        """Download the video in specified format"""
        try:
            self.ydl_opts.update({
                'format': f'{format_id}+bestaudio[ext=m4a]/best',
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


def create_format_keyboard(formats: List[dict], video_id: str) -> InlineKeyboardMarkup:
    """Create keyboard with format selection buttons"""
    keyboard = []
    for fmt in formats:
        format_id = fmt.get('format_id', 'N/A')
        button_text = create_format_button(fmt)
        keyboard.append([InlineKeyboardButton(text=button_text, callback_data=f"format_{video_id}_{format_id}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@dp.callback_query(lambda c: c.data.startswith('format_'))
async def process_format_selection(callback_query: CallbackQuery):
    try:
        _, video_id, format_id = callback_query.data.split('_')

        if video_id not in video_info_cache:
            await callback_query.answer("Xatolik: Video ma'lumotlari topilmadi!")
            return

        video_info = video_info_cache[video_id]
        await callback_query.message.edit_text("⏳ Video yuklab olinmoqda...")

        downloader = VideoDownloader(video_info['url'])
        success, message_text, file_path = await downloader.download(format_id)

        if not success or not file_path:
            await callback_query.message.edit_text(f"❌ Xatolik: {message_text}")
            return

        try:
            await callback_query.message.edit_text("📤 Telegram'ga yuklanmoqda...")

            # Use FSInputFile for direct upload from local server
            video = FSInputFile(file_path)
            await bot.send_video(
                chat_id=callback_query.message.chat.id,
                video=video,
                caption=f"📹 {video_info['title']}",
            )

            await callback_query.message.delete()

        except Exception as e:
            error_message = f"Video yuborishda xatolik: {str(e)}"
            logger.error(f"Error sending video: {str(e)}")
            await callback_query.message.edit_text(f"❌ {error_message}")
        finally:
            os.remove(file_path)  # Clean up the file
    except Exception as e:
        error_message = f"❌ Kutilmagan xatolik: {str(e)}"
        logger.error(f"Unexpected error: {str(e)}")
        await callback_query.message.edit_text(error_message)


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

    format_keyboard = create_format_keyboard(video_info['formats'], video_id)
    await message.answer(f"📹 {video_info['title']}\nFormatni tanlang:", reply_markup=format_keyboard)


async def main():
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
