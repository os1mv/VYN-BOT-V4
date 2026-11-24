import os
import re
import io
import json
import time
import hmac
import hashlib
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from groq import AsyncGroq
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    WebAppInfo
)
from telegram.constants import ChatType
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from pypdf import PdfReader

load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

DATABASE = os.getenv("DATABASE_PATH", "/var/data/vyn.db")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")

if not BOT_TOKEN or not GROQ_API_KEY:
    raise RuntimeError("BOT_TOKEN and GROQ_API_KEY are required.")

os.makedirs(os.path.dirname(DATABASE) or ".", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("VYN")

groq = AsyncGroq(api_key=GROQ_API_KEY)

SYSTEM = """
You are VYN BOT GOD EDITION.

You are a highly capable, intelligent AI assistant.

Capabilities:
- Programming and software engineering
- Research and analysis
- Education and explanations
- Productivity and planning
- Business and creativity
- Document analysis
- Technical support

Rules:
1. Be accurate and useful.
2. Never invent sources or claim a tool ran when it did not.
3. Explain code clearly.
4. Protect private information.
5. Ask for clarification when necessary.
6. Use Markdown formatting.
7. You are VYN, not another assistant.
"""

# ==========================================
# DATABASE
# ==========================================

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined TEXT
        );

        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            created TEXT
        );

        CREATE TABLE IF NOT EXISTS memories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            content TEXT,
            created TEXT
        );

        CREATE TABLE IF NOT EXISTS reminders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            chat_id INTEGER,
            message TEXT,
            due REAL,
            completed INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS group_settings(
            chat_id INTEGER PRIMARY KEY,
            anti_link INTEGER DEFAULT 0,
            anti_flood INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS warnings(
            chat_id INTEGER,
            user_id INTEGER,
            count INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id,user_id)
        );
        """)

def save_user(user):
    with db() as con:
        con.execute("""
        INSERT INTO users VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username,
        first_name=excluded.first_name
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            datetime.now(timezone.utc).isoformat()
        ))

def save_message(user_id, role, content):
    with db() as con:
        con.execute(
            "INSERT INTO messages(user_id,role,content,created) VALUES(?,?,?,?)",
            (user_id, role, content[:12000],
             datetime.now(timezone.utc).isoformat())
        )

def get_history(user_id):
    with db() as con:
        rows = con.execute("""
            SELECT role,content FROM messages
            WHERE user_id=?
            ORDER BY id DESC LIMIT 16
        """, (user_id,)).fetchall()
    return [
        {"role": r["role"], "content": r["content"]}
        for r in reversed(rows)
    ]

def get_memories(user_id):
    with db() as con:
        rows = con.execute("""
            SELECT content FROM memories
            WHERE user_id=?
            ORDER BY id DESC LIMIT 15
        """, (user_id,)).fetchall()
    return [r["content"] for r in rows]

def save_memory(user_id, content):
    with db() as con:
        con.execute(
            "INSERT INTO memories(user_id,content,created) VALUES(?,?,?)",
            (user_id, content[:1000],
             datetime.now(timezone.utc).isoformat())
        )

# ==========================================
# AI ENGINE
# ==========================================

async def ask_ai(user_id, prompt, system=None):
    history = get_history(user_id)
    memories = get_memories(user_id)

    memory_text = "\n".join(memories) or "No saved memories."

    messages = [
        {
            "role": "system",
            "content": (system or SYSTEM)
            + "\n\nRelevant saved user memories:\n"
            + memory_text
        },
        *history,
        {"role": "user", "content": prompt}
    ]

    result = await groq.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.6,
        max_completion_tokens=4000
    )

    answer = result.choices[0].message.content or "No response."

    save_message(user_id, "user", prompt)
    save_message(user_id, "assistant", answer)

    return answer

# ==========================================
# UTILITIES
# ==========================================

async def send_long(message, text):
    text = str(text)
    for i in range(0, len(text), 4000):
        await message.reply_text(text[i:i+4000])

