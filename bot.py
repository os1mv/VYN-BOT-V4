import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from groq import Groq

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 AI Assistant - Meta Llama", callback_data="ai")],
        [InlineKeyboardButton("💻 Code Lab", callback_data="code")],
        [InlineKeyboardButton("🔍 Research", callback_data="research")],
        [InlineKeyboardButton("🎨 Creative Studio", callback_data="creative")],
        [InlineKeyboardButton("📊 System Status", callback_data="status")],
    ]
    await update.message.reply_text("🩶 *VYN BOT* - Powered by Meta Llama\n\nSift liya ay haja, ana njawebk!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["mode"] = query.data
    await query.edit_message_text(f"Ok! Mode: {query.data} - Sift liya daba chno bghiti 👇")

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    mode = context.user_data.get("mode", "ai")
    await update.message.reply_text("Kankteb... ⏳")
    try:
        res = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": f"You are VYN BOT, a helpful assistant. Mode: {mode}. Answer in Moroccan Darija mixed with Arabic, friendly."},
                {"role": "user", "content": text}
            ],
        )
        await update.message.reply_text(res.choices[0].message.content)
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e} - Wach 7etiti l'GROQ_API_KEY f .env?")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.run_polling()