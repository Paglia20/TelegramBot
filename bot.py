import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from firebase_keywords import add_keyword, remove_keyword, get_keywords as list_keywords
from firebase_seen import reset_seen_posts

# Load environment variables (solo per il token Telegram)
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Logging (utile per Railway)
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
        "/reset_seen"
    )

async def reset_seen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_seen_posts()
    await update.message.reply_text("🧹 Lista dei post visti è stata resettata.")

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

# Setup del bot
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("aggiungi", aggiungi))
app.add_handler(CommandHandler("elimina", elimina))
app.add_handler(CommandHandler("lista", lista))
app.add_handler(CommandHandler("reset_seen", reset_seen))

if __name__ == "__main__":
    logging.info("🤖 Bot avviato e collegato a Firebase.")
    app.run_polling()