def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("AI CHAT", callback_data="chat"),
            InlineKeyboardButton("CODE LAB", callback_data="code")
        ],
        [
            InlineKeyboardButton("RESEARCH", callback_data="research"),
            InlineKeyboardButton("AGENTS", callback_data="agents")
        ],
        [
            InlineKeyboardButton("MEMORY", callback_data="memory"),
            InlineKeyboardButton("TOOLS", callback_data="tools")
        ],
        [
            InlineKeyboardButton("REMINDERS", callback_data="reminders"),
            InlineKeyboardButton("STATUS", callback_data="status")
        ]
    ]

    if WEBAPP_URL:
        keyboard.insert(0, [
            InlineKeyboardButton(
                "OPEN VYN COMMAND CENTER",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ])

    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    await update.message.reply_text(
        "◉ VYN BOT — GOD EDITION\n\n"
        "Welcome to your AI Command Center.\n\n"
        "Your intelligence platform is ready.\n"
        "Choose a system below.",
        reply_markup=main_keyboard()
    )

async def menu(update, context):
    await update.message.reply_text(
        "VYN COMMAND CENTER",
        reply_markup=main_keyboard()
    )

async def help_command(update, context):
    text = """
VYN GOD EDITION COMMANDS

/start - Launch VYN
/menu - Main menu
/chat - AI chat
/code - Code Lab
/debug - Debug code
/research - Internet research
/agents - Multi-agent AI
/image - Image generation
/analyze - Analyze uploaded files
/voice - Voice tools
/memory - View saved memory
/remember - Save a memory
/forget - Clear your saved memories
/remind - Create reminder
/tasks - View reminders
/reset - Reset chat history
/status - System status
/admin - Admin dashboard

GROUP COMMANDS
/warn - Warn a member
/ban - Ban a member
/unban - Unban a member
/mute - Mute a member
/antilink - Toggle link protection
"""
    await send_long(update.message, text)

# ==========================================
# AI CHAT
# ==========================================

async def chat_command(update, context):
    context.user_data["mode"] = "chat"
    await update.message.reply_text(
        "VYN AI CHAT activated.\nSend your message."
    )

async def text_handler(update, context):
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    chat = update.effective_chat
    save_user(user)

    prompt = update.message.text

    if chat.type != ChatType.PRIVATE:
        # In groups, only respond when mentioned or replied to.
        if not update.message.reply_to_message:
            if not context.bot.username:
                return
            if f"@{context.bot.username.lower()}" not in prompt.lower():
                return
        prompt = re.sub(r"@\w+", "", prompt).strip()

    mode = context.user_data.get("mode", "chat")

    await update.message.chat.send_action("typing")

    try:
        if mode == "code":
            system = SYSTEM + """
You are VYN Code Lab.
Generate clean, secure, well-commented code.
Include language, explanation and setup steps.
"""
        else:
            system = SYSTEM

        answer = await ask_ai(user.id, prompt, system)
        await send_long(update.message, answer)

    except Exception:
        log.exception("AI error")
        await update.message.reply_text(
            "VYN encountered an AI error. Check your Groq configuration."
        )

async def code_command(update, context):
    context.user_data["mode"] = "code"
    await update.message.reply_text(
        "VYN CODE LAB\n\n"
        "Describe what you want to build or paste your code."
    )

async def debug_command(update, context):
    context.user_data["mode"] = "code"
    prompt = "Debug and fix this code:\n" + " ".join(context.args)
    if not context.args:
        await update.message.reply_text("Use /debug followed by your code.")
        return
    answer = await ask_ai(update.effective_user.id, prompt)
    await send_long(update.message, answer)

# ==========================================
# MULTI-AGENT SYSTEM
# ==========================================

AGENTS = {
    "researcher": """
You are VYN Researcher.
Investigate questions, identify evidence and explain uncertainty.
Do not fabricate sources.
""",
    "coder": """
You are VYN Senior Software Engineer.
Create secure, maintainable and documented code.
""",
    "analyst": """
You are VYN Data Analyst.
Analyze information, identify patterns and summarize findings.
""",
    "planner": """
You are VYN Strategic Planner.
Break tasks into realistic, ordered steps.
"""
}

async def agents_command(update, context):
    if not context.args:
        await update.message.reply_text(
            "Usage:\n/agents Build an e-commerce website"
        )
        return

    task = " ".join(context.args)
    await update.message.reply_text(
        "VYN AGENT TEAM ACTIVATED\n"
        "Planner → Researcher → Coder → Analyst"
    )

    user_id = update.effective_user.id

    try:
        results = []

        for name, instructions in AGENTS.items():
            result = await ask_ai(
                user_id,
                task,
                SYSTEM + "\n" + instructions
            )
            results.append(f"### {name.upper()}\n{result}")

        final = await ask_ai(
            user_id,
            "Combine and critically review these agent reports:\n\n"
            + "\n\n".join(results),
            SYSTEM
        )

        await send_long(update.message, final)

    except Exception:
        log.exception("Agent error")
        await update.message.reply_text("Agent workflow failed.")

# ==========================================
# INTERNET RESEARCH - TAVILY
# ==========================================

async def research_command(update, context):
    if not context.args:
        await update.message.reply_text(
            "Usage: /research your research question"
        )
        return

    query = " ".join(context.args)

    if not TAVILY_API_KEY:
        await update.message.reply_text(
            "Research API is not configured. Add TAVILY_API_KEY to Render."
        )
        return

    await update.message.reply_text("VYN RESEARCHER is searching...")

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 6
                }
            )
            response.raise_for_status()
            data = response.json()

        sources = data.get("results", [])
        source_text = "\n\n".join(
            f"Title: {s.get('title')}\n"
            f"URL: {s.get('url')}\n"
            f"Content: {s.get('content')}"
            for s in sources
        )

        answer = await ask_ai(
            update.effective_user.id,
            "Answer this research question using the sources. "
            "Cite URLs next to claims and mention uncertainty.\n"
            f"QUESTION: {query}\nSOURCES:\n{source_text}"
        )

        await send_long(update.message, answer)

    except Exception:
        log.exception("Research failed")
        await update.message.reply_text("Research failed.")

