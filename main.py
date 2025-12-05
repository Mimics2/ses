import os
import logging
import asyncio
import random
import json
from pathlib import Path
import qrcode
from io import BytesIO
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from aiogram import Bot, Dispatcher, Router, F
    from aiogram.types import Message, CallbackQuery, BufferedInputFile
    from aiogram.filters import Command, CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import (
        SessionPasswordNeededError, 
        PhoneCodeInvalidError,
        PhoneNumberInvalidError,
        FloodWaitError,
        PhoneCodeExpiredError
    )
except ImportError as e:
    print(f"❌ Missing dependencies: {e}")
    exit(1)

# Конфигурация
BOT_TOKEN = os.environ.get('BOT_TOKEN')
API_ID = int(os.environ.get('API_ID', '2040'))
API_HASH = os.environ.get('API_HASH', 'b18441a1ff607e10a989891a5462e627')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))  # ID администратора

# Файл для хранения белого списка
WHITELIST_FILE = Path("whitelist.json")

class WhiteListManager:
    """Менеджер белого списка пользователей"""
    
    def __init__(self):
        self.whitelist = set()
        self.load_whitelist()
    
    def load_whitelist(self):
        """Загружает белый список из файла"""
        try:
            if WHITELIST_FILE.exists():
                with open(WHITELIST_FILE, 'r') as f:
                    data = json.load(f)
                    self.whitelist = set(data.get('users', []))
                    logger.info(f"📋 Загружен белый список: {len(self.whitelist)} пользователей")
            else:
                self.whitelist = set()
                self.save_whitelist()
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки белого списка: {e}")
            self.whitelist = set()
    
    def save_whitelist(self):
        """Сохраняет белый список в файл"""
        try:
            with open(WHITELIST_FILE, 'w') as f:
                json.dump({'users': list(self.whitelist)}, f, indent=2)
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения белого списка: {e}")
    
    def is_allowed(self, user_id: int) -> bool:
        """Проверяет, есть ли пользователь в белом списке"""
        return user_id in self.whitelist
    
    def add_user(self, user_id: int) -> bool:
        """Добавляет пользователя в белый список"""
        if user_id not in self.whitelist:
            self.whitelist.add(user_id)
            self.save_whitelist()
            return True
        return False
    
    def remove_user(self, user_id: int) -> bool:
        """Удаляет пользователя из белого списка"""
        if user_id in self.whitelist:
            self.whitelist.remove(user_id)
            self.save_whitelist()
            return True
        return False
    
    def get_all_users(self) -> list:
        """Возвращает список всех пользователей в белом списке"""
        return sorted(list(self.whitelist))

class SessionStates(StatesGroup):
    METHOD = State()

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Менеджеры
whitelist_manager = WhiteListManager()

