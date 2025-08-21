import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import pytz
from datetime import datetime
import aiohttp
import re
import logging
import os

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Configuration ===
TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = 1225075859333845154  # Server ID
VOICE_CHANNEL_ID = 1262879963183317176  # Voice channel ID
TEXT_CHANNEL_ID = 1408154723189657661  # Text channel ID
SOUND_FILE = "sound.mp3"  # Sound file

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

COUNTRIES = [
    "Россия", "Германия", "Чехия", "Украина"
]

URLS = {
    "Россия": "https://autotraveler.ru/russia/#fuel",
    "Германия": "https://autotraveler.ru/germany/#fuel",
    "Чехия": "https://autotraveler.ru/czech/#fuel",
    "Украина": "https://autotraveler.ru/ukraine/#fuel"
}

# Country flags mapping
COUNTRY_FLAGS = {
    "Россия": "🇷🇺", "Германия": "🇩🇪", "Чехия": "🇨🇿", "Украина": "🇺🇦"
}

# Regex patterns that work with actual website HTML structure
PATTERNS = {
    "Россия": {
        "petrol": [
            r"АИ-95</span>.*?RUB.*?<span[^>]*>([\d\.,]+)</span>\s*\(€[^\d]*([\d\.,]+)\)",  # HTML with RUB and EUR
            r"АИ-95</span>.*?€[^\d]*([\d\.,]+)",  # EUR from HTML
            r"АИ-95.*?€\s*([\d\.,]+)",  # Fallback EUR
        ],
        "diesel": [
            r"ДТ</span>.*?RUB.*?<span[^>]*>([\d\.,]+)</span>\s*\(€[^\d]*([\d\.,]+)\)",  # HTML with RUB and EUR
            r"ДТ</span>.*?€[^\d]*([\d\.,]+)",  # EUR from HTML
            r"ДТ[^П].*?€\s*([\d\.,]+)",  # Fallback EUR (avoid ДТП)
        ],
    },
    "Германия": {
        "petrol": [
            r"Super\s*\(95\).*?€.*?<span[^>]*>([\d\.,]+)</span>",  # HTML span structure
            r"E10.*?€.*?<span[^>]*>([\d\.,]+)</span>",  # Alternative E10 pattern
            r"Super\s*\(95\).*?€.*?([\d\.,]+)",  # Fallback
        ],
        "diesel": [
            r"Diesel.*?€.*?<span[^>]*>([\d\.,]+)</span>",  # HTML span structure
            r"Diesel.*?€.*?([\d\.,]+)",  # Fallback
        ],
    },
    "Чехия": {
        "petrol": [
            r"Natural\s*95</span>.*?CZK.*?<span[^>]*>([\d\.,]+)</span>\s*\(€\s*([\d\.,]+)\)",  # HTML with CZK and EUR
            r"Natural\s*95</span>.*?€\s*([\d\.,]+)",  # EUR from HTML
            r"Natural\s*95.*?€\s*([\d\.,]+)",  # Fallback EUR
        ],
        "diesel": [
            r"Nafta</span>.*?CZK.*?<span[^>]*>([\d\.,]+)</span>\s*\(€\s*([\d\.,]+)\)",  # HTML with CZK and EUR
            r"Nafta</span>.*?€\s*([\d\.,]+)",  # EUR from HTML
            r"Nafta.*?€\s*([\d\.,]+)",  # Fallback EUR
        ],
    },
    "Украина": {
        "petrol": [
            r"АИ-95</span>.*?UAH.*?<span[^>]*>([\d\.,]+)</span>\s*\(€\s*([\d\.,]+)\)",  # HTML with UAH and EUR
            r"АИ-95.*?UAH.*?([\d\.,]+).*?\(€\s*([\d\.,]+)\)",  # Alternative pattern
            r"АИ-95.*?€\s*([\d\.,]+)",  # Fallback EUR only
        ],
        "diesel": [
            r"ДТ</span>.*?UAH.*?<span[^>]*>([\d\.,]+)</span>\s*\(€\s*([\d\.,]+)\)",  # HTML with UAH and EUR
            r"ДТ.*?UAH.*?([\d\.,]+).*?\(€\s*([\d\.,]+)\)",  # Alternative pattern
            r"ДТ[^П].*?€\s*([\d\.,]+)",  # Fallback EUR only
        ],
    }
}

# Country-specific currency information
COUNTRY_CURRENCIES = {
    "Россия": "RUB", "Германия": "EUR", "Чехия": "CZK", "Украина": "UAH"
}


