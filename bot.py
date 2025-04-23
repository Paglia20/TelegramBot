import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from utils import add_keyword, remove_keyword, list_keywords

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Logging (Railway will show this in logs tab)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# Telegram command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Benvenuto nel bot annunci!\n"
        "Usa:\n"
        "/aggiungi <parola>\n"
        "/elimina <parola>\n"
        "/lista"
    )

async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        parola = " ".join(context.args).lower()
        add_keyword(parola)
        await update.message.reply_text(f"✅ Aggiunta parola chiave: *{parola}*", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Usa il comando così: /aggiungi <parola>")

async def elimina(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        parola = " ".join(context.args).lower()
        remove_keyword(parola)
        await update.message.reply_text(f"🗑️ Rimossa parola chiave: *{parola}*", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Usa il comando così: /elimina <parola>")

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keywords = list_keywords()
    if keywords:
        formatted = "\n".join(f"- {kw}" for kw in keywords)
        await update.message.reply_text(f"📋 Parole chiave attive:\n{formatted}")
    else:
        await update.message.reply_text("📭 Nessuna parola chiave attiva.")

# Init bot
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("aggiungi", aggiungi))
app.add_handler(CommandHandler("elimina", elimina))
app.add_handler(CommandHandler("lista", lista))

if __name__ == "__main__":
    logging.info("🤖 Bot avviato su Railway")
    app.run_polling()