class WorkingSessionManager:
    def __init__(self):
        self.active_sessions = {}
        self.user_messages = {}
    
    async def create_qr_session(self, user_id: int, message: Message):
        """Создание QR-сессии и немедленный старт отслеживания"""
        try:
            # Закрываем старую сессию если есть
            if user_id in self.active_sessions:
                try:
                    await self.active_sessions[user_id]['client'].disconnect()
                except:
                    pass
            
            devices = [
                {
                    "device_model": "Samsung SM-G991B",
                    "system_version": "Android 13",
                    "app_version": "10.0.0",
                },
                {
                    "device_model": "iPhone15,3", 
                    "system_version": "iOS 17.1.2",
                    "app_version": "10.0.0",
                }
            ]
            
            device = random.choice(devices)
            
            client = TelegramClient(StringSession(), API_ID, API_HASH, **device)
            await client.connect()
            
            # Создаем QR-логин
            qr_login = await client.qr_login()
            
            self.active_sessions[user_id] = {
                'client': client,
                'qr_login': qr_login,
                'created_at': datetime.now(),
                'message': message
            }
            
            self.user_messages[user_id] = message
            
            return True, qr_login.url
            
        except Exception as e:
            logger.error(f"QR creation error: {e}")
            return False, f"❌ Ошибка создания QR: {str(e)}"
    
    async def start_qr_monitoring(self, user_id: int):
        """Запуск мониторинга статуса QR-авторизации"""
        if user_id not in self.active_sessions:
            return
        
        data = self.active_sessions[user_id]
        message = data['message']
        
        try:
            status_msg = await message.answer("⏳ Ожидаем сканирование QR-кода...")
            
            logger.info(f"🔄 Начало ожидания QR для пользователя {user_id}")
            
            await asyncio.wait_for(data['qr_login'].wait(), timeout=120)
            logger.info(f"✅ QR код отсканирован для пользователя {user_id}")
            
            await status_msg.edit_text("✅ QR-код отсканирован! Проверяем авторизацию...")
            await asyncio.sleep(3)
            
            is_authorized = await data['client'].is_user_authorized()
            logger.info(f"🔐 Статус авторизации для {user_id}: {is_authorized}")
            
            if not is_authorized:
                await status_msg.edit_text("❌ Авторизация не завершена. Подтвердите вход в Telegram.")
                return
            
            await status_msg.edit_text("✅ Авторизация успешна! Создаем сессию...")
            
            session_string = data['client'].session.save()
            logger.info(f"📦 Сессия создана для {user_id}")
            
            session_bytes = session_string.encode('utf-8')
            session_file = BufferedInputFile(session_bytes, filename="telegram_session.txt")
            
            await message.answer_document(
                document=session_file,
                caption="✅ **Сессия успешно создана!**\n\n"
                       "💾 Сохраните этот файл\n"
                       "🔒 Он дает полный доступ к аккаунту"
            )
            
            await message.answer(f"📋 **Session String:**\n```\n{session_string}\n```")
            logger.info(f"🎉 Сессия отправлена пользователю {user_id}")
            
        except asyncio.TimeoutError:
            logger.warning(f"⏰ Таймаут QR для пользователя {user_id}")
            if user_id in self.user_messages:
                await self.user_messages[user_id].answer("❌ Время ожидания истекло. QR-код не был отсканирован.")
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга QR для {user_id}: {e}")
            if user_id in self.user_messages:
                await self.user_messages[user_id].answer(f"❌ Ошибка: {str(e)}")
        finally:
            await self.cleanup_session(user_id)
    
    async def cleanup_session(self, user_id: int):
        """Очистка сессии"""
        if user_id in self.active_sessions:
            try:
                await self.active_sessions[user_id]['client'].disconnect()
            except:
                pass
            del self.active_sessions[user_id]
        
        if user_id in self.user_messages:
            del self.user_messages[user_id]

manager = WorkingSessionManager()

# Мидлварь для проверки белого списка
@router.message.middleware
async def whitelist_middleware(handler, event: Message, data: dict):
    """Проверяет доступ пользователя к боту"""
    user_id = event.from_user.id
    
    # Команды, доступные всем
    public_commands = ['start', 'help']
    
    # Если это команда и она публичная - пропускаем
    if event.text and event.text.startswith('/'):
        cmd = event.text[1:].split(' ')[0].lower()
        if cmd in public_commands:
            return await handler(event, data)
    
    # Проверяем доступ
    if not whitelist_manager.is_allowed(user_id):
        await event.answer(
            "⛔ **Доступ запрещен!**\n\n"
            "Вы не находитесь в белом списке пользователей.\n"
            "Обратитесь к администратору для получения доступа."
        )
        return
    
    return await handler(event, data)

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    await state.clear()
    
    # Проверяем доступ
    if not whitelist_manager.is_allowed(user_id):
        builder = InlineKeyboardBuilder()
        builder.button(text="📞 Связаться с администратором", url=f"tg://user?id={ADMIN_ID}")
        builder.adjust(1)
        
        await message.answer(
            "👋 **Добро пожаловать!**\n\n"
            "Этот бот доступен только для пользователей из белого списка.\n"
            "Для получения доска обратитесь к администратору.",
            reply_markup=builder.as_markup()
        )
        return
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📷 Создать сессию через QR-код", callback_data="method_qr")
    builder.adjust(1)
    
    await message.answer(
        "🔐 **Генератор сессий Telegram**\n\n"
        "Создайте сессию для вашего аккаунта через QR-код.\n"
        "После сканирования **сессия придет автоматически**.",
        reply_markup=builder.as_markup()
    )

@router.callback_query(F.data == "method_qr")
async def handle_qr_method(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    # Проверяем доступ
    if not whitelist_manager.is_allowed(user_id):
        await callback.answer("⛔ У вас нет доступа!", show_alert=True)
        return
    
    await callback.answer()
    await callback.message.edit_text("🔄 Создаем QR-код...")
    
    success, qr_url = await manager.create_qr_session(user_id, callback.message)
    
    if success:
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_url)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        bio = BytesIO()
        img.save(bio, 'PNG')
        bio.seek(0)
        
        qr_file = BufferedInputFile(bio.getvalue(), filename="qr_code.png")
        
        await callback.message.answer_photo(
            photo=qr_file,
            caption="📷 **QR-код для подключения:**\n\n"
                   "1. Откройте Telegram → Настройки\n"
                   "2. Устройства → Подключить устройство\n"
                   "3. Отсканируйте этот QR-код\n"
                   "4. **Подтвердите вход** в приложении\n\n"
                   "⏳ Ожидаем 2 минуты...\n"
                   "✅ Сессия придет автоматически после подключения"
        )
        
        asyncio.create_task(manager.start_qr_monitoring(user_id))
        
    else:
        await callback.message.edit_text(f"❌ {qr_url}")

