import logging
from datetime import datetime, timedelta
import copy

# Библиотеки для работы с Google Sheets API
import gspread
from google.oauth2.service_account import Credentials

# Библиотеки для работы с Telegram API
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8373760158:AAHhSP7B8zabVoM07iOB-Rz19KuaeLMOYRM" 
# Имя файла с учетными данными Google
GOOGLE_CREDS_FILE = 'tournament_bot\\credentials.json'
ADMIN_ID = 824172699  # <--- ВАЖНО: ВСТАВЬТЕ СЮДА ВАШ TELEGRAM ID
SPREADSHEET_NAME = 'Заявки на турнир'
ADMIN_USERNAME = '@jozzikjo'

# Названия листов
SHEET_APPLICATIONS = 'Заявки'
SHEET_TEAMS = 'Команды'
SHEET_SCHEDULE = 'Расписание'

# Время жизни кэша и частота проверки таблиц в секундах
CACHE_TTL = 55
JOB_INTERVAL = 60

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- СОСТОЯНИЯ ДИАЛОГА И КОНСТАНТЫ ---
CHOOSE_ROLE, MAIN_MENU, ADMIN_MENU, ASK_LOBBY_ID = range(4)
(
    SELECT_TOURNAMENT, GET_NAME, GET_INGAME_NAME, GET_LOG_LINK, SELECT_RANK,
    SELECT_PRIMARY_ROLE, SELECT_SECONDARY_ROLES, GET_CHAMPIONS, CONFIRM_DATA
) = range(4, 13)

TOURNAMENTS = {"tourn_1": "Классический турнир №1, 2025-08-20", "tourn_2": "Турнир ARAM, 2025-09-05"}
RANKS = ["Iron", "Bronze", "Silver", "Gold", "Platinum", "Emerald", "Diamond", "Master", "Grandmaster", "Challenger"]
ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]

COLUMN_ORDER_APPS = [
    "Telegram ID", "Telegram Username", "Турнир", "Имя", "Никнейм в игре", "Ссылка на LeagueOfGraphs",
    "Ранг", "Основная роль", "Дополнительные роли", "Чемпионы Top", "Чемпионы Jungle",
    "Чемпионы Mid", "Чемпионы ADC", "Чемпионы Support", "Timestamp"
]

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С GOOGLE SHEETS И КЭШЕМ ---
async def get_sheet_data(context: ContextTypes.DEFAULT_TYPE, sheet_name: str, force_refresh: bool = False) -> list[dict] | None:
    """Получает данные из Google Sheets, используя кэш."""
    cache_key = f'cache_{sheet_name}'
    cache_time_key = f'cache_time_{sheet_name}'
    now = datetime.now()
    if not force_refresh:
        cached_data = context.bot_data.get(cache_key)
        cache_time = context.bot_data.get(cache_time_key)
        if cached_data is not None and cache_time and (now - cache_time) < timedelta(seconds=CACHE_TTL):
            return cached_data
    
    logger.info(f"Fetching fresh data for sheet: {sheet_name}")
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return None
    try:
        sheet = spreadsheet.worksheet(sheet_name)
        records = sheet.get_all_records()
        context.bot_data[cache_key] = records
        context.bot_data[cache_time_key] = now
        return records
    except gspread.exceptions.WorksheetNotFound:
        logger.error(f"Лист '{sheet_name}' не найден.")
        context.bot_data[cache_key] = []
        context.bot_data[cache_time_key] = now
        return []
    except Exception as e:
        logger.error(f"Не удалось получить данные из листа '{sheet_name}': {e}")
        return None

def get_spreadsheet():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open(SPREADSHEET_NAME)
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")
        return None

