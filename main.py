import os
import requests
import telebot

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN not found")
    exit(1)

bot = telebot.TeleBot(TOKEN)

def get_price(symbol):
    try:
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        r = requests.get(url, timeout=10).json()
        return {
            "price": float(r['lastPrice']),
            "change": float(r['priceChangePercent'])
        }
    except Exception as e:
        print(f"Error: {e}")
        return None

def get_signal(change):
    if change > 2:
        return "🟢 شراء قوي"
    elif change > 0:
        return "🟡 شراء ضعيف"
    elif change < -2:
        return "🔴 بيع قوي"
    elif change < 0:
        return "🟠 بيع ضعيف"
    return "⚪ انتظار"

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message,
        "🤖 مرحبا!\n\n"
        "/gold — الذهب\n"
        "/btc — البيتكوين\n"
        "/price — الأسعار"
    )

@bot.message_handler(commands=['btc'])
def btc(message):
    send_analysis(message, "BTCUSDT", "₿ البيتكوين")

@bot.message_handler(commands=['gold'])
def gold(message):
    send_analysis(message, "PAXGUSDT", "🥇 الذهب")

@bot.message_handler(commands=['price'])
def price(message):
    b = get_price("BTCUSDT")
    g = get_price("PAXGUSDT")
    if b and g:
        bot.reply_to(message,
            f"💵 الأسعار:\n"
            f"₿ بيتكوين: {b['price']:,.2f}$\n"
            f"🥇 ذهب: {g['price']:,.2f}$"
        )
    else:
        bot.reply_to(message, "❌ خطأ")

def send_analysis(message, symbol, name):
    data = get_price(symbol)
    if not data:
        bot.reply_to(message, "❌ خطأ في جلب البيانات")
        return
    signal = get_signal(data['change'])
    msg = (
        f"{name}\n"
        f"━━━━━━━━━━━━\n"
        f"💰 السعر: {data['price']:,.2f}$\n"
        f"📊 التغيير: {data['change']:+.2f}%\n"
        f"🎯 الإشارة: {signal}"
    )
    bot.reply_to(message, msg)

@bot.message_handler(func=lambda m: True)
def unknown(message):
    bot.reply_to(message, "اكتب /start")

print("✅ Bot started...")
bot.infinity_polling()
