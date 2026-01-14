import os
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, CallbackContext
import threading
from flask import Flask

# ===== CONFIGURATION SIMPLE =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

# ===== FLASK POUR RENDER =====
app = Flask(__name__)

@app.route('/')
def home():
    return """
    <h1>🔍 Wallet Tracker Public</h1>
    <p>Track Solana wallets - Each user has private data</p>
    <p><a href="/health">Health Check</a></p>
    """

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    app.run(host='0.0.0.0', port=10000)

# ===== BASE DE DONNÉES SIMPLE =====
class WalletDatabase:
    def __init__(self):
        self.db_file = "wallets_db.json"
        self.cache_file = "tx_cache.json"
        self.load_data()
    
    def load_data(self):
        """Charger ou créer la base de données"""
        try:
            with open(self.db_file, "r") as f:
                self.users = json.load(f)
        except:
            self.users = {}
        
        try:
            with open(self.cache_file, "r") as f:
                self.tx_cache = json.load(f)
        except:
            self.tx_cache = {}
    
    def save_data(self):
        """Sauvegarder les données"""
        with open(self.db_file, "w") as f:
            json.dump(self.users, f, indent=2)
        with open(self.cache_file, "w") as f:
            json.dump(self.tx_cache, f, indent=2)
    
    def get_user(self, user_id):
        """Obtenir ou créer un utilisateur"""
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {
                "wallets": {},  # {address: {"name": "xxx", "added": "date"}}
                "created": datetime.now().isoformat(),
                "alert_count": 0
            }
            self.save_data()
        return self.users[user_id]
    
    def add_wallet(self, user_id, address, name):
        """Ajouter un wallet pour un utilisateur"""
        user = self.get_user(user_id)
        
        # Vérifier la limite (10 wallets max)
        if len(user["wallets"]) >= 10:
            return False, "❌ Maximum 10 wallets reached"
        
        # Vérifier si déjà existant
        if address in user["wallets"]:
            return False, "❌ This wallet is already tracked"
        
        # Ajouter le wallet
        user["wallets"][address] = {
            "name": name[:20],
            "added": datetime.now().isoformat(),
            "last_tx": None,
            "tx_count": 0
        }
        
        self.save_data()
        return True, f"✅ *{name}* is now being tracked!"
    
    def remove_wallet(self, user_id, identifier):
        """Retirer un wallet par nom ou adresse"""
        user = self.get_user(user_id)
        
        # Chercher par adresse complète
        if identifier in user["wallets"]:
            name = user["wallets"][identifier]["name"]
            del user["wallets"][identifier]
            self.save_data()
            return True, f"✅ Removed *{name}*"
        
        # Chercher par nom
        for addr, data in user["wallets"].items():
            if data["name"].lower() == identifier.lower():
                name = data["name"]
                del user["wallets"][addr]
                self.save_data()
                return True, f"✅ Removed *{name}*"
        
        return False, "❌ Wallet not found"

# ===== CORE DU BOT =====
db = WalletDatabase()

async def fetch_transactions(address, limit=5):
    """Récupérer les transactions d'une adresse"""
    try:
        url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={HELIUS_API_KEY}&limit={limit}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    return await response.json()
    except Exception as e:
        print(f"API Error: {e}")
    
    return []

def format_alert(tx, wallet_name, wallet_address):
    """Créer un message d'alerte"""
    tx_id = tx.get("signature", "")[:12] + "..."
    tx_type = tx.get("type", "TRANSACTION").replace("_", " ").title()
    
    # Emoji selon le type
    if "SWAP" in tx_type:
        emoji = "🔀"
    elif "NFT" in tx_type:
        emoji = "🖼️"
    elif "TRANSFER" in tx_type:
        emoji = "💸"
    else:
        emoji = "🔍"
    
    # Timestamp
    timestamp = tx.get("timestamp")
    if timestamp:
        time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
    else:
        time_str = "Just now"
    
    # Montant SOL si disponible
    amount_info = ""
    if "nativeTransfers" in tx and tx["nativeTransfers"]:
        transfer = tx["nativeTransfers"][0]
        amount = transfer.get("amount", 0) / 1e9
        if amount > 0:
            amount_info = f"💰 *Amount:* {amount:.4f} SOL\n"
    
    message = f"""
{emoji} *New Activity*

📛 *Wallet:* {wallet_name}
📝 *TX:* `{tx_id}`
📊 *Type:* {tx_type}
⏰ *Time:* {time_str}
{amount_info}🔗 [View on Solscan](https://solscan.io/tx/{tx.get('signature', '')})

📍 `{wallet_address[:10]}...`
    """
    
    return message.strip()