# ==========================================
# FILE ANALYSIS
# ==========================================

async def document_handler(update, context):
    document = update.message.document

    if document.file_size > 15 * 1024 * 1024:
        await update.message.reply_text("Maximum file size is 15 MB.")
        return

    name = document.file_name or "document"
    extension = os.path.splitext(name)[1].lower()

    if extension not in [".pdf", ".txt", ".md"]:
        await update.message.reply_text(
            "Supported files: PDF, TXT and Markdown."
        )
        return

    await update.message.reply_text("Reading your document...")

    try:
        file = await context.bot.get_file(document.file_id)
        data = await file.download_as_bytearray()

        if extension == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            content = "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        else:
            content = bytes(data).decode("utf-8", errors="replace")

        content = content[:30000]

        if not content.strip():
            await update.message.reply_text(
                "No readable text found in this file."
            )
            return

        answer = await ask_ai(
            update.effective_user.id,
            "Analyze this document. Give me a summary, key points, "
            "important information and possible questions.\n\n"
            + content
        )

        await send_long(update.message, answer)

    except Exception:
        log.exception("File analysis error")
        await update.message.reply_text("Could not analyze this file.")

# ==========================================
# IMAGE GENERATION - REPLICATE
# ==========================================

async def image_command(update, context):
    prompt = " ".join(context.args)

    if not prompt:
        await update.message.reply_text(
            "Usage: /image a futuristic silver robot"
        )
        return

    if not REPLICATE_API_TOKEN:
        await update.message.reply_text(
            "Add REPLICATE_API_TOKEN to Render to enable image generation."
        )
        return

    await update.message.reply_text("Generating your image...")

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait"
    }

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                "https://api.replicate.com/v1/models/"
                "black-forest-labs/flux-schnell/predictions",
                headers=headers,
                json={
                    "input": {
                        "prompt": prompt,
                        "num_outputs": 1,
                        "aspect_ratio": "1:1",
                        "output_format": "webp"
                    }
                }
            )
            response.raise_for_status()
            prediction = response.json()

            for _ in range(40):
                if prediction.get("status") in ("succeeded", "failed", "canceled"):
                    break

                await asyncio.sleep(2)
                response = await client.get(
                    prediction["urls"]["get"],
                    headers=headers
                )
                response.raise_for_status()
                prediction = response.json()

        if prediction.get("status") != "succeeded":
            await update.message.reply_text("Image generation failed or timed out.")
            return

        output = prediction.get("output")
        image_url = output[0] if isinstance(output, list) else output

        if not image_url:
            await update.message.reply_text("No image was returned.")
            return

        async with httpx.AsyncClient(timeout=60) as client:
            image = await client.get(image_url)
            image.raise_for_status()

        await update.message.reply_photo(
            photo=io.BytesIO(image.content),
            caption="VYN IMAGE GENERATOR"
        )

    except Exception:
        log.exception("Image generation error")
        await update.message.reply_text("Image generation error.")

