import os
import logging
import asyncio
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from supabase import create_client, Client
import google.generativeai as genai
import requests

# --- CONFIGURACIÓN DESDE VARIABLES DE ENTORNO ---
# Estas variables las configuraremos en Render
TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM")
API_KEY_GEMINI = os.getenv("API_KEY_GEMINI")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
# Para publicar en Facebook/Instagram (Opcional por ahora)
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
ID_PAGINA_FACEBOOK = os.getenv("ID_PAGINA_FACEBOOK")

# Inicializar clientes
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=API_KEY_GEMINI)
model = genai.GenerativeModel('gemini-1.5-flash')

# Estados de la conversación
ESPERANDO_FOTO, ESPERANDO_ELECCION = range(2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍕 ¡Hola Jefe! Soy Nano Banana. Mándame una foto de la pizza y te doy ideas."
    )
    return ESPERANDO_FOTO

async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    await update.message.reply_text("👀 Recibido. Subiendo a la nube y pensando ideas...")

    # 1. Obtener la foto de Telegram
    photo_file = await update.message.photo[-1].get_file()
    byte_array = await photo_file.download_as_bytearray()
    
    # 2. Subir a Supabase Storage
    # Nombre único para el archivo
    file_name = f"{user.id}_{photo_file.file_unique_id}.jpg"
    bucket_name = "fotos-pizza"
    
    try:
        # Subimos el archivo (reemplaza si existe)
        supabase.storage.from_(bucket_name).upload(
            path=file_name, 
            file=byte_array, 
            file_options={"content-type": "image/jpeg", "upsert": "true"}
        )
        # Obtenemos la URL pública
        public_url = supabase.storage.from_(bucket_name).get_public_url(file_name)
    except Exception as e:
        await update.message.reply_text(f"Error subiendo imagen: {e}")
        return ConversationHandler.END

    # 3. Generar textos con Gemini (IA)
    prompt = """
    Actúa como 'Nano Banana', experto en marketing de pizzerías. 
    Analiza esta imagen y dame 3 opciones de texto para Facebook/Instagram.
    Separa las opciones EXACTAMENTE con '|||'.
    Ejemplo: Texto 1... ||| Texto 2... ||| Texto 3...
    """
    
    # Gemini 1.5 Flash puede leer imágenes desde bytes
    response = model.generate_content([
        {'mime_type': 'image/jpeg', 'data': byte_array},
        prompt
    ])
    
    texto_generado = response.text
    # Separamos las opciones
    try:
        opciones = texto_generado.split('|||')
        # Limpieza básica
        if len(opciones) < 3:
            opciones = [texto_generado, "Opción 2 genérica", "Opción 3 genérica"]
    except:
        opciones = [texto_generado]

    opcion_1 = opciones[0].strip()
    opcion_2 = opciones[1].strip() if len(opciones) > 1 else "N/A"
    opcion_3 = opciones[2].strip() if len(opciones) > 2 else "N/A"

    # 4. Guardar en Base de Datos Supabase (La Memoria)
    data = {
        "chat_id": user.id,
        "photo_url": public_url,
        "opcion_1": opcion_1,
        "opcion_2": opcion_2,
        "opcion_3": opcion_3,
        "estado": "esperando"
    }
    supabase.table("publicaciones").insert(data).execute()

    # 5. Mostrar al usuario
    msg = f"🍌 **Ideas Nano Banana:**\n\n1️⃣: {opcion_1[:100]}...\n\n2️⃣: {opcion_2[:100]}...\n\n3️⃣: {opcion_3[:100]}...\n\n👇 **Elige 1, 2 o 3:**"
    keyboard = [["1", "2", "3"], ["Cancelar"]]
    
    await update.message.reply_text(msg, reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))
    return ESPERANDO_ELECCION

async def publicar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eleccion = update.message.text
    if eleccion not in ["1", "2", "3"]:
        await update.message.reply_text("Elige 1, 2 o 3.")
        return ESPERANDO_ELECCION

    await update.message.reply_text(f"🚀 Publicando opción {eleccion}...")
    
    # 1. Recuperar info de la BD
    user_id = update.effective_user.id
    response = supabase.table("publicaciones").select("*").eq("chat_id", user_id).order("id", desc=True).limit(1).execute()
    
    if not response.data:
        await update.message.reply_text("No encontré la foto. Mándala de nuevo.")
        return ESPERANDO_FOTO
        
    registro = response.data[0]
    texto_final = registro[f"opcion_{eleccion}"]
    foto_url = registro["photo_url"]
    
    # 2. PUBLICAR EN FACEBOOK (Requiere Token)
    # Si tienes el token configurado, esto funcionará:
    if META_ACCESS_TOKEN and ID_PAGINA_FACEBOOK:
        try:
            url_fb = f"https://graph.facebook.com/{ID_PAGINA_FACEBOOK}/photos"
            payload = {
                'url': foto_url,
                'caption': texto_final,
                'access_token': META_ACCESS_TOKEN
            }
            r = requests.post(url_fb, data=payload)
            if r.status_code == 200:
                await update.message.reply_text("✅ ¡Publicado en Facebook!")
            else:
                await update.message.reply_text(f"⚠️ Error Facebook: {r.text}")
        except Exception as e:
            await update.message.reply_text(f"Error conexión FB: {e}")
    else:
        await update.message.reply_text("⚠️ Modo Simulación: No hay Token de Facebook configurado. Pero el texto y foto están listos.")

    await update.message.reply_text("🎉 ¡Listo Jefe! Mándame otra pizza.")
    return ESPERANDO_FOTO

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelado.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    application = Application.builder().token(TOKEN_TELEGRAM).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), MessageHandler(filters.PHOTO, recibir_foto)],
        states={
            ESPERANDO_FOTO: [MessageHandler(filters.PHOTO, recibir_foto)],
            ESPERANDO_ELECCION: [MessageHandler(filters.Regex("^(1|2|3)$"), publicar)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    application.add_handler(conv_handler)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()