# ===== COMMANDES TELEGRAM =====
async def start_command(update: Update, context: CallbackContext):
    """Commande /start"""
    user = update.effective_user
    
    welcome = f"""
👋 Welcome *{user.first_name}*!

🤖 *Wallet Tracker Bot*
Track Solana wallet activity in real-time.

📋 *Quick Commands:*
/add <address> <name> - Track a wallet
/my - List your wallets  
/remove <name> - Remove a wallet
/check - Check now
/stats - Your statistics
/help - Detailed help

🔒 *Your data is private* - Only you see your wallets.
💰 *Free* - Track up to 10 wallets
🔔 *Auto-alerts* - Every 2 minutes

*Example:* `/add 9xQeWvG816bUx9EPpV9wYJqM8N9ZJmRkXkG3E6ZP8hX MainWallet`
    """
    
    await update.message.reply_text(welcome, parse_mode="Markdown")

async def add_command(update: Update, context: CallbackContext):
    """Commande /add"""
    user_id = update.effective_user.id
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "📝 *Add a Wallet*\n\n"
            "Usage: `/add <solana_address> <wallet_name>`\n\n"
            "*Example:*\n"
            "`/add 9xQeWvG816bUx9EPpV9wYJqM8N9ZJmRkXkG3E6ZP8hX MyWallet`\n\n"
            "*Note:* Max 10 wallets per user",
            parse_mode="Markdown"
        )
        return
    
    address = context.args[0]
    name = " ".join(context.args[1:])
    
    success, message = db.add_wallet(user_id, address, name)
    await update.message.reply_text(message, parse_mode="Markdown")

async def my_command(update: Update, context: CallbackContext):
    """Commande /my - Lister les wallets"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    
    if not user["wallets"]:
        await update.message.reply_text(
            "📭 *No wallets tracked yet.*\n\n"
            "Add your first wallet with:\n"
            "`/add <address> <name>`",
            parse_mode="Markdown"
        )
        return
    
    message = "📋 *Your Tracked Wallets:*\n\n"
    
    for address, data in user["wallets"].items():
        added_date = datetime.fromisoformat(data["added"]).strftime("%d/%m")
        message += f"• **{data['name']}**\n"
        message += f"  `{address[:14]}...{address[-6:]}`\n"
        message += f"  Added: {added_date} | TXs: {data['tx_count']}\n\n"
    
    message += f"📊 *Total:* {len(user['wallets'])}/10 wallets"
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def remove_command(update: Update, context: CallbackContext):
    """Commande /remove"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "🗑️ *Remove a Wallet*\n\n"
            "Usage: `/remove <wallet_name>`\n"
            "or: `/remove <partial_address>`\n\n"
            "*Example:* `/remove MyWallet`",
            parse_mode="Markdown"
        )
        return
    
    identifier = " ".join(context.args)
    success, message = db.remove_wallet(user_id, identifier)
    await update.message.reply_text(message, parse_mode="Markdown")

async def check_command(update: Update, context: CallbackContext):
    """Commande /check - Vérification manuelle"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    
    if not user["wallets"]:
        await update.message.reply_text("❌ No wallets to check. Add one with `/add`")
        return
    
    await update.message.reply_text("🔍 Checking for new transactions...")
    
    new_alerts = []
    
    for address, wallet_data in user["wallets"].items():
        transactions = await fetch_transactions(address, limit=5)
        
        for tx in transactions:
            tx_id = tx.get("signature")
            if not tx_id:
                continue
            
            # Vérifier si nouvelle transaction
            cache_key = f"{user_id}_{address}_{tx_id}"
            if cache_key not in db.tx_cache:
                db.tx_cache[cache_key] = datetime.now().isoformat()
                
                # Mettre à jour le compteur
                wallet_data["tx_count"] += 1
                wallet_data["last_tx"] = datetime.now().isoformat()
                user["alert_count"] += 1
                
                new_alerts.append((tx, wallet_data["name"], address))
    
    if new_alerts:
        db.save_data()
        
        # Envoyer les alertes (max 3 pour éviter le spam)
        for i, (tx, name, addr) in enumerate(new_alerts[:3]):
            message = format_alert(tx, name, addr)
            await update.message.reply_text(message, parse_mode="Markdown")
            await asyncio.sleep(1)
        
        if len(new_alerts) > 3:
            await update.message.reply_text(f"📨 +{len(new_alerts)-3} more transactions...")
    else:
        await update.message.reply_text("✅ No new transactions found.")

async def stats_command(update: Update, context: CallbackContext):
    """Commande /stats - Statistiques personnelles"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    
    # Calculer l'activité des dernières 24h
    recent_txs = 0
    for wallet in user["wallets"].values():
        if wallet.get("last_tx"):
            last_tx_time = datetime.fromisoformat(wallet["last_tx"])
            if datetime.now() - last_tx_time < timedelta(hours=24):
                recent_txs += wallet["tx_count"]
    
    stats_msg = f"""
📊 *Your Statistics*

👤 *User:* {update.effective_user.first_name}
📅 *Member since:* {datetime.fromisoformat(user['created']).strftime('%b %d, %Y')}

📈 *Tracking Stats:*
• Wallets: {len(user['wallets'])}/10
• Total Alerts: {user['alert_count']}
• 24h Activity: {recent_txs} TXs

🔔 *Auto Monitoring:*
• Interval: Every 2 minutes
• Status: ✅ Active
• Alerts: Real-time

💡 *Tips:*
• Use `/add` to track more wallets
• Use `/my` to see your wallets
• Max 10 wallets per user
    """
    
    await update.message.reply_text(stats_msg, parse_mode="Markdown")