# --- КОД АНКЕТЫ ---
def update_or_append_row(user_data: dict):
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return False
    sheet = spreadsheet.worksheet(SHEET_APPLICATIONS)
    user_id = user_data["id"]
    try: cell = sheet.find(str(user_id), in_column=1)
    except gspread.CellNotFound: cell = None
    champion_data = {f"Чемпионы {role}": "" for role in ROLES}
    for role, champs in user_data.get("champions", {}).items(): champion_data[f"Чемпионы {role}"] = champs
    row_data = [
        user_id, user_data.get("username", ""), user_data.get("tournament", ""), user_data.get("name", ""),
        user_data.get("ingame_name", ""), user_data.get("log_link", ""), user_data.get("rank", ""),
        user_data.get("primary_role", ""), ", ".join(user_data.get("secondary_roles", [])),
        champion_data["Чемпионы Top"], champion_data["Чемпионы Jungle"], champion_data["Чемпионы Mid"],
        champion_data["Чемпионы ADC"], champion_data["Чемпионы Support"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ]
    try:
        if sheet.row_values(1) != COLUMN_ORDER_APPS: sheet.insert_row(COLUMN_ORDER_APPS, 1)
    except (gspread.exceptions.APIError, IndexError): sheet.insert_row(COLUMN_ORDER_APPS, 1)
    if cell: sheet.update(f"A{cell.row}:O{cell.row}", [row_data])
    else: sheet.append_row(row_data, value_input_option='USER_ENTERED')
    return True
async def send_or_edit(update: Update, text: str, reply_markup: InlineKeyboardMarkup):
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else: await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = f"Выбран турнир: {context.user_data['tournament']}.\n\nКак тебя зовут?"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_start_reg")]])
    await send_or_edit(update, text, keyboard)
async def ask_ingame_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Введи свой никнейм в игре."
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_name")]])
    await send_or_edit(update, text, keyboard)
async def ask_log_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Отправь ссылку на свой профиль на leagueofgraphs.com."
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_ingame_name")]])
    await send_or_edit(update, text, keyboard)
async def ask_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [[InlineKeyboardButton(rank, callback_data=rank)] for rank in RANKS]
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_log_link")])
    keyboard = InlineKeyboardMarkup(buttons)
    await send_or_edit(update, "Выбери свой текущий ранг:", keyboard)
async def ask_primary_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [[InlineKeyboardButton(role, callback_data=f"primary_{role}")] for role in ROLES]
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_rank")])
    keyboard = InlineKeyboardMarkup(buttons)
    text = f"Ранг: {context.user_data['rank']}.\n\nШаг 1: Выбери **основную** роль."
    await send_or_edit(update, text, keyboard)
async def ask_secondary_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    primary_role = context.user_data['primary_role']
    selected_secondary = context.user_data.get('selected_secondary_roles', [])
    available_roles = [r for r in ROLES if r != primary_role]
    buttons = []
    for r in available_roles:
        mark = "✅" if r in selected_secondary else "☐"
        buttons.append([InlineKeyboardButton(f"{mark} {r}", callback_data=f"secondary_{r}")])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_primary_role"), InlineKeyboardButton("✅ Готово", callback_data="secondary_done")])
    keyboard = InlineKeyboardMarkup(buttons)
    text = f"Основная роль: {primary_role}.\n\nШаг 2: Выбери **до двух** дополнительных ролей (это не обязательно)."
    await send_or_edit(update, text, keyboard)
async def ask_champions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role_index = context.user_data.get('current_role_index', 0)
    all_roles = [context.user_data['primary_role']] + context.user_data.get('secondary_roles', [])
    current_role = all_roles[role_index]
    text = f"Введите **через запятую** 3-5 своих основных чемпионов для роли: **{current_role}**"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_champions")]])
    await send_or_edit(update, text, keyboard)
async def show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_info = context.user_data
    champions_str = ""
    for role, champs in user_info.get('champions', {}).items(): champions_str += f"\n  - {role}: {champs}"
    summary_text = f"**Проверь свою анкету:**\n\n- **Турнир**: {user_info.get('tournament', 'Не указан')}\n- **Имя**: {user_info.get('name', 'Не указано')}\n- **Никнейм**: {user_info.get('ingame_name', 'Не указан')}\n- **Ссылка**: {user_info.get('log_link', 'Не указана')}\n- **Ранг**: {user_info.get('rank', 'Не указан')}\n- **Основная роль**: {user_info.get('primary_role', 'Не указана')}\n- **Доп. роли**: {', '.join(user_info.get('secondary_roles', []))}\n- **Чемпионы**:{champions_str}\n\nВсё верно?"
    buttons = [[InlineKeyboardButton("✅ Да, все верно", callback_data="confirm_yes")], [InlineKeyboardButton("⬅️ Изменить", callback_data="back_to_last_champion"), InlineKeyboardButton("❌ Начать заново", callback_data="confirm_no")]]
    keyboard = InlineKeyboardMarkup(buttons)
    await send_or_edit(update, summary_text, keyboard)
async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    buttons = [[InlineKeyboardButton(text, callback_data=key)] for key, text in TOURNAMENTS.items()]
    keyboard = InlineKeyboardMarkup(buttons)
    await update.callback_query.edit_message_text("Выбери турнир для регистрации:", reply_markup=keyboard)
    return SELECT_TOURNAMENT
async def select_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); context.user_data['tournament'] = TOURNAMENTS[query.data]
    await ask_name(update, context); return GET_NAME
async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text; await ask_ingame_name(update, context); return GET_INGAME_NAME
async def get_ingame_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ingame_name'] = update.message.text; await ask_log_link(update, context); return GET_LOG_LINK
async def get_log_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['log_link'] = update.message.text; await ask_rank(update, context); return SELECT_RANK
async def select_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); context.user_data['rank'] = query.data
    await ask_primary_role(update, context); return SELECT_PRIMARY_ROLE
async def select_primary_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); role = query.data.split('_')[1]
    context.user_data['primary_role'] = role; context.user_data['selected_secondary_roles'] = []
    await ask_secondary_roles(update, context); return SELECT_SECONDARY_ROLES