# Enhanced patterns for better data extraction
def get_country_patterns(country):
    """Get regex patterns for a specific country"""
    if country in PATTERNS:
        return PATTERNS[country]

    # Improved patterns that focus on realistic price ranges and avoid date/time matches
    return {
        "petrol": [
            # More specific patterns that avoid large numbers and dates
            r"(?:Super|Petrol|Benzin|95|E5|SP95).*?€\s*([0-3]\.\d{1,3})",  # EUR prices 0-3.999
            r"(?:Super|Petrol|Benzin|95|E5|SP95).*?(\d{1,3}\.\d{1,3})\s*€",  # EUR prices reversed
            r"95.*?€\s*([0-3]\.\d{1,3})",  # 95 octane EUR
            r"бензин.*?([0-9]{1,3}\.[0-9]{1,3})",  # Local currency (more conservative)
        ],
        "diesel": [
            r"(?:Diesel|Dizel|Дизель).*?€\s*([0-3]\.\d{1,3})",  # EUR prices 0-3.999
            r"(?:Diesel|Dizel|Дизель).*?(\d{1,3}\.\d{1,3})\s*€",  # EUR prices reversed
            r"дизель.*?([0-9]{1,3}\.[0-9]{1,3})",  # Local currency
        ],
    }


def format_price(country, fuel_type, match, pattern):
    """Format price based on country and available data"""
    try:
        if not match:
            return "Нет данных"

        groups = match.groups()

        if country == "Россия":
            if len(groups) >= 2 and groups[1]:  # RUB with EUR
                rub_price = groups[0].replace(",", ".")
                eur_price = groups[1].replace(",", ".")
                return f"{rub_price} RUB (€{eur_price})"
            elif len(groups) >= 1 and groups[0]:  # Single currency
                price = groups[0].replace(",", ".")
                if "RUB" in pattern:
                    return f"{price} RUB"
                else:
                    return f"€{price}"

        elif country == "Германия":
            if len(groups) >= 1 and groups[0]:
                price = groups[0].replace(",", ".")
                return f"€{price}"

        elif country == "Чехия":
            if len(groups) >= 2 and groups[1]:  # CZK with EUR
                czk_price = groups[0].replace(",", ".")
                eur_price = groups[1].replace(",", ".")
                return f"{czk_price} CZK (€{eur_price})"
            elif len(groups) >= 1 and groups[0]:  # Single currency
                price = groups[0].replace(",", ".")
                if "CZK" in pattern:
                    return f"{price} CZK"
                else:
                    return f"€{price}"

        elif country == "Украина":
            if len(groups) >= 2 and groups[1]:  # UAH with EUR
                uah_price = groups[0].replace(",", ".")
                eur_price = groups[1].replace(",", ".")
                return f"{uah_price} UAH (€{eur_price})"
            elif len(groups) >= 1 and groups[0]:  # Single currency
                price = groups[0].replace(",", ".")
                if "UAH" in pattern:
                    return f"{price} UAH"
                else:
                    return f"€{price}"

        # Enhanced formatting for all other countries with local currency
        else:
            if len(groups) >= 1 and groups[0]:
                price = groups[0].replace(",", ".")
                currency = COUNTRY_CURRENCIES.get(country, "EUR")

                # For EUR countries and EUR prices detected
                if currency == "EUR" or "€" in pattern:
                    try:
                        price_float = float(price)
                        # Validate EUR price ranges
                        if 0.5 <= price_float <= 3.0:
                            return f"€{price}"
                        else:
                            return "Нет данных"  # Invalid EUR price range
                    except:
                        return f"€{price}"
                else:
                    # For non-EUR countries with local currency data
                    try:
                        price_float = float(price)
                        # Simple conversion estimates for our countries
                        eur_estimates = {
                            "RUB": price_float * 0.011,
                            "CZK": price_float * 0.041,
                            "UAH": price_float * 0.024
                        }

                        if currency in eur_estimates and price_float < 10000:  # Reasonable local currency check
                            eur_price = round(eur_estimates[currency], 3)
                            if 0.3 <= eur_price <= 5.0:  # Reasonable EUR equivalent range
                                return f"{price} {currency} (€{eur_price})"
                            else:
                                return "Нет данных"  # Invalid converted price
                        else:
                            return f"{price} {currency}"
                    except:
                        return f"{price} {currency}"

        return "Нет данных"
    except Exception as e:
        logger.error(f"Error formatting price for {country} {fuel_type}: {e}")
        return "Ошибка форматирования"


