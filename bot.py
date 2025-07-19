import os
import logging
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import requests
import qbittorrentapi
from pathlib import Path
import asyncio
from datetime import datetime
import humanize
import time

# Configuration
API_ID = 10247139
API_HASH = "96b46175824223a33737657ab943fd6a"
BOT_TOKEN = "8142248256:AAFCrdKxcUYt2Ohtw0cJJ600YwoGAWjISXA"
SOURCE_CHANNEL = -1002812861775
DESTINATION_CHANNEL = -1002896336339
DOWNLOAD_DIR = "/downloads"
ENCODED_DIR = "/encoded"
FFMPEG_PATH = "/usr/bin/ffmpeg"
STATUS_UPDATE_INTERVAL = 10  # seconds

# qBittorrent config
QBITTORRENT_HOST = "http://localhost:8080"
QBITTORRENT_USER = "admin"
QBITTORRENT_PASS = "adminadmin"

# Initialize logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class AnimeEncoderBot:
    def __init__(self):
        self.app = Client(
            "anime_encoder_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN
        )
        self.active_tasks = {}
        self.status_messages = {}
        
        # Initialize qBittorrent client
        self.qbt = qbittorrentapi.Client(
            host=QBITTORRENT_HOST,
            username=QBITTORRENT_USER,
            password=QBITTORRENT_PASS
        )
        
        try:
            self.qbt.auth_log_in()
            logger.info("Connected to qBittorrent")
        except qbittorrentapi.LoginFailed as e:
            logger.error(f"qBittorrent login failed: {e}")
        
        # Register handlers
        self.app.on_message(filters.chat(SOURCE_CHANNEL))(self.handle_torrent_link)
        self.app.on_callback_query()(self.handle_callback_query)
    
    async def start(self):
        await self.app.start()
        logger.info("Bot started...")
        await self.app.idle()
    
    async def handle_torrent_link(self, client: Client, message: Message):
        text = message.text or message.caption
        if text and text.endswith('.torrent'):
            logger.info(f"Received torrent link: {text}")
            task_id = f"task_{int(time.time())}"
            self.active_tasks[task_id] = {
                'status': 'starting',
                'torrent_url': text,
                'start_time': datetime.now()
            }
            asyncio.create_task(self.process_torrent(task_id, text))
    
    async def process_torrent(self, task_id: str, torrent_url: str):
        try:
            self.active_tasks[task_id]['status'] = 'downloading_torrent'
            
            # Send initial status message
            status_msg = await self.app.send_message(
                SOURCE_CHANNEL,
                "🔄 Preparing to process torrent...",
                reply_markup=self.get_status_keyboard(task_id)
            )
            self.status_messages[task_id] = status_msg.id
            
            # Download torrent file
            torrent_file = os.path.join(DOWNLOAD_DIR, os.path.basename(torrent_url))
            await self.update_status(task_id, "⬇️ Downloading torrent file...")
            
            response = requests.get(torrent_url, stream=True)
            with open(torrent_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            await self.update_status(task_id, "🔵 Adding torrent to qBittorrent...")
            
            # Add torrent to qBittorrent with anime-specific settings
            torrent = self.qbt.torrents_add(
                torrent_files=torrent_file,
                save_path=DOWNLOAD_DIR,
                category='anime',
                is_sequential_download=True,
                is_first_last_piece_prio=True
            )
            
            # Get torrent hash
            torrent_info = self.qbt.torrents_info(torrent_hashes=torrent.hash)[0]
            self.active_tasks[task_id]['torrent_hash'] = torrent_info.hash
            self.active_tasks[task_id]['torrent_name'] = torrent_info.name
            
            # Start status update loop
            asyncio.create_task(self.update_torrent_status(task_id))
            
            # Wait for download to complete
            while True:
                torrent_info = self.qbt.torrents_info(torrent_hashes=torrent_info.hash)[0]
                if torrent_info.progress == 1:
                    break
                await asyncio.sleep(10)
            
            # Find the downloaded files
            files = self.qbt.torrents_files(torrent_info.hash)
            for file in files:
                if file.name.endswith(('.mkv', '.mp4', '.avi', '.mov')):
                    input_file = os.path.join(DOWNLOAD_DIR, file.name)
                    output_file = os.path.join(
                        ENCODED_DIR,
                        f"encoded_{Path(file.name).stem}.mkv"
                    )
                    
                    self.active_tasks[task_id].update({
                        'current_file': file.name,
                        'status': 'encoding',
                        'input_file': input_file,
                        'output_file': output_file
                    })
                    
                    # Prioritize this file if not fully downloaded
                    if file.progress < 1:
                        self.qbt.torrents_file_priority(
                            torrent_hash=torrent_info.hash,
                            file_ids=file.id,
                            priority=7  # Maximum priority
                        )
                        await self.update_status(task_id, f"⚡ Prioritizing {file.name} for encoding...")
                        while file.progress < 1:
                            await asyncio.sleep(5)
                            file = self.qbt.torrents_files(torrent_info.hash)[file.id]
                    
                    # Encode the file
                    await self.encode_video(task_id, input_file, output_file)
                    
                    # Upload to destination channel
                    await self.update_status(task_id, "☁️ Uploading to channel...")
                    await self.upload_to_channel(task_id, output_file)
                    
                    # Clean up
                    os.remove(input_file)
                    os.remove(output_file)
            
            # Remove torrent from qBittorrent (keep files)
            self.qbt.torrents_delete(torrent_hashes=torrent_info.hash, delete_files=False)
            
            # Clean up torrent file
            os.remove(torrent_file)
            
            # Mark task as complete
            await self.update_status(task_id, "✅ Processing complete!", final=True)
            del self.active_tasks[task_id]
            
        except Exception as e:
            logger.error(f"Error processing torrent: {e}")
            await self.update_status(task_id, f"❌ Error: {str(e)}", final=True)
    
    async def update_torrent_status(self, task_id: str):
        """Periodically update torrent download status"""
        while task_id in self.active_tasks:
            try:
                torrent_info = self.qbt.torrents_info(torrent_hashes=self.active_tasks[task_id]['torrent_hash'])[0]
                
                downloaded = humanize.naturalsize(torrent_info.downloaded)
                size = humanize.naturalsize(torrent_info.size)
                progress = torrent_info.progress * 100
                speed = humanize.naturalsize(torrent_info.dlspeed) + "/s"
                eta = humanize.naturaldelta(torrent_info.eta) if torrent_info.eta > 0 else "Unknown"
                
                # Get file-specific progress
                files_info = ""
                files = self.qbt.torrents_files(torrent_info.hash)
                for file in files[:3]:  # Show first 3 files
                    if file.progress > 0:
                        files_info += f"\n├─ {Path(file.name).name[:20]}...: {file.progress*100:.1f}%"
                
                status_text = (
                    "⬇️ Downloading Torrent\n\n"
                    f"📁 {torrent_info.name[:50]}\n"
                    f"📊 Progress: {progress:.1f}%\n"
                    f"🔽 Downloaded: {downloaded} / {size}\n"
                    f"⚡ Speed: {speed}\n"
                    f"⏳ ETA: {eta}\n"
                    f"📋 Files:{files_info}"
                )
                
                self.active_tasks[task_id]['torrent_status'] = {
                    'progress': progress,
                    'downloaded': downloaded,
                    'size': size,
                    'speed': speed,
                    'eta': eta
                }
                
                await self.update_status(task_id, status_text)
                await asyncio.sleep(STATUS_UPDATE_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error updating torrent status: {e}")
                await asyncio.sleep(STATUS_UPDATE_INTERVAL)
    
    async def encode_video(self, task_id: str, input_path: str, output_path: str):
        """Encode video to HEVC x265 10-bit with progress reporting"""
        command = [
            FFMPEG_PATH,
            '-i', input_path,
            '-c:v', 'libx265',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p10le',
            '-x265-params', 'profile=main10',
            '-c:a', 'copy',
            '-c:s', 'copy',
            '-progress', '-',  # Enable progress reporting
            '-nostats',       # Disable additional stats
            '-y',             # Overwrite output file
            output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Start progress monitoring
        asyncio.create_task(self.monitor_encode_progress(task_id, process))
        
        await process.wait()
        
        if process.returncode != 0:
            error = (await process.stderr.read()).decode()
            logger.error(f"Encoding failed: {error}")
            raise Exception(f"Encoding failed: {error}")
        
        logger.info(f"Successfully encoded: {output_path}")
    
    async def monitor_encode_progress(self, task_id: str, process):
        """Monitor FFmpeg encoding progress"""
        start_time = time.time()
        frame_count = 0
        total_duration = 0
        
        while process.returncode is None and task_id in self.active_tasks:
            line = (await process.stderr.readline()).decode().strip()
            
            if not line:
                await asyncio.sleep(0.1)
                continue
            
            # Parse FFmpeg progress output
            if line.startswith('frame='):
                parts = line.split()
                progress_data = {p.split('=')[0]: p.split('=')[1] for p in parts if '=' in p}
                
                if 'frame' in progress_data:
                    frame_count = int(progress_data['frame'])
                if 'total_size' in progress_data:
                    size = humanize.naturalsize(int(progress_data['total_size']))
                if 'out_time_ms' in progress_data:
                    current_time = int(progress_data['out_time_ms']) / 1000000
                if 'total_size' in progress_data and 'out_time_ms' in progress_data:
                    if total_duration == 0 and 'duration' in progress_data:
                        total_duration = float(progress_data['duration'])
                    elif total_duration == 0:
                        # Estimate duration from bitrate if not available
                        pass
                    
                    if total_duration > 0:
                        progress = (current_time / total_duration) * 100
                        elapsed = time.time() - start_time
                        remaining = (elapsed / current_time) * (total_duration - current_time) if current_time > 0 else 0
                        
                        status_text = (
                            "🎞️ Encoding Video\n\n"
                            f"📁 {self.active_tasks[task_id]['current_file']}\n"
                            f"📊 Progress: {progress:.1f}%\n"
                            f"🕒 Elapsed: {humanize.naturaldelta(elapsed)}\n"
                            f"⏳ Remaining: {humanize.naturaldelta(remaining)}\n"
                            f"💾 Size: {size}"
                        )
                        
                        await self.update_status(task_id, status_text)
            
            await asyncio.sleep(1)
    
    async def upload_to_channel(self, task_id: str, file_path: str):
        """Upload encoded file to destination channel with progress"""
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        start_time = time.time()
        last_update = 0
        
        async def progress(current, total):
            nonlocal last_update
            now = time.time()
            if now - last_update > STATUS_UPDATE_INTERVAL or current == total:
                progress_percent = (current / total) * 100
                speed = current / (now - start_time)
                eta = (total - current) / speed if speed > 0 else 0
                
                status_text = (
                    "☁️ Uploading to Channel\n\n"
                    f"📁 {file_name}\n"
                    f"📊 Progress: {progress_percent:.1f}%\n"
                    f"📦 {humanize.naturalsize(current)} / {humanize.naturalsize(total)}\n"
                    f"⚡ Speed: {humanize.naturalsize(speed)}/s\n"
                    f"⏳ ETA: {humanize.naturaldelta(eta)}"
                )
                
                await self.update_status(task_id, status_text)
                last_update = now
        
        try:
            await self.app.send_document(
                chat_id=DESTINATION_CHANNEL,
                document=file_path,
                caption="Encoded with ❤️ by AnimeEncoderBot",
                progress=progress
            )
            logger.info(f"Successfully uploaded: {file_path}")
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            raise
    
    async def update_status(self, task_id: str, text: str, final: bool = False):
        """Update the status message"""
        try:
            if task_id in self.status_messages:
                await self.app.edit_message_text(
                    chat_id=SOURCE_CHANNEL,
                    message_id=self.status_messages[task_id],
                    text=text,
                    reply_markup=None if final else self.get_status_keyboard(task_id)
                )
        except Exception as e:
            logger.error(f"Error updating status: {e}")
    
    def get_status_keyboard(self, task_id: str):
        """Generate inline keyboard for status message"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{task_id}")]
        ])
    
    async def handle_callback_query(self, client: Client, callback_query):
        """Handle refresh button clicks"""
        task_id = callback_query.data.split('_')[1]
        if task_id in self.active_tasks:
            task = self.active_tasks[task_id]
            
            if task['status'] == 'downloading':
                # Show torrent status
                status = task.get('torrent_status', {})
                text = (
                    "⬇️ Downloading Torrent\n\n"
                    f"📊 Progress: {status.get('progress', 0):.1f}%\n"
                    f"🔽 Downloaded: {status.get('downloaded', '0')} / {status.get('size', '0')}\n"
                    f"⚡ Speed: {status.get('speed', '0')}\n"
                    f"⏳ ETA: {status.get('eta', 'Unknown')}"
                )
            elif task['status'] == 'encoding':
                # Show encoding status
                text = "🎞️ Encoding in progress..."
            
            await callback_query.edit_message_text(
                text=text,
                reply_markup=self.get_status_keyboard(task_id)
            )
        
        await callback_query.answer()

if __name__ == "__main__":
    bot = AnimeEncoderBot()
    asyncio.run(bot.start())