async def select_secondary_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); callback_data = query.data
    selected = context.user_data.get('selected_secondary_roles', [])
    if callback_data == "secondary_done":
        context.user_data['secondary_roles'] = selected
        context.user_data.pop('selected_secondary_roles', None)
        context.user_data['champions'] = {}
        context.user_data['current_role_index'] = 0
        all_roles_to_fill = [context.user_data['primary_role']] + context.user_data.get('secondary_roles', [])
        if not all_roles_to_fill:
             await show_summary(update, context); return CONFIRM_DATA
        await ask_champions(update, context); return GET_CHAMPIONS
    role = callback_data.split('_')[1]
    if role not in selected:
        selected.append(role)
    else:
        selected.remove(role)
    context.user_data['selected_secondary_roles'] = selected; await ask_secondary_roles(update, context); return SELECT_SECONDARY_ROLES
async def get_champions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    champions_list = [champ.strip() for champ in update.message.text.split(',') if champ.strip()]
    if not (3 <= len(champions_list) <= 5): await update.message.reply_text("Нужно указать от 3 до 5 чемпионов."); return GET_CHAMPIONS
    all_roles = [context.user_data['primary_role']] + context.user_data.get('secondary_roles', [])
    current_index = context.user_data['current_role_index']
    current_role = all_roles[current_index]; context.user_data.setdefault('champions', {})[current_role] = ", ".join(champions_list)
    context.user_data['current_role_index'] += 1
    if context.user_data['current_role_index'] < len(all_roles): await ask_champions(update, context); return GET_CHAMPIONS
    else: await show_summary(update, context); return CONFIRM_DATA
async def confirm_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data == "confirm_yes":
        user = query.from_user; context.user_data['id'] = user.id; context.user_data['username'] = user.username
        if update_or_append_row(context.user_data):
            context.bot_data.pop(f'cache_{SHEET_APPLICATIONS}', None) # Сбрасываем кэш заявок
            await query.edit_message_text("Отлично, твоя заявка принята!")
            await show_participant_menu(update, context) # Переход в меню участника
            return MAIN_MENU
        else:
            await query.edit_message_text(f"Произошла ошибка, обратитесь к {ADMIN_USERNAME}.")
            return ConversationHandler.END
    else: # confirm_no
        await query.edit_message_text("Начинаем сначала."); return await back_to_start(update, context)
async def back_to_start_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_registration(update, context); return SELECT_TOURNAMENT
async def back_to_name(update: Update, context: ContextTypes.DEFAULT_TYPE): await ask_name(update, context); return GET_NAME
async def back_to_ingame_name(update: Update, context: ContextTypes.DEFAULT_TYPE): await ask_ingame_name(update, context); return GET_INGAME_NAME
async def back_to_log_link(update: Update, context: ContextTypes.DEFAULT_TYPE): await ask_log_link(update, context); return GET_LOG_LINK
async def back_to_rank(update: Update, context: ContextTypes.DEFAULT_TYPE): await ask_rank(update, context); return SELECT_RANK
async def back_to_primary_role(update: Update, context: ContextTypes.DEFAULT_TYPE): await ask_primary_role(update, context); return SELECT_PRIMARY_ROLE
async def back_to_champions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('current_role_index', 0) == 0: await ask_secondary_roles(update, context); return SELECT_SECONDARY_ROLES
    else: context.user_data['current_role_index'] -= 1; await ask_champions(update, context); return GET_CHAMPIONS
async def back_to_last_champion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_roles = [context.user_data['primary_role']] + context.user_data.get('secondary_roles', [])
    context.user_data['current_role_index'] = len(all_roles) - 1
    await ask_champions(update, context); return GET_CHAMPIONS
# --- КОНЕЦ СЕКЦИИ КОДА АНКЕТЫ ---

# --- ФУНКЦИИ ТУРНИРНОГО ХАБА ---
async def get_all_players_data(context: ContextTypes.DEFAULT_TYPE) -> dict:
    records = await get_sheet_data(context, SHEET_APPLICATIONS)
    if records is None: return {}
    return {str(rec["Telegram ID"]): rec for rec in records}
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[InlineKeyboardButton("👤 Я участник", callback_data="role_participant")], [InlineKeyboardButton("👀 Я зритель", callback_data="role_spectator")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text("Добро пожаловать на турнир! Кто вы?", reply_markup=reply_markup)
    else: await update.callback_query.edit_message_text("Добро пожаловать на турнир! Кто вы?", reply_markup=reply_markup)
    return CHOOSE_ROLE
async def choose_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if query.data == "role_participant":
        all_players = await get_all_players_data(context)
        if str(query.from_user.id) in all_players:
            await show_participant_menu(update, context)
            return MAIN_MENU
        return await start_registration(update, context)
    elif query.data == "role_spectator":
        await show_spectator_menu(update, context)
        return MAIN_MENU
    return CHOOSE_ROLE
async def show_spectator_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏆 Составы команд", callback_data="menu_all_teams")], [InlineKeyboardButton("📅 Расписание игр", callback_data="menu_schedule")]]
    if update.effective_user.id == ADMIN_ID: keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="menu_admin")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_start")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text("Меню зрителя:", reply_markup=reply_markup)
