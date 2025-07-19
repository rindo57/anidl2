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

# Initialize aria2
aria2 = aria2p.API(
    aria2p.Client(host="http://localhost", port=6800, secret="")
)

def start_aria2():
    subprocess.Popen(["aria2c", "--enable-rpc", "--rpc-listen-all=false", "--rpc-allow-origin-all", "--seed-time=0", "--dir=./downloads"])

start_aria2()

def sizeof_fmt(num, suffix="B"):
    for unit in ['','K','M','G','T']:
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}P{suffix}"

async def update_download_status(msg, download):
    while not download.is_complete:
        download.update()
        progress = (download.completed_length / download.total_length) * 100 if download.total_length else 0
        speed = sizeof_fmt(download.download_speed) + "/s"
        eta = f"{download.eta}s" if download.eta else "∞"
        text = f"📥 **Downloading...**\n\n**File:** {download.name}\n**Progress:** `{progress:.2f}%`\n**Speed:** `{speed}`\n**ETA:** `{eta}`"
        try:
            await msg.edit(text)
        except:
            pass
        await asyncio.sleep(5)

async def encode_with_progress(input_path, output_path, msg):
    cmd = [
        "ffmpeg", "-i", input_path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p10le",
        "-c:a", "copy", output_path, "-y"
    ]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    
    duration = None
    pattern_time = "time="
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        line = line.decode("utf-8", errors="ignore")
        if "Duration" in line:
            try:
                h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
                duration = int(float(h) * 3600 + float(m) * 60 + float(s))
            except:
                pass
        elif "time=" in line and duration:
            try:
                time_str = line.split("time=")[1].split(" ")[0]
                h, m, s = map(float, time_str.split(":"))
                seconds = int(h * 3600 + m * 60 + s)
                percent = (seconds / duration) * 100
                await msg.edit(f"🎬 **Encoding...**\n\n**Progress:** `{percent:.2f}%`\n**Time:** `{int(seconds)}/{duration} sec`")
            except:
                pass
    await process.wait()

@app.on_message(filters.chat(INPUT_CHANNEL) & filters.document)
async def handle_torrent(client, message):
    if not message.document.file_name.endswith(".torrent"):
        return

    status = await message.reply("📥 Downloading torrent...")
    torrent_path = await message.download()

    download = aria2.add_torrent(torrent_path, options={"dir": "./downloads"})

    await update_download_status(status, download)

    await status.edit("✅ Download complete. Starting encoding...")
    
    for file in download.files:
        input_file = str(file.path)
        if not input_file.lower().endswith((".mkv", ".mp4", ".avi")):
            continue

        output_file = input_file.rsplit(".", 1)[0] + "_x265.mkv"
        try:
            await encode_with_progress(input_file, output_file, status)
            await status.edit(f"📤 Uploading {os.path.basename(output_file)}...")
            await client.send_document(OUTPUT_CHANNEL, document=output_file)
        except Exception as e:
            await status.edit(f"❌ Encoding failed:\n`{e}`")
    await status.edit("✅ All done.")
    os.remove(torrent_path)

app.run()