# ==========================================
# VOICE TRANSCRIPTION
# ==========================================

async def voice_handler(update, context):
    await update.message.reply_text("Transcribing voice...")

    try:
        voice = update.message.voice or update.message.audio
        file = await context.bot.get_file(voice.file_id)
        data = io.BytesIO()
        await file.download_to_memory(data)
        data.seek(0)

        result = await groq.audio.transcriptions.create(
            file=("voice.ogg", data.read()),
            model="whisper-large-v3",
            response_format="text"
        )

        transcript = str(result)

        await update.message.reply_text(
            "TRANSCRIPTION:\n" + transcript[:3500]
        )

        answer = await ask_ai(
            update.effective_user.id,
            transcript
        )
        await send_long(update.message, answer)

    except Exception:
        log.exception("Voice error")
        await update.message.reply_text("Voice processing failed.")

# ==========================================
# AI MEMORY
# ==========================================

async def remember_command(update, context):
    content = " ".join(context.args)

    if not content:
        await update.message.reply_text(
            "Usage: /remember I prefer Python."
        )
        return

    save_memory(update.effective_user.id, content)

    await update.message.reply_text("Memory saved.")

async def memory_command(update, context):
    memories = get_memories(update.effective_user.id)

    if not memories:
        await update.message.reply_text("No saved memories.")
        return

    text = "YOUR VYN MEMORIES\n\n"
    text += "\n".join(
        f"{i+1}. {m}" for i, m in enumerate(memories)
    )
    await send_long(update.message, text)

async def forget_command(update, context):
    with db() as con:
        con.execute(
            "DELETE FROM memories WHERE user_id=?",
            (update.effective_user.id,)
        )
    await update.message.reply_text("Your saved memories were cleared.")

async def reset_command(update, context):
    with db() as con:
        con.execute(
            "DELETE FROM messages WHERE user_id=?",
            (update.effective_user.id,)
        )
    await update.message.reply_text("Conversation history cleared.")

# ==========================================
# REMINDERS
# ==========================================

async def reminder_callback(context):
    job = context.job
    try:
        await context.bot.send_message(
            chat_id=job.chat_id,
            text="◉ VYN REMINDER\n\n" + job.data["message"]
        )
    except Exception:
        log.exception("Reminder delivery failed")

    with db() as con:
        con.execute(
            "UPDATE reminders SET completed=1 WHERE id=?",
            (job.data["id"],)
        )

async def remind_command(update, context):
    # Format: /remind 10m Drink water
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "/remind 10m Drink water\n"
            "/remind 2h Check project\n"
            "/remind 1d Call someone\n\n"
            "Units: m, h, d"
        )
        return

    match = re.fullmatch(
        r"(\d+)(m|h|d)",
        context.args[0].lower()
    )

    if not match:
        await update.message.reply_text("Use a time like 10m, 2h or 1d.")
        return

    amount = int(match.group(1))
    unit = match.group(2)

    if amount <= 0:
        await update.message.reply_text("Time must be positive.")
        return

    seconds = amount * {"m": 60, "h": 3600, "d": 86400}[unit]

    if seconds > 30 * 86400:
        await update.message.reply_text("Maximum reminder is 30 days.")
        return

    message = " ".join(context.args[1:])
    due = time.time() + seconds

    with db() as con:
        cursor = con.execute(
            "INSERT INTO reminders(user_id,chat_id,message,due) VALUES(?,?,?,?)",
            (update.effective_user.id,
             update.effective_chat.id, message, due)
        )
        reminder_id = cursor.lastrowid

    context.job_queue.run_once(
        reminder_callback,
        when=seconds,
        chat_id=update.effective_chat.id,
        data={"id": reminder_id, "message": message},
        name=f"reminder_{reminder_id}"
    )

    await update.message.reply_text("Reminder scheduled.")