async def show_participant_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🤝 Моя команда", callback_data="menu_my_team")], [InlineKeyboardButton("🏆 Все составы", callback_data="menu_all_teams")], [InlineKeyboardButton("📅 Расписание игр", callback_data="menu_schedule")]]
    if update.effective_user.id == ADMIN_ID: keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="menu_admin")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_start")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text("Меню участника:", reply_markup=reply_markup)
async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await start(update, context)
async def show_all_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    teams_data = await get_sheet_data(context, SHEET_TEAMS)
    players_data = await get_all_players_data(context)
    if teams_data is None or players_data is None: await query.message.reply_text("Ошибка получения данных."); return
    if not teams_data: await query.edit_message_text("Составы команд еще не сформированы.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]])); return
    response_text = "Составы команд:\n\n"
    for team in teams_data:
        response_text += f"**Команда: {team['Название команды']}**\n"
        player_ids = [str(team[key]) for key in team if key.startswith('Игрок') and team[key]]
        for pid in player_ids:
            player = players_data.get(pid)
            if player:
                telegram_username = player.get('Telegram Username')
                telegram_link = f"(@{telegram_username})" if telegram_username else ""
                primary_role = player.get('Основная роль', 'Не указ.')
                secondary_roles = player.get('Дополнительные роли', '')
                roles_text = f"  Роли: {primary_role}"
                if secondary_roles: roles_text += f" ({secondary_roles})"
                response_text += f"- `{player['Никнейм в игре']}` {telegram_link} - [Профиль]({player['Ссылка на LeagueOfGraphs']})\n{roles_text}\n"
            else:
                response_text += f"- `Игрок с ID {pid} не найден`\n"
        response_text += "\n"
    await query.edit_message_text(response_text, parse_mode='Markdown', disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]]))
async def show_my_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    user_id = str(query.from_user.id)
    all_teams = await get_sheet_data(context, SHEET_TEAMS)
    players_data = await get_all_players_data(context)
    if all_teams is None or players_data is None: await query.message.reply_text("Ошибка получения данных."); return
    user_team = next((team for team in all_teams if user_id in [str(team.get(key)) for key in team if key.startswith('Игрок')]), None)
    if not user_team: await query.edit_message_text("Вы еще не состоите в команде.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]])); return
    response_text = f"**Ваша команда: {user_team['Название команды']}**\n\nСостав:\n"
    team_player_ids = [str(user_team[key]) for key in user_team if key.startswith('Игрок') and user_team[key]]
    for pid in team_player_ids:
        player = players_data.get(pid)
        if player:
            telegram_username = player.get('Telegram Username')
            telegram_link = f"(@{telegram_username})" if telegram_username else ""
            primary_role = player.get('Основная роль', 'Не указ.')
            secondary_roles = player.get('Дополнительные роли', '')
            roles_text = f"  Роли: {primary_role}"
            if secondary_roles: roles_text += f" ({secondary_roles})"
            response_text += f"- `{player['Никнейм в игре']}` {telegram_link} - [Профиль]({player['Ссылка на LeagueOfGraphs']})\n{roles_text}\n"
    await query.edit_message_text(response_text, parse_mode='Markdown', disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]]))
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    schedule_data = await get_sheet_data(context, SHEET_SCHEDULE)
    if schedule_data is None: await query.message.reply_text("Ошибка получения данных."); return
    if not schedule_data: await query.edit_message_text("Расписание игр еще не составлено.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]])); return
    response_text = "📅 **Расписание игр:**\n\n"
    for match in schedule_data: response_text += f"**{match['Команда 1']} vs {match['Команда 2']}**\n**Время:** {match['Дата и время']}\n[Ссылка на стрим]({match['Ссылка на стрим']})\n\n"
    await query.edit_message_text(response_text, parse_mode='Markdown', disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]]))
async def back_to_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    all_players = await get_all_players_data(context)
    if str(query.from_user.id) in all_players: await show_participant_menu(update, context)
    else: await show_spectator_menu(update, context)
    return MAIN_MENU