async def fetch_country_price(session, country):
    """Fetch fuel prices for a specific country"""
    url = URLS[country]
    patterns = get_country_patterns(country)
    result = {"petrol": "Нет данных", "diesel": "Нет данных"}

    try:
        async with session.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}) as resp:
            if resp.status != 200:
                logger.error(f"Failed to fetch {country}: HTTP {resp.status}")
                return result

            text = await resp.text()
            logger.info(f"Successfully fetched data for {country}")

            # Parse petrol prices
            for i, pattern in enumerate(patterns["petrol"]):
                match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if match:
                    result["petrol"] = format_price(country, "petrol", match, pattern)
                    logger.info(
                        f"{country} petrol found with pattern {i + 1}: {result['petrol']} (groups: {match.groups()})")
                    break
                else:
                    logger.debug(f"{country} petrol pattern {i + 1} failed")

            # Parse diesel prices
            for i, pattern in enumerate(patterns["diesel"]):
                match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if match:
                    result["diesel"] = format_price(country, "diesel", match, pattern)
                    logger.info(
                        f"{country} diesel found with pattern {i + 1}: {result['diesel']} (groups: {match.groups()})")
                    break
                else:
                    logger.debug(f"{country} diesel pattern {i + 1} failed")

            if result["petrol"] == "Нет данных":
                logger.warning(f"No petrol price found for {country}")
            if result["diesel"] == "Нет данных":
                logger.warning(f"No diesel price found for {country}")

    except asyncio.TimeoutError:
        logger.error(f"Timeout while fetching data for {country}")
    except Exception as e:
        logger.error(f"Error fetching data for {country}: {e}")

    return result


async def fetch_fuel_prices():
    """Fetch fuel prices for all countries"""
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        results = {}
        for country in COUNTRIES:
            logger.info(f"Fetching prices for {country}")
            results[country] = await fetch_country_price(session, country)
        return results


# Global variables
fuel_data = {}
last_price_message_id = None


async def connect_voice():
    """Connect to voice channel (simplified to reduce reconnection loops)"""
    try:
        await bot.wait_until_ready()
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            logger.error("Guild not found")
            return

        channel = guild.get_channel(VOICE_CHANNEL_ID)
        if channel and not guild.voice_client:
            await channel.connect()
            logger.info("Connected to voice channel")
    except Exception as e:
        logger.error(f"Failed to connect to voice channel: {e}")


async def play_sound():
    """Play notification sound"""
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    vc = guild.voice_client
    if vc and vc.is_connected() and not vc.is_playing():
        try:
            if os.path.exists(SOUND_FILE):
                vc.stop()
                vc.play(discord.FFmpegPCMAudio(SOUND_FILE))
                logger.info("Playing notification sound")
            else:
                logger.warning(f"Sound file {SOUND_FILE} not found")
        except Exception as e:
            logger.error(f"Error playing sound: {e}")


async def update_prices():
    """Update fuel prices from web sources"""
    global fuel_data
    try:
        logger.info("Updating fuel prices...")
        data = await fetch_fuel_prices()
        fuel_data.clear()
        fuel_data.update(data)
        logger.info("Fuel prices updated successfully")
    except Exception as e:
        logger.error(f"Error updating prices: {e}")


async def send_prices(channel, liters=1, new_message=False, edit_message=False):
    """Send fuel prices embed to channel"""
    global last_price_message_id

    if not fuel_data:
        embed = discord.Embed(
            title="⛽ Цены на топливо",
            color=0x00ffcc,
            description=""
        )
        embed.add_field(
            name="❌ Ошибка",
            value="Данные о ценах временно недоступны",
            inline=False
        )
        if new_message:
            await channel.send(embed=embed)
        return

    # Simple single embed for 4 countries
    embed = discord.Embed(
        title=f"⛽ Цены на топливо ({liters} л)",
        color=0x00ffcc,
        description=""
    )

    for country, values in fuel_data.items():
        petrol_price = values["petrol"]
        diesel_price = values["diesel"]

        # Calculate prices for multiple liters if needed
        if liters != 1 and petrol_price != "Нет данных" and "€" in petrol_price:
            eur_match = re.search(r"€([\d\.,]+)", petrol_price)
            if eur_match:
                try:
                    base_price = float(eur_match.group(1).replace(",", "."))
                    calculated_price = round(base_price * liters, 2)
                    petrol_price += f" → €{calculated_price} за {liters}л"
                except:
                    pass

        if liters != 1 and diesel_price != "Нет данных" and "€" in diesel_price:
            eur_match = re.search(r"€([\d\.,]+)", diesel_price)
            if eur_match:
                try:
                    base_price = float(eur_match.group(1).replace(",", "."))
                    calculated_price = round(base_price * liters, 2)
                    diesel_price += f" → €{calculated_price} за {liters}л"
                except:
                    pass

        flag = COUNTRY_FLAGS.get(country, "🏳️")
        embed.add_field(
            name=f"{flag} {country}",
            value=f"⛽ Бензин: **{petrol_price}**\n🛢 Дизель: **{diesel_price}**",
            inline=False
        )

    try:
        if new_message:
            msg = await channel.send(embed=embed)
            last_price_message_id = msg.id
    except Exception as e:
        logger.error(f"Error sending prices message: {e}")