async def tasks_command(update, context):
    with db() as con:
        rows = con.execute("""
            SELECT id,message,due FROM reminders
            WHERE user_id=? AND completed=0
            ORDER BY due LIMIT 20
        """, (update.effective_user.id,)).fetchall()

    if not rows:
        await update.message.reply_text("No pending reminders.")
        return

    result = "YOUR REMINDERS\n\n"
    for row in rows:
        due = datetime.fromtimestamp(
            row["due"], timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
        result += f"#{row['id']} — {row['message']}\n{due}\n\n"

    await send_long(update.message, result)

async def restore_reminders(app):
    with db() as con:
        rows = con.execute("""
            SELECT id,chat_id,message,due FROM reminders
            WHERE completed=0
        """).fetchall()

    for row in rows:
        delay = max(1, row["due"] - time.time())
        app.job_queue.run_once(
            reminder_callback,
            when=delay,
            chat_id=row["chat_id"],
            data={"id": row["id"], "message": row["message"]},
            name=f"reminder_{row['id']}"
        )

# ==========================================
# GROUP MODERATION
# ==========================================

async def is_admin(update, context, user_id=None):
    target = user_id or update.effective_user.id

    try:
        member = await context.bot.get_chat_member(
            update.effective_chat.id, target
        )
        return member.status in ("administrator", "creator")
    except Exception:
        return False

async def warn_command(update, context):
    if not await is_admin(update, context):
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to warn them.")
        return

    target = update.message.reply_to_message.from_user.id
    chat_id = update.effective_chat.id

    with db() as con:
        con.execute("""
        INSERT INTO warnings(chat_id,user_id,count)
        VALUES(?,?,1)
        ON CONFLICT(chat_id,user_id)
        DO UPDATE SET count=count+1
        """, (chat_id, target))

        row = con.execute(
            "SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
            (chat_id, target)
        ).fetchone()

    count = row["count"]

    if count >= 3:
        await context.bot.ban_chat_member(chat_id, target)
        await update.message.reply_text("3 warnings reached. User banned.")
    else:
        await update.message.reply_text(
            f"Warning {count}/3 issued."
        )

async def ban_command(update, context):
    if not await is_admin(update, context):
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to ban them.")
        return

    target = update.message.reply_to_message.from_user.id

    await context.bot.ban_chat_member(
        update.effective_chat.id, target
    )
    await update.message.reply_text("User banned.")

async def unban_command(update, context):
    if not await is_admin(update, context):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /unban USER_ID")
        return

    await context.bot.unban_chat_member(
        update.effective_chat.id,
        int(context.args[0])
    )
    await update.message.reply_text("User unbanned.")

async def mute_command(update, context):
    if not await is_admin(update, context):
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to mute them.")
        return

    target = update.message.reply_to_message.from_user.id

    await context.bot.restrict_chat_member(
        update.effective_chat.id,
        target,
        permissions={"can_send_messages": False}
    )
    await update.message.reply_text("User muted.")

async def antilink_command(update, context):
    if not await is_admin(update, context):
        return

    chat_id = update.effective_chat.id

    with db() as con:
        con.execute("""
        INSERT INTO group_settings(chat_id,anti_link)
        VALUES(?,1)
        ON CONFLICT(chat_id)
        DO UPDATE SET anti_link=1-anti_link
        """, (chat_id,))

        row = con.execute(
            "SELECT anti_link FROM group_settings WHERE chat_id=?",
            (chat_id,)
        ).fetchone()

    await update.message.reply_text(
        "Anti-link: " + ("ON" if row["anti_link"] else "OFF")
    )

async def moderation_handler(update, context):
    message = update.message

    if not message or not message.text:
        return

    if message.chat.type not in ("group", "supergroup"):
        return

    if await is_admin(update, context):
        return

    chat_id = message.chat_id
    user_id = message.from_user.id

    with db() as con:
        row = con.execute(
            "SELECT anti_link FROM group_settings WHERE chat_id=?",
            (chat_id,)
        ).fetchone()

    if row and row["anti_link"]:
        if re.search(
            r"(https?://|www\.|t\.me/)",
            message.text,
            re.IGNORECASE
        ):
            await message.delete()
            return

# ==========================================
# ADMIN DASHBOARD
# ==========================================

async def admin_command(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Access denied.")
        return

    with db() as con:
        users = con.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        messages = con.execute(
            "SELECT COUNT(*) FROM messages"
        ).fetchone()[0]

        reminders = con.execute(
            "SELECT COUNT(*) FROM reminders WHERE completed=0"
        ).fetchone()[0]

    text = (
        "◉ VYN ADMIN DASHBOARD\n\n"
        f"Users: {users}\n"
        f"AI messages: {messages}\n"
        f"Pending reminders: {reminders}\n"
        f"Model: {MODEL}\n"
        "Status: ONLINE"
    )

    await update.message.reply_text(text)

async def status_command(update, context):
    await update.message.reply_text(
        "◉ VYN SYSTEM STATUS\n\n"
        "Telegram: Connected\n"
        "Groq: Configured\n"
        f"Model: {MODEL}\n"
        f"Research: {'Enabled' if TAVILY_API_KEY else 'Disabled'}\n"
        f"Images: {'Enabled' if REPLICATE_API_TOKEN else 'Disabled'}\n"
        "Memory: SQLite\n"
        "Status: ONLINE"
    )

# ==========================================
# BUTTON HANDLER
# ==========================================

async def button_handler(update, context):
    query = update.callback_query
    await query.answer()

    actions = {
        "chat": ("AI CHAT", "Send a message to VYN."),
        "code": ("CODE LAB", "Use /code to begin."),
        "research": ("RESEARCH", "Use /research followed by a question."),
        "agents": ("AGENT TEAM", "Use /agents followed by your task."),
        "memory": ("MEMORY", "Use /memory or /remember."),
        "tools": ("TOOLS", "Available: /image, /analyze, /voice."),
        "reminders": ("REMINDERS", "Use /remind 10m Your message."),
        "status": ("SYSTEM STATUS", f"VYN is online.\nModel: {MODEL}")
    }

    title, description = actions.get(
        query.data, ("VYN", "Unknown command.")
    )

    await query.message.reply_text(
        f"◉ {title}\n\n{description}"
    )

# ==========================================
# MINI APP DATA HANDLER
# ==========================================

async def webapp_handler(update, context):
    data = update.effective_message.web_app_data.data

    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return

    if payload.get("action") == "chat":
        prompt = str(payload.get("text", ""))[:4000]

        if not prompt:
            return

        answer = await ask_ai(
            update.effective_user.id,
            prompt
        )

        await update.message.reply_text(answer)

# ==========================================
# ERROR HANDLER
# ==========================================

async def error_handler(update, context):
    log.error(
        "Update failed",
        exc_info=context.error
    )

# ==========================================
# START APPLICATION
# ==========================================

async def post_init(app):
    init_db()
    await restore_reminders(app)

    commands = [
        ("start", "Launch VYN"),
        ("menu", "Command Center"),
        ("chat", "AI assistant"),
        ("code", "Code Lab"),
        ("debug", "Debug code"),
        ("research", "Internet research"),
        ("agents", "Multi-agent AI"),
        ("image", "Generate image"),
        ("memory", "View memory"),
        ("remember", "Save memory"),
        ("forget", "Clear memory"),
        ("analyze", "Analyze document"),
        ("voice", "Voice assistant"),
        ("remind", "Create reminder"),
        ("tasks", "View reminders"),
        ("status", "System status"),
        ("admin", "Admin dashboard"),
        ("help", "Help center")
    ]

    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand(command, description)
        for command, description in commands
    ])

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("chat", chat_command))
    app.add_handler(CommandHandler("code", code_command))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("agents", agents_command))
    app.add_handler(CommandHandler("research", research_command))
    app.add_handler(CommandHandler("image", image_command))
    app.add_handler(CommandHandler("remember", remember_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("warn", warn_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("mute", mute_command))
    app.add_handler(CommandHandler("antilink", antilink_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("status", status_command))

    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.WEB_APP_DATA,
            webapp_handler
        )
    )

    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO,
            voice_handler
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Document.ALL,
            document_handler
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            moderation_handler,
            group=0
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
            group=1
        )
    )

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    log.info("VYN GOD EDITION starting...")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