async def my_team_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    all_players = await get_all_players_data(context)
    if user_id not in all_players: await update.message.reply_text("Эта команда доступна только для зарегистрированных участников.\nПройдите регистрацию через /start."); return
    all_teams = await get_sheet_data(context, SHEET_TEAMS)
    if all_teams is None: await update.message.reply_text("Ошибка получения данных."); return
    user_team = next((team for team in all_teams if user_id in [str(team.get(key)) for key in team if key.startswith('Игрок')]), None)
    if not user_team: await update.message.reply_text("Вы еще не состоите в команде."); return
    response_text = f"**Ваша команда: {user_team['Название команды']}**\n\nСостав:\n"
    team_player_ids = [str(user_team[key]) for key in user_team if key.startswith('Игрок') and user_team[key]]
    for pid in team_player_ids:
        player = all_players.get(pid)
        if player:
            telegram_username = player.get('Telegram Username')
            telegram_link = f"(@{telegram_username})" if telegram_username else ""
            primary_role = player.get('Основная роль', 'Не указ.')
            secondary_roles = player.get('Дополнительные роли', '')
            roles_text = f"  Роли: {primary_role}"
            if secondary_roles: roles_text += f" ({secondary_roles})"
            response_text += f"- `{player['Никнейм в игре']}` {telegram_link} - [Профиль]({player['Ссылка на LeagueOfGraphs']})\n{roles_text}\n"
    await update.message.reply_text(response_text, parse_mode='Markdown', disable_web_page_preview=True)
async def all_teams_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teams_data = await get_sheet_data(context, SHEET_TEAMS)
    players_data = await get_all_players_data(context)
    if teams_data is None or players_data is None: await update.message.reply_text("Ошибка получения данных."); return
    if not teams_data: await update.message.reply_text("Составы команд еще не сформированы."); return
    response_text = "Составы команд:\n\n"
    for team in teams_data:
        response_text += f"**Команда: {team['Название команды']}**\n"
        player_ids = [str(team[key]) for key in team if key.startswith('Игрок') and team[key]]
        for pid in player_ids:
            player = players_data.get(pid)
            if player:
                telegram_username = player.get('Telegram Username')
                telegram_link = f"(@{telegram_username})" if telegram_username else ""
                primary_role = player.get('Основная роль', 'Не указ.')
                secondary_roles = player.get('Дополнительные роли', '')
                roles_text = f"  Роли: {primary_role}"
                if secondary_roles: roles_text += f" ({secondary_roles})"
                response_text += f"- `{player['Никнейм в игре']}` {telegram_link} - [Профиль]({player['Ссылка на LeagueOfGraphs']})\n{roles_text}\n"
            else:
                response_text += f"- `Игрок с ID {pid} не найден`\n"
        response_text += "\n"
    await update.message.reply_text(response_text, parse_mode='Markdown', disable_web_page_preview=True)
async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    schedule_data = await get_sheet_data(context, SHEET_SCHEDULE)
    if schedule_data is None: await update.message.reply_text("Ошибка получения данных."); return
    if not schedule_data: await update.message.reply_text("Расписание игр еще не составлено."); return
    response_text = "📅 **Расписание игр:**\n\n"
    for match in schedule_data: response_text += f"**{match['Команда 1']} vs {match['Команда 2']}**\n**Время:** {match['Дата и время']}\n[Ссылка на стрим]({match['Ссылка на стрим']})\n\n"
    await update.message.reply_text(response_text, parse_mode='Markdown', disable_web_page_preview=True)