# ===== КОМАНДЫ АДМИНИСТРАТОРА =====

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    return user_id == ADMIN_ID

@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message):
    """Показывает список пользователей в белом списке"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администратору!")
        return
    
    users = whitelist_manager.get_all_users()
    
    if not users:
        await message.answer("📋 Белый список пуст")
        return
    
    user_list = "\n".join([f"• `{user_id}`" for user_id in users])
    await message.answer(
        f"📋 **Белый список пользователей** ({len(users)}):\n\n{user_list}\n\n"
        f"Используйте:\n"
        f"`/add <user_id>` - добавить пользователя\n"
        f"`/remove <user_id>` - удалить пользователя"
    )

@router.message(Command("add"))
async def cmd_add_user(message: Message):
    """Добавляет пользователя в белый список"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администратору!")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.answer("❌ Использование: `/add <user_id>`")
            return
        
        user_id = int(args[1])
        
        if whitelist_manager.add_user(user_id):
            await message.answer(f"✅ Пользователь `{user_id}` добавлен в белый список")
        else:
            await message.answer(f"ℹ️ Пользователь `{user_id}` уже в белом списке")
    
    except ValueError:
        await message.answer("❌ Неверный формат ID. ID должен быть числом")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@router.message(Command("remove"))
async def cmd_remove_user(message: Message):
    """Удаляет пользователя из белого списка"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администратору!")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.answer("❌ Использование: `/remove <user_id>`")
            return
        
        user_id = int(args[1])
        
        if whitelist_manager.remove_user(user_id):
            await message.answer(f"✅ Пользователь `{user_id}` удален из белого списка")
        else:
            await message.answer(f"ℹ️ Пользователь `{user_id}` не найден в белом списке")
    
    except ValueError:
        await message.answer("❌ Неверный формат ID. ID должен быть числом")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@router.message(Command("check"))
async def cmd_check(message: Message):
    """Проверка статуса сессии"""
    user_id = message.from_user.id
    
    if not whitelist_manager.is_allowed(user_id):
        await message.answer("⛔ У вас нет доступа!")
        return
    
    if user_id in manager.active_sessions:
        created_time = manager.active_sessions[user_id]['created_at']
        time_passed = datetime.now() - created_time
        await message.answer(f"🔄 Сессия активна\n⏰ Прошло: {int(time_passed.total_seconds())} сек")
    else:
        await message.answer("❌ Нет активной сессии\n🔄 Используйте /start")

@router.message(Command("debug"))
async def cmd_debug(message: Message):
    """Отладочная информация"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администратору!")
        return
    
    user_id = message.from_user.id
    if user_id in manager.active_sessions:
        data = manager.active_sessions[user_id]
        try:
            is_auth = await data['client'].is_user_authorized()
            await message.answer(f"🔧 Debug:\nAuth: {is_auth}\nClient: {data['client'].session}")
        except Exception as e:
            await message.answer(f"🔧 Debug Error: {e}")
    else:
        await message.answer("❌ Нет активной сессии")

@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "🔐 **Помощь по генератору сессий**\n\n"
        "Как использовать:\n"
        "1. Нажмите /start\n"
        "2. Нажмите 'Создать сессию через QR-код'\n"
        "3. Отсканируйте QR-код в Telegram\n"
        "4. **Обязательно подтвердите вход** в приложении\n"
        "5. **Сессия придет автоматически**\n\n"
        "Команды:\n"
        "/start - начать создание сессии\n"
        "/check - проверить статус\n"
        "/help - эта справка\n\n"
        "⚠️ **Важно:** После сканирования нужно нажать 'Подключить' в Telegram!"
    )
    
    # Администраторам показываем дополнительные команды
    if is_admin(message.from_user.id):
        help_text += (
            "\n\n🔧 **Команды администратора:**\n"
            "/whitelist - показать белый список\n"
            "/add <user_id> - добавить пользователя\n"
            "/remove <user_id> - удалить пользователя\n"
            "/debug - отладочная информация"
        )
    
    await message.answer(help_text)

async def main():
    logger.info("🚀 Starting Working QR Session Bot with Whitelist...")
    logger.info(f"👑 Администратор: {ADMIN_ID}")
    logger.info(f"📋 Пользователей в белом списке: {len(whitelist_manager.whitelist)}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
	
