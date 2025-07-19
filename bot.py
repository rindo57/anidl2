import os
import asyncio
import aria2p
import subprocess
from pyrogram import Client, filters
from dotenv import load_dotenv

load_dotenv()

API_ID = 10247139
API_HASH = "96b46175824223a33737657ab943fd6a"
BOT_TOKEN = "8142248256:AAFCrdKxcUYt2Ohtw0cJJ600YwoGAWjISXA"
INPUT_CHANNEL = -1002812861775
OUTPUT_CHANNEL = -1002896336339

app = Client("anime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Initialize aria2 RPC interface
aria2 = aria2p.API(
    aria2p.Client(host="http://localhost", port=6800, secret="")
)

# Make sure aria2c is running in daemon mode
def start_aria2():
    subprocess.Popen(["aria2c", "--enable-rpc", "--rpc-listen-all=false", "--rpc-allow-origin-all"])

start_aria2()

# HEVC 10-bit encoding using ffmpeg
def encode_to_hevc(input_path, output_path):
    command = [
        "ffmpeg", "-i", input_path,
        "-c:v", "libx265", "-preset", "medium", "-crf", "28",
        "-pix_fmt", "yuv420p10le",
        "-c:a", "copy",  # Optional: copy original audio
        output_path
    ]
    subprocess.run(command, check=True)

# Watch for torrent files in the input channel
@app.on_message(filters.chat(INPUT_CHANNEL) & filters.document)
async def handle_torrent(client, message):
    if not message.document.file_name.endswith(".torrent"):
        return

    status = await message.reply("📥 Downloading torrent...")
    torrent_path = await message.download()

    # Add torrent to aria2
    download = aria2.add_torrent(torrent_path, options={"dir": "./downloads"})

    # Wait for download
    while not download.is_complete:
        await asyncio.sleep(5)
        download.update()

    await status.edit("✅ Download complete. Starting encoding...")

    # Encode all video files
    for file in download.files:
        input_file = file.path
        if not input_file.lower().endswith((".mkv", ".mp4", ".avi")):
            continue
        output_file = input_file.rsplit(".", 1)[0] + "_x265.mkv"
        try:
            encode_to_hevc(input_file, output_file)
            await status.edit(f"📤 Uploading {os.path.basename(output_file)}...")
            await client.send_document(OUTPUT_CHANNEL, document=output_file)
        except Exception as e:
            await message.reply(f"❌ Encoding failed: {e}")

    await status.edit("✅ All files uploaded.")
    os.remove(torrent_path)

app.run()

