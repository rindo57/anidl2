
import os
import asyncio
import libtorrent as lt
import ffmpeg
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
API_ID = 10247139
API_HASH = "96b46175824223a33737657ab943fd6a"
BOT_TOKEN = "8142248256:AAFCrdKxcUYt2Ohtw0cJJ600YwoGAWjISXA"
SOURCE_CHANNEL = -1002812861775  # e.g., -100123456789
DEST_CHANNEL = -1002896336339   # e.g., -100987654321

# Initialize Pyrogram client
app = Client("AnimeBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Helper function to format file size
def format_size(bytes_size: int) -> str:
    if bytes_size < 1024**2:
        return f"{bytes_size / 1024:.2f} KB"
    elif bytes_size < 1024**3:
        return f"{bytes_size / (1024**2):.2f} MB"
    else:
        return f"{bytes_size / (1024**3):.2f} GB"

# Helper function to format ETA
def format_eta(seconds: int) -> str:
    if seconds < 0:
        return "Unknown"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"

# Function to download torrent with status updates
async def download_torrent(client: Client, message: Message, torrent_url: str, download_path: str) -> str:
    ses = lt.session()
    params = lt.parse_magnet_uri(torrent_url) if torrent_url.startswith("magnet:") else lt.add_torrent_params(lt.torrent_info(torrent_url))
    params.save_path = download_path
    handle = ses.add_torrent(params)
    
    status_message = await client.send_message(SOURCE_CHANNEL, "Starting torrent download...")
    
    while not handle.is_seed():
        s = handle.status()
        progress = s.progress * 100
        speed = s.download_rate / 1024  # KB/s
        downloaded = s.total_done
        total_size = s.total_wanted
        eta = (total_size - downloaded) / s.download_rate if s.download_rate > 0 else -1
        
        status_text = (
            f"**Download Status**\n"
            f"Progress: {progress:.2f}%\n"
            f"Speed: {speed:.2f} KB/s\n"
            f"Downloaded: {format_size(downloaded)} / {format_size(total_size)}\n"
            f"ETA: {format_eta(eta)}"
        )
        await client.edit_message_text(SOURCE_CHANNEL, status_message.id, status_text)
        await asyncio.sleep(10)  # Update every 10 seconds
    
    # Find the largest video file
    files = handle.torrent_file().files()
    video_extensions = (".mkv", ".mp4", ".avi")
    for i in range(files.num_files()):
        file_path = os.path.join(download_path, files.file_path(i))
        if file_path.endswith(video_extensions):
            await client.edit_message_text(SOURCE_CHANNEL, status_message.id, "Download complete!")
            return file_path
    return None

# Function to encode video with progress updates
async def encode_video(client: Client, message: Message, input_path: str, output_path: str) -> bool:
    status_message = await client.send_message(SOURCE_CHANNEL, "Starting video encoding...")
    
    try:
        def progress_callback(stream, chunk, bytes_remaining):
            total_size = os.path.getsize(input_path)
            progress = ((total_size - bytes_remaining) / total_size) * 100
            asyncio.create_task(
                client.edit_message_text(
                    SOURCE_CHANNEL,
                    status_message.id,
                    f"**Encoding Status**\nProgress: {progress:.2f}%"
                )
            )
        
        stream = ffmpeg.input(input_path)
        stream = ffmpeg.output(stream, output_path, vcodec="libx264", crf=28, preset="veryfast", acodec="aac", ab="192k")
        ffmpeg.run_async(stream, pipe=True, progress=progress_callback)
        await asyncio.sleep(10)  # Allow some time for encoding to start
        return True
    except ffmpeg.Error:
        await client.edit_message_text(SOURCE_CHANNEL, status_message.id, "Encoding failed!")
        return False

# Handle torrent links in source channel
@app.on_message(filters.chat(SOURCE_CHANNEL) & filters.document)
async def handle_torrent(client: Client, message: Message):
    #if message.document.mime_type != "application/x-bittorrent":
  #      await message.reply("Invalid file. Please send a .torrent file.")
   #     return
    
    torrent_url = message.document.file_name
    download_path = "./downloads"
    os.makedirs(download_path, exist_ok=True)
    
    # Download torrent
    video_file = await download_torrent(client, message, torrent_url, download_path)
    if not video_file:
        await message.reply("No video file found in torrent.")
        return
    
    # Encode video
    output_file = video_file.replace(".mkv", "_hevc.mkv").replace(".mp4", "_hevc.mkv")
    success = await encode_video(client, message, video_file, output_file)
    if not success:
        await message.reply("Encoding failed.")
        return
    
    # Upload to destination channel
    await client.send_video(
        chat_id=DEST_CHANNEL,
        video=output_file,
        caption=f"Encoded: {os.path.basename(output_file)}",
        progress=lambda current, total: print(f"Upload: {current / total * 100:.2f}%")
    )
    
    # Clean up
    os.remove(video_file)
    os.remove(output_file)
    await message.reply("Processing complete! File uploaded to destination channel.")

# Run the bot
async def main():
    await app.start()
    print("Bot is running...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