def in_correct_text_channel():
    """Проверка, что команда используется в нужном текстовом канале"""
    async def predicate(interaction: discord.Interaction):
        if interaction.channel_id == TEXT_CHANNEL_ID:
            return True
        await interaction.response.send_message(
            f"❌ Эта команда доступна только в текстовом канале <#{TEXT_CHANNEL_ID}>!",
            ephemeral=True
        )
        return False
    return app_commands.check(predicate)
    
    
# Removed auto-reconnection loop to prevent constant reconnection issues

@tasks.loop(minutes=1)
async def midnight_task():
    """Task that runs at midnight Moscow time"""
    tz = pytz.timezone("Europe/Moscow")
    now = datetime.now(tz)
    if now.hour == 0 and now.minute == 0 and now.second < 30:
        logger.info("Midnight task triggered")
        await asyncio.sleep(15)  # Wait a bit to ensure proper execution
        await update_prices()
        channel = bot.get_channel(TEXT_CHANNEL_ID)
        if channel:
            await send_prices(channel, new_message=True)
            await play_sound()


@bot.tree.command(name="price", description="Узнать стоимость топлива на выбранное количество литров")
@app_commands.describe(liters="Количество литров")
@in_correct_text_channel()
async def price(interaction: discord.Interaction, liters: float):
    """Slash command to get fuel prices"""
    if liters <= 0:
        await interaction.response.send_message("Количество литров должно быть больше 0!", ephemeral=True)
        return

    if liters > 1000:
        await interaction.response.send_message("Слишком большое количество литров! Максимум 1000л.", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        await send_prices(interaction.followup, liters=liters, new_message=True)
        await play_sound()
    except Exception as e:
        logger.error(f"Error in price command: {e}")
        await interaction.followup.send("Произошла ошибка при получении цен на топливо.")


@bot.tree.command(name="update", description="Принудительно обновить цены на топливо")
@in_correct_text_channel()
async def update_command(interaction: discord.Interaction):
    """Slash command to manually update prices"""
    await interaction.response.defer()

    try:
        await update_prices()
        await send_prices(interaction.followup, new_message=True)
        await play_sound()
    except Exception as e:
        logger.error(f"Error in update command: {e}")
        await interaction.followup.send("Произошла ошибка при обновлении цен.")


@bot.event
async def on_ready():
    """Bot ready event"""
    try:
        logger.info(f"Бот {bot.user} запущен! ID: {bot.user.id}")

        # Синхронизация команд
        try:
            # Сначала синхронизируем для гильдии
            bot.tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
            synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            logger.info(f"Синхронизировано {len(synced)} команд: {[cmd.name for cmd in synced]}")
        except Exception as sync_error:
            logger.error(f"Ошибка синхронизации для гильдии: {sync_error}")
            # Попробуем глобальную синхронизацию
            try:
                synced = await bot.tree.sync()
                logger.info(f"Глобально синхронизировано {len(synced)} команд")
            except Exception as global_error:
                logger.error(f"Ошибка глобальной синхронизации: {global_error}")

        # Подключение к голосовому каналу
        await connect_voice()

        # Запуск задачи
        midnight_task.start()

        # Первоначальная загрузка цен
        await update_prices()
        channel = bot.get_channel(TEXT_CHANNEL_ID)
        if channel:
            await send_prices(channel, new_message=True)
            await play_sound()

        logger.info("Бот полностью готов к работе!")

    except Exception as e:
        logger.error(f"Критическая ошибка в on_ready: {e}")


@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state updates"""
    if member.id == bot.user.id:
        if after.channel is None or after.channel.id != VOICE_CHANNEL_ID:
            await asyncio.sleep(2)
            await connect_voice()


@bot.event
async def on_error(event, *args, **kwargs):
    """Global error handler"""
    logger.error(f"Error in event {event}: {args}")


if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

@bot.command()
@commands.is_owner()
async def sync(ctx):
    """Принудительная синхронизация команд"""
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"Синхронизировано {len(synced)} команд: {[cmd.name for cmd in synced]}")
        logger.info(f"Синхронизировано команд: {len(synced)}")
    except Exception as e:
        await ctx.send(f"Ошибка синхронизации: {e}")
        logger.error(f"Sync error: {e}")