async def help_command(update: Update, context: CallbackContext):
    """Commande /help"""
    help_text = """
🆘 *Wallet Tracker Bot - Help*

*📋 Available Commands:*
/start - Welcome message
/add <address> <name> - Track a wallet
/my - List your tracked wallets
/remove <name> - Remove a wallet
/check - Manual check for new transactions
/stats - Your personal statistics
/help - This help message

*🔒 How It Works:*
1. Each Telegram user has private data
2. You can track up to 10 Solana wallets
3. Bot checks every 2 minutes automatically
4. Real-time alerts for new transactions
5. Data is isolated - others can't see your wallets

*⚠️ Important Notes:*
• Bot monitors PUBLIC data only
• No access to private keys
• Free Helius API has limits (1000 requests/day)
• For best results, track max 5 wallets

*Example Usage:*
1. `/add 9xQeWvG816bUx9EPpV9wYJqM8N9ZJmRkXkG3E6ZP8hX MainWallet`
2. Wait for auto-alerts (every 2 minutes)
3. Use `/my` to see your wallets
4. Use `/remove MainWallet` to stop tracking

*Need Help?* Contact @DemkAaZa
    """
    
    await update.message.reply_text(help_text, parse_mode="Markdown")

# ===== SURVEILLANCE AUTOMATIQUE =====
async def auto_monitor(context: CallbackContext):
    """Vérification automatique toutes les 2 minutes"""
    print(f"⏰ Auto-check at {datetime.now().strftime('%H:%M:%S')}")
    
    for user_id_str, user_data in db.users.items():
        try:
            user_id = int(user_id_str)
            new_alerts = []
            
            for address, wallet_data in user_data["wallets"].items():
                transactions = await fetch_transactions(address, limit=5)
                
                for tx in transactions:
                    tx_id = tx.get("signature")
                    if not tx_id:
                        continue
                    
                    cache_key = f"{user_id_str}_{address}_{tx_id}"
                    if cache_key not in db.tx_cache:
                        db.tx_cache[cache_key] = datetime.now().isoformat()
                        
                        # Mettre à jour les stats
                        wallet_data["tx_count"] += 1
                        wallet_data["last_tx"] = datetime.now().isoformat()
                        user_data["alert_count"] += 1
                        
                        new_alerts.append((tx, wallet_data["name"], address))
            
            # Envoyer les alertes
            if new_alerts:
                db.save_data()
                
                for tx, name, addr in new_alerts[:3]:  # Max 3 alertes à la fois
                    message = format_alert(tx, name, addr)
                    
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=message,
                            parse_mode="Markdown",
                            disable_web_page_preview=True
                        )
                        await asyncio.sleep(1)  # Anti-spam
                    except Exception as e:
                        # Si l'utilisateur a bloqué le bot, le supprimer
                        if "blocked" in str(e).lower() or "forbidden" in str(e).lower():
                            print(f"User {user_id} blocked bot, removing...")
                            del db.users[user_id_str]
                            db.save_data()
                            break
        
        except Exception as e:
            print(f"Error monitoring user {user_id_str}: {e}")

# ===== MAIN =====
def main():
    """Fonction principale"""
    # Vérifications
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing!")
        return
    
    if not HELIUS_API_KEY:
        print("❌ HELIUS_API_KEY missing!")
        return
    
    print("\n" + "="*50)
    print("🤖 WALLET TRACKER PUBLIC BOT")
    print("="*50)
    print(f"🔑 Bot Token: ✅")
    print(f"🔧 API Key: ✅")
    print(f"👥 Multi-user: ✅")
    print(f"🔔 Auto-alerts: ✅ (every 2 minutes)")
    print(f"📊 Max wallets/user: 10")
    print("="*50)
    
    # Démarrer Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🌐 Web server: http://localhost:10000")
    
    # Créer l'application Telegram
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Ajouter les commandes
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler(["my", "list", "wallets"], my_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Configurer la surveillance automatique
    job_queue = application.job_queue
    job_queue.run_repeating(auto_monitor, interval=120, first=10)  # Toutes les 2 minutes
    
    print("✅ Bot started successfully!")
    print("👉 Open Telegram and search for your bot")
    print("👉 Use /start to begin")
    print("="*50)
    
    # Démarrer le bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