# --- АДМИНСКИЕ ФУНКЦИИ ---
async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    keyboard = [
        [InlineKeyboardButton("📢 Разослать составы", callback_data="admin_notify_teams")],
        [InlineKeyboardButton("🔑 Отправить код лобби", callback_data="admin_ask_lobby_id")],
        [InlineKeyboardButton("⬅️ Назад в главное меню", callback_data="back_to_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("⚙️ **Админ-панель**", reply_markup=reply_markup, parse_mode='Markdown')
    return ADMIN_MENU
async def handle_notify_teams(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.message.reply_text("Начинаю рассылку уведомлений о составах...")
    context.bot_data.pop(f'cache_{SHEET_TEAMS}', None)
    context.bot_data.pop(f'cache_{SHEET_APPLICATIONS}', None)
    teams_data = await get_sheet_data(context, SHEET_TEAMS)
    players_data = await get_all_players_data(context)
    if teams_data is None or players_data is None: await query.message.reply_text("Ошибка получения данных."); return ADMIN_MENU
    if not teams_data: await query.message.reply_text("Лист 'Команды' пуст."); return ADMIN_MENU
    sent_count, errors_count = 0, 0
    for team in teams_data:
        team_name = team['Название команды']
        player_ids = [str(team[key]) for key in team if key.startswith('Игрок') and team[key]]
        teammates_info = [f"- `{players_data.get(pid, {}).get('Никнейм в игре', 'Неизвестно')}` ({players_data.get(pid, {}).get('Ранг', '?')})" for pid in player_ids]
        message_text = f"Вас распределили в команду **{team_name}**!\n\nВаши тиммейты:\n" + "\n".join(teammates_info)
        for pid in player_ids:
            try:
                await context.bot.send_message(chat_id=int(pid), text=message_text, parse_mode='Markdown')
                sent_count += 1
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение {pid}: {e}"); errors_count += 1
    await query.message.reply_text(f"Рассылка завершена!\nОтправлено: {sent_count}\nОшибок: {errors_count}")
    return ADMIN_MENU
async def ask_lobby_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("Введите ID матча, для которого нужно отправить код лобби:")
    return ASK_LOBBY_ID
async def handle_lobby_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try: match_id = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Неверный формат. Введите только число - ID матча."); return ASK_LOBBY_ID
    await update.message.reply_text(f"Ищу матч с ID {match_id} и готовлю рассылку...")
    context.bot_data.pop(f'cache_{SHEET_SCHEDULE}', None)
    context.bot_data.pop(f'cache_{SHEET_TEAMS}', None)
    all_matches = await get_sheet_data(context, SHEET_SCHEDULE)
    all_teams = await get_sheet_data(context, SHEET_TEAMS)
    if all_matches is None or all_teams is None:
        await update.message.reply_text("Ошибка получения данных из таблиц."); return ADMIN_MENU
    target_match = next((m for m in all_matches if m['ID Матча'] == match_id), None)
    if not target_match:
        await update.message.reply_text(f"Матч с ID {match_id} не найден."); return ADMIN_MENU
    lobby_code = target_match.get('Код лобби')
    if not lobby_code:
        await update.message.reply_text(f"Для матча ID {match_id} не указан код лобби в таблице."); return ADMIN_MENU
    team1_name, team2_name = target_match['Команда 1'], target_match['Команда 2']
    team1_ids, team2_ids = [], []
    for team in all_teams:
        if team['Название команды'] == team1_name: team1_ids = [str(team[key]) for key in team if key.startswith('Игрок') and team[key]]
        if team['Название команды'] == team2_name: team2_ids = [str(team[key]) for key in team if key.startswith('Игрок') and team[key]]
    if not team1_ids or not team2_ids:
        await update.message.reply_text("Не удалось найти одну из команд."); return ADMIN_MENU
    message_team1 = f"Ваш матч скоро начнется!\n\n**Соперник:** {team2_name}\n**Код лобби:** `{lobby_code}`"
    message_team2 = f"Ваш матч скоро начнется!\n\n**Соперник:** {team1_name}\n**Код лобби:** `{lobby_code}`"
    sent_count = 0
    for pid in team1_ids + team2_ids:
        message = message_team1 if pid in team1_ids else message_team2
        try: await context.bot.send_message(int(pid), message, parse_mode='Markdown'); sent_count += 1
        except Exception as e: logger.error(f"Ошибка отправки лобби {pid}: {e}")
    await update.message.reply_text(f"Код лобби для матча ID {match_id} разослан {sent_count} участникам.")
    keyboard = [[InlineKeyboardButton("📢 Разослать составы", callback_data="admin_notify_teams")], [InlineKeyboardButton("🔑 Отправить код лобби", callback_data="admin_ask_lobby_id")], [InlineKeyboardButton("⬅️ Назад в главное меню", callback_data="back_to_menu")]]
    await update.message.reply_text("⚙️ **Админ-панель**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ADMIN_MENU

# --- ФОНОВЫЕ ЗАДАЧИ И УВЕДОМЛЕНИЯ ---
def get_player_info_string(player_data: dict) -> str:
    """Форматирует информацию об игроке в красивую строку."""
    if not player_data:
        return "Неизвестный игрок"
    
    telegram_username = player_data.get('Telegram Username')
    telegram_link = f"(@{telegram_username})" if telegram_username else ""
    primary_role = player_data.get('Основная роль', 'N/A')
    secondary_roles = player_data.get('Дополнительные роли', '')
    roles_text = f"{primary_role}"
    if secondary_roles:
        roles_text += f" ({secondary_roles})"

    return (f"`{player_data.get('Никнейм в игре', 'N/A')}` {telegram_link}\n"
            f"Ранг: {player_data.get('Ранг', 'N/A')}, Роли: {roles_text}")

async def check_sheet_updates(context: ContextTypes.DEFAULT_TYPE):
    """Основная фоновая задача, проверяющая изменения в таблицах."""
    logger.info("Job: Checking for sheet updates...")
    
    # --- 1. Проверка замен в командах ---
    old_teams = context.bot_data.get('teams_snapshot', [])
    new_teams = await get_sheet_data(context, SHEET_TEAMS, force_refresh=True)
    if new_teams is not None and old_teams:
        all_players = await get_all_players_data(context)
        # Сравниваем старые и новые составы
        for i, new_team in enumerate(new_teams):
            if i < len(old_teams):
                old_team = old_teams[i]
                if new_team != old_team: # Если строка команды изменилась
                    old_ids = {str(old_team[key]) for key in old_team if key.startswith('Игрок')}
                    new_ids = {str(new_team[key]) for key in new_team if key.startswith('Игрок')}
                    
                    removed_ids = old_ids - new_ids
                    added_ids = new_ids - old_ids
                    
                    if removed_ids and added_ids:
                        removed_player_info = get_player_info_string(all_players.get(list(removed_ids)[0]))
                        added_player_info = get_player_info_string(all_players.get(list(added_ids)[0]))
                        
                        message = (f"📢 **Замена в команде «{new_team['Название команды']}»**\n\n"
                                   f"Ушёл игрок:\n{removed_player_info}\n\n"
                                   f"Пришёл игрок:\n{added_player_info}")
                        
                        # Рассылка всем капитанам
                        captains = [str(team['Игрок 1 (ID)']) for team in new_teams if team.get('Игрок 1 (ID)')]
                        for captain_id in captains:
                            try:
                                await context.bot.send_message(chat_id=captain_id, text=message, parse_mode='Markdown')
                            except Exception as e:
                                logger.error(f"Failed to send substitution notification to captain {captain_id}: {e}")

    context.bot_data['teams_snapshot'] = copy.deepcopy(new_teams)

    # --- 2. Проверка новых и измененных матчей ---
    old_schedule = context.bot_data.get('schedule_snapshot', [])
    new_schedule = await get_sheet_data(context, SHEET_SCHEDULE, force_refresh=True)
    if new_schedule is not None:
        old_match_ids = {str(m['ID Матча']) for m in old_schedule}
        
        for match in new_schedule:
            match_id_str = str(match['ID Матча'])
            
            # Новый матч
            if match_id_str not in old_match_ids and match.get('Статус') == 'Планируется':
                logger.info(f"New match found: {match_id_str}")
                team1_name = match.get('Команда 1')
                team2_name = match.get('Команда 2')
                if not team1_name or not team2_name: continue
                
                all_teams = await get_sheet_data(context, SHEET_TEAMS)
                if all_teams is None: continue
                
                team1_ids, team2_ids = [], []
                for team in all_teams:
                    if team['Название команды'] == team1_name: team1_ids = [str(team[key]) for key in team if key.startswith('Игрок') and team[key]]
                    if team['Название команды'] == team2_name: team2_ids = [str(team[key]) for key in team if key.startswith('Игрок') and team[key]]
                
                participant_ids = team1_ids + team2_ids
                message = (f"📅 **Анонс нового матча!**\n\n"
                           f"**{team1_name} vs {team2_name}**\n\n"
                           f"**Время:** {match.get('Дата и время', 'Не указано')}\n"
                           f"Следите за обновлениями!")
                for user_id in participant_ids:
                    try: await context.bot.send_message(chat_id=user_id, text=message, parse_mode='Markdown')
                    except Exception as e: logger.error(f"Failed to send new match notification to {user_id}: {e}")
            
            # Изменение статуса существующего матча
            else:
                old_match = next((m for m in old_schedule if str(m['ID Матча']) == match_id_str), None)
                if old_match and old_match.get('Статус') != match.get('Статус'):
                    all_users = await get_all_players_data(context)
                    
                    # Матч начался
                    if match.get('Статус') == 'В процессе':
                        message = (f"🔥 **Матч начался!**\n\n"
                                   f"**{match['Команда 1']} vs {match['Команда 2']}**\n\n"
                                   f"Смотрите трансляцию: {match['Ссылка на стрим']}")
                        for user_id in all_users.keys():
                            try: await context.bot.send_message(user_id, message, parse_mode='Markdown')
                            except Exception as e: logger.error(f"Failed to send match start notification to {user_id}: {e}")
                    
                    # Матч завершен
                    elif match.get('Статус') == 'Завершен':
                        caption = (f"🏁 **Матч завершён!**\n\n"
                                   f"**{match['Команда 1']} vs {match['Команда 2']}**\n\n"
                                   f"Результат: **{match.get('Результат', 'Не указан')}**")
                        photo_id = match.get('Скриншот пиков (ID)')
                        for user_id in all_users.keys():
                            try:
                                if photo_id: await context.bot.send_photo(user_id, photo=photo_id, caption=caption, parse_mode='Markdown')
                                else: await context.bot.send_message(user_id, caption, parse_mode='Markdown')
                            except Exception as e: logger.error(f"Failed to send match end notification to {user_id}: {e}")


    context.bot_data['schedule_snapshot'] = copy.deepcopy(new_schedule)

# --- АДМИНСКИЕ КОМАНДЫ ДЛЯ ИЗОБРАЖЕНИЙ ---
async def handle_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает получение фото от админа с командами в подписи."""
    if not update.message or not update.message.photo or update.effective_user.id != ADMIN_ID:
        return
    
    caption = update.message.caption
    if not caption:
        return
        
    # --- Команда для добавления скриншота пиков ---
    if caption.lower().startswith('/add_picks'):
        try:
            match_id = int(caption.split()[1])
        except (IndexError, ValueError):
            await update.message.reply_text("Неверный формат. Используйте: `/add_picks [ID матча]` в подписи к фото.")
            return
            
        spreadsheet = get_spreadsheet()
        if not spreadsheet: await update.message.reply_text("Ошибка подключения к таблице."); return
        
        try:
            sheet = spreadsheet.worksheet(SHEET_SCHEDULE)
            cell = sheet.find(str(match_id), in_column=1) # Ищем матч по ID
            if not cell:
                await update.message.reply_text(f"Матч с ID {match_id} не найден."); return
            
            # Находим столбец "Скриншот пиков (ID)"
            headers = sheet.row_values(1)
            try:
                col_index = headers.index('Скриншот пиков (ID)') + 1
            except ValueError:
                await update.message.reply_text("В таблице 'Расписание' отсутствует столбец 'Скриншот пиков (ID)'."); return
            
            photo_id = update.message.photo[-1].file_id
            sheet.update_cell(cell.row, col_index, photo_id)
            await update.message.reply_text(f"✅ Скриншот пиков успешно добавлен к матчу ID {match_id}.")
            
        except Exception as e:
            await update.message.reply_text(f"Произошла ошибка при работе с таблицей: {e}")

    # --- Команда для рассылки сетки ---
    elif caption.lower() == '/send_bracket':
        await update.message.reply_text("Начинаю рассылку турнирной сетки...")
        all_players = await get_all_players_data(context)
        if not all_players:
            await update.message.reply_text("Нет зарегистрированных участников для рассылки."); return
            
        photo_id = update.message.photo[-1].file_id
        sent_count, errors_count = 0, 0
        for user_id in all_players.keys():
            try:
                await context.bot.send_photo(chat_id=user_id, photo=photo_id, caption="🏆 Актуальная турнирная сетка")
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send bracket to {user_id}: {e}"); errors_count += 1
        
        await update.message.reply_text(f"Сетка разослана!\nОтправлено: {sent_count}\nОшибок: {errors_count}")

async def post_init(application: Application):
    """Устанавливает кнопку меню и запускает фоновые задачи."""
    commands = [
        BotCommand("start", "Главное меню / Регистрация"),
        BotCommand("myteam", "Посмотреть мою команду (участникам)"),
        BotCommand("teams", "Посмотреть все команды"),
        BotCommand("schedule", "Посмотреть расписание игр"),
    ]
    await application.bot.set_my_commands(commands)
    if application.job_queue:
        application.job_queue.run_repeating(check_sheet_updates, interval=JOB_INTERVAL, first=10)

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_ROLE: [CallbackQueryHandler(choose_role, pattern="^role_")],
            MAIN_MENU: [
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                CallbackQueryHandler(show_all_teams, pattern="^menu_all_teams$"),
                CallbackQueryHandler(show_my_team, pattern="^menu_my_team$"),
                CallbackQueryHandler(show_schedule, pattern="^menu_schedule$"),
                CallbackQueryHandler(back_to_menu_handler, pattern="^back_to_menu$"),
                CallbackQueryHandler(show_admin_menu, pattern="^menu_admin$"),
            ],
            ADMIN_MENU: [
                CallbackQueryHandler(handle_notify_teams, pattern="^admin_notify_teams$"),
                CallbackQueryHandler(ask_lobby_id, pattern="^admin_ask_lobby_id$"),
                CallbackQueryHandler(back_to_menu_handler, pattern="^back_to_menu$"),
            ],
            ASK_LOBBY_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lobby_id_input)],
            SELECT_TOURNAMENT: [CallbackQueryHandler(select_tournament)],
            GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name), CallbackQueryHandler(back_to_start_reg, pattern="^back_to_start_reg$")],
            GET_INGAME_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ingame_name), CallbackQueryHandler(back_to_name, pattern="^back_to_name$")],
            GET_LOG_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_log_link), CallbackQueryHandler(back_to_ingame_name, pattern="^back_to_ingame_name$")],
            SELECT_RANK: [CallbackQueryHandler(select_rank, pattern=f"^(?!back_to_log_link).*$"), CallbackQueryHandler(back_to_log_link, pattern="^back_to_log_link$")],
            SELECT_PRIMARY_ROLE: [CallbackQueryHandler(select_primary_role, pattern="^primary_"), CallbackQueryHandler(back_to_rank, pattern="^back_to_rank$")],
            SELECT_SECONDARY_ROLES: [CallbackQueryHandler(select_secondary_roles, pattern="^secondary_"), CallbackQueryHandler(back_to_primary_role, pattern="^back_to_primary_role$")],
            GET_CHAMPIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_champions), CallbackQueryHandler(back_to_champions, pattern="^back_to_champions$")],
            CONFIRM_DATA: [CallbackQueryHandler(confirm_data, pattern="^confirm_"), CallbackQueryHandler(back_to_last_champion, pattern="^back_to_last_champion$")],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("myteam", my_team_command))
    application.add_handler(CommandHandler("teams", all_teams_command))
    application.add_handler(CommandHandler("schedule", schedule_command))
    # ИСПРАВЛЕНО: Правильный фильтр для фото с подписью
    application.add_handler(MessageHandler(filters.PHOTO & filters.CAPTION, handle_admin_photo))
    
    print("Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()
