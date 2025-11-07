import json
import logging
import re
from uuid import uuid4
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, CallbackContext, CallbackQueryHandler,
    ConversationHandler
)
from telegram.error import BadRequest

# Configuration
TOKEN = "8133269730:AAH-KtZkzwdrWXYli63FWcy0SknH5bTm8g4"
ADMIN_ID = 1903179151
PAYMENT_METHODS = {
    "mtn": "+237652586999",
    "orange": "+237658723403"
}

# États de conversation
BROWSING, WAITING_PAYMENT_PROOF, WAITING_ADMIN_LINKS = range(3)

# Données initiales
catalog = {
    "films": [],
    "series": [],
    "transactions": {}
}

# Initialisation du logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Fonction pour charger le catalogue ---
def load_catalog():
    global catalog
    try:
        with open("catalog.json", "r", encoding='utf-8') as f:
            data = json.load(f)
            catalog["films"] = data.get("films", [])
            catalog["series"] = data.get("series", [])
            logger.info(f"Catalogue chargé : {len(catalog['films'])} films, {len(catalog['series'])} séries.")
    except FileNotFoundError:
        logger.warning("catalog.json non trouvé. Le catalogue est vide.")
    except json.JSONDecodeError:
        logger.error("Erreur de décodage JSON dans catalog.json. Vérifiez la syntaxe.")
    except Exception as e:
        logger.error(f"Erreur lors du chargement du catalogue : {e}")

# ==================== FONCTIONS ADMIN ====================
async def handle_admin_document(update: Update, context: CallbackContext):
    """Reçoit et traite le fichier JSON de l'admin"""
    user = update.message.from_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("❌ Accès réservé à l'administrateur.")
        return

    if update.message.document.mime_type != "application/json":
        await update.message.reply_text("❌ Veuillez envoyer un fichier JSON valide pour le catalogue.")
        return

    file = await update.message.document.get_file()
    file_path = "catalog.json"
    await file.download_to_drive(file_path)

    global catalog
    try:
        with open(file_path, "r", encoding='utf-8') as f:
            new_catalog = json.load(f)
            if "films" in new_catalog and "series" in new_catalog:
                catalog["films"] = new_catalog["films"]
                catalog["series"] = new_catalog["series"]
                await update.message.reply_text(f"✅ Catalogue mis à jour avec {len(catalog['films'])} films et {len(catalog['series'])} séries !")
            else:
                await update.message.reply_text("❌ Le fichier JSON ne contient pas les clés 'films' ou 'series'.")
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Erreur lors de la lecture du fichier JSON. Le format est incorrect.")
    except Exception as e:
        await update.message.reply_text(f"❌ Une erreur inattendue est survenue : {e}")

async def send_access_links(update: Update, context: CallbackContext):
    """Permet à l'admin d'envoyer les liens d'accès manuellement"""
    user = update.message.from_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("❌ Accès réservé à l'administrateur.")
        return

    # Format attendu: /send_links transaction_id lien1 lien2 ...
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Format: /send_links <transaction_id> <lien1> <lien2> ...")
        return

    transaction_id = args[0]
    links = args[1:]
    
    transaction = catalog["transactions"].get(transaction_id)
    if not transaction:
        await update.message.reply_text("❌ Transaction introuvable.")
        return

    if transaction.get("status") != "pending_links":
        await update.message.reply_text("❌ Cette transaction n'est pas en attente de liens.")
        return

    user_id = transaction["user_id"]
    item_title = transaction["item_title"]

    # Mettre à jour la transaction
    transaction["status"] = "completed"
    transaction["access_links"] = links

    # Envoyer les liens au client
    client_text = (
        f"✅ Votre paiement pour *{item_title}* a été validé !\n\n"
        f"Voici vos liens d'accès :\n\n"
    )
    
    for i, link in enumerate(links, 1):
        client_text += f"🔗 [Lien {i}]({link})\n"
    
    client_text += "\n🎬 Bon visionnage !"

    try:
        await context.bot.send_message(
            user_id,
            client_text,
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ Liens envoyés au client {user_id} pour la transaction {transaction_id}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Impossible d'envoyer les liens au client {user_id}. Erreur: {e}")
        logger.error(f"Erreur en envoyant les liens au client {user_id}: {e}")

async def approve_transaction(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    transaction_id = query.data.split("_")[-1]
    transaction = catalog["transactions"].get(transaction_id)

    if not transaction:
        try:
            await query.edit_message_text("Transaction introuvable.")
        except BadRequest:
            pass
        return

    if transaction.get("status") != "pending":
        try:
            await query.edit_message_text(f"Cette transaction a déjà été {transaction.get('status')}.")
        except BadRequest:
            pass
        return

    # Changement: On passe en attente de liens au lieu d'approuver directement
    transaction["status"] = "pending_links"
    
    admin_text = (
        f"✅ Paiement validé pour la transaction {transaction_id}\n\n"
        f"👤 Client: {transaction['user_username']} (ID: {transaction['user_id']})\n"
        f"🎬 Contenu: {transaction['item_title']}\n"
        f"💰 Montant: {transaction['item_price']} FCFA\n\n"
        f"Utilisez la commande /send_links {transaction_id} <lien1> <lien2> ... pour envoyer les liens d'accès."
    )

    try:
        await query.edit_message_text(admin_text)
    except BadRequest:
        pass

async def reject_transaction(update: Update, context: CallbackContext):
    """Rejette une transaction et notifie le client."""
    query = update.callback_query
    await query.answer()

    transaction_id = query.data.split("_")[-1]
    transaction = catalog["transactions"].get(transaction_id)

    if not transaction:
        try:
            await query.edit_message_text("Transaction introuvable.")
        except BadRequest:
            pass
        return

    if transaction.get("status") != "pending":
        try:
            await query.edit_message_text(f"Cette transaction a déjà été {transaction.get('status')}.")
        except BadRequest:
            pass
        return

    user_id = transaction["user_id"]
    item_title = transaction.get("item_title", "Contenu inconnu")

    transaction["status"] = "rejected"
    logger.info(f"Transaction rejetée: {transaction_id} pour le client {user_id}")

    client_text = (
        f"❌ Votre transaction pour *{item_title}* a été rejetée par l'administrateur.\n"
        "Veuillez vérifier votre preuve de paiement (capture d'écran lisible, montant correct) ou contacter le support pour plus d'informations."
    )
    try:
        await context.bot.send_message(
            user_id,
            client_text,
            parse_mode="Markdown"
        )
        try:
            await query.edit_message_text(f"❌ Transaction rejetée pour {item_title} et client {user_id} notifié.")
        except BadRequest:
            pass
    except Exception as e:
        try:
            await query.edit_message_text(f"❌ Transaction rejetée mais impossible d'envoyer le message au client {user_id}. Erreur: {e}")
        except BadRequest:
            pass
        logger.error(f"Erreur en envoyant le message de rejet au client {user_id}: {e}")

# ==================== FONCTIONS CLIENTS ====================
async def start(update: Update, context: CallbackContext):
    """Commande /start avec menu interactif"""
    # Réinitialiser l'état de conversation et le panier
    context.user_data.clear()
    context.user_data['cart'] = []
    
    buttons = [
        [KeyboardButton("🎬 Films"), KeyboardButton("📺 Séries")],
        [KeyboardButton("🛒 Panier"), KeyboardButton("❓ Aide")]
    ]
    reply_markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)

    await update.message.reply_text(
        "🍿 Bienvenue dans votre cinéma virtuel !\n"
        "Utilisez les boutons ci-dessous pour naviguer :",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: CallbackContext):
    """Commande /aide"""
    await update.message.reply_text(
        "❓ Aide :\n"
        "- Cliquez sur 🎬 *Films* pour voir les films disponibles\n"
        "- Cliquez sur 📺 *Séries* pour les séries\n"
        "- Ajoutez des articles à votre 🛒 *Panier*\n"
        "- Validez votre panier pour procéder au paiement\n\n"
        "Besoin d'aide ? Contactez @votre_support"
    )

async def view_cart(update: Update, context: CallbackContext):
    """Affiche le contenu du panier avec options de suppression"""
    cart = context.user_data.get('cart', [])
    
    if not cart:
        message_text = "🛒 Votre panier est vide."
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(message_text)
            except BadRequest:
                await update.callback_query.message.reply_text(message_text)
        else:
            await update.message.reply_text(message_text)
        return
    
    total_price = sum(item['price'] for item in cart)
    cart_text = "🛒 *Votre Panier* :\n\n"
    
    buttons = []
    for i, item in enumerate(cart):
        cart_text += f"{i+1}. {item['title']} - {item['price']} FCFA\n"
        # Bouton pour supprimer chaque article individuellement
        buttons.append([
            InlineKeyboardButton(f"🗑️ Supprimer: {item['title'][:30]}...", callback_data=f"remove_from_cart_{i}")
        ])
    
    cart_text += f"\n💰 *Total* : {total_price} FCFA\n\n"
    cart_text += "Que souhaitez-vous faire ?"
    
    # Boutons d'action pour le panier
    action_buttons = [
        [InlineKeyboardButton("✅ Payer maintenant", callback_data="checkout_cart")],
        [InlineKeyboardButton("🗑️ Vider tout le panier", callback_data="clear_cart")],
        [InlineKeyboardButton("🔙 Continuer les achats", callback_data="continue_shopping")]
    ]
    
    # Ajouter les boutons d'action après les boutons de suppression
    buttons.extend(action_buttons)
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                cart_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except BadRequest:
            await update.callback_query.message.reply_text(
                cart_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
    else:
        await update.message.reply_text(
            cart_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

async def remove_from_cart(update: Update, context: CallbackContext):
    """Supprime un article spécifique du panier"""
    query = update.callback_query
    await query.answer()

    try:
        item_index = int(query.data.split("_")[-1])
        cart = context.user_data.get('cart', [])
        
        if 0 <= item_index < len(cart):
            removed_item = cart.pop(item_index)
            await query.answer(f"✅ {removed_item['title']} retiré du panier!", show_alert=True)
            # Recharger la vue du panier
            await view_cart(update, context)
        else:
            await query.answer("❌ Article introuvable dans le panier.", show_alert=True)
    except (ValueError, IndexError):
        await query.answer("❌ Erreur lors de la suppression.", show_alert=True)

async def add_to_cart(update: Update, context: CallbackContext):
    """Ajoute un article au panier"""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    item_type = parts[1]
    item_id = int(parts[2])
    season_number = None
    
    if item_type == "season" and len(parts) > 3:
        season_number = int(parts[3])

    item_title = "Contenu Inconnu"
    item_price = 0

    if item_type == "film":
        film = next((f for f in catalog["films"] if f["id"] == item_id), None)
        if film:
            item_title = film['title']
            item_price = film.get('price', 0)
        else:
            await query.answer("Film introuvable.", show_alert=True)
            return

    elif item_type == "series":
        series = next((s for s in catalog["series"] if s["id"] == item_id), None)
        if series:
            item_title = series['title'] + " (Série Complète)"
            for season in series.get('seasons', []):
                for episode in season.get('episodes', []):
                    item_price += episode.get('price', 0)
        else:
            await query.answer("Série introuvable.", show_alert=True)
            return

    elif item_type == "season" and season_number is not None:
        series = next((s for s in catalog["series"] if s["id"] == item_id), None)
        if series:
            season = next((s for s in series.get("seasons", []) if s["number"] == season_number), None)
            if season:
                item_title = f"{series['title']} - Saison {season_number}"
                for episode in season.get('episodes', []):
                    item_price += episode.get('price', 0)
            else:
                await query.answer("Saison introuvable.", show_alert=True)
                return
        else:
            await query.answer("Série introuvable.", show_alert=True)
            return

    # Ajouter au panier
    if 'cart' not in context.user_data:
        context.user_data['cart'] = []
    
    context.user_data['cart'].append({
        'type': item_type,
        'id': item_id,
        'season_number': season_number,
        'title': item_title,
        'price': item_price
    })
    
    # Message de confirmation avec boutons
    confirmation_text = f"✅ *{item_title}* ajouté au panier!\n💰 Prix: {item_price} FCFA"
    
    buttons = [
        [InlineKeyboardButton("👁️ Voir le panier", callback_data="view_cart")],
        [InlineKeyboardButton("🛒 Continuer les achats", callback_data="continue_shopping")]
    ]
    
    try:
        await query.edit_message_text(
            confirmation_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except BadRequest:
        await query.answer(f"✅ {item_title} ajouté au panier!", show_alert=True)

async def clear_cart(update: Update, context: CallbackContext):
    """Vide le panier"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['cart'] = []
    await query.edit_message_text("🗑️ Panier vidé. Vous pouvez maintenant ajouter de nouveaux articles.")

async def continue_shopping(update: Update, context: CallbackContext):
    """Retourne aux achats"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("🛏️ Vous pouvez continuer vos achats.")
    # Retour au menu principal
    await start_menu_inline(update, context)

async def checkout_cart(update: Update, context: CallbackContext):
    """Passe à la caisse avec le contenu du panier"""
    query = update.callback_query
    await query.answer()

    cart = context.user_data.get('cart', [])
    if not cart:
        await query.answer("Votre panier est vide.", show_alert=True)
        return

    total_price = sum(item['price'] for item in cart)
    cart_items = ", ".join([item['title'] for item in cart])
    
    transaction_id = str(uuid4())
    context.user_data["current_transaction"] = {
        "id": transaction_id,
        "user_id": query.from_user.id,
        "user_username": query.from_user.username or "N/A",
        'cart_items': cart,
        "item_title": f"Panier: {cart_items}",
        "item_price": total_price,
        "status": "pending"
    }
    catalog["transactions"][transaction_id] = context.user_data["current_transaction"]

    payment_text = (
        f"💳 *Récapitulatif de votre commande* :\n\n"
    )
    
    for item in cart:
        payment_text += f"• {item['title']} - {item['price']} FCFA\n"
    
    payment_text += (
        f"\n💰 *Total à payer* : {total_price} FCFA\n\n"
        f"📞 *MTN Mobile Money* : `{PAYMENT_METHODS['mtn']}`\n"
        f"📞 *Orange Money* : `{PAYMENT_METHODS['orange']}`\n\n"
        "➡️ Après paiement, veuillez envoyer la *capture d'écran de confirmation* de votre transaction par message direct à ce bot."
    )

    try:
        await query.edit_message_text(
            payment_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Annuler le paiement", callback_data="cancel_payment")]
            ])
        )
        
        # Vider le panier après passage en caisse
        context.user_data['cart'] = []
        
        return WAITING_PAYMENT_PROOF
    except BadRequest:
        return ConversationHandler.END

async def handle_payment_proof(update: Update, context: CallbackContext):
    """Reçoit la preuve de paiement et notifie l'admin"""
    user_message = update.message

    logger.info(f"handle_payment_proof appelé. Type de message: {user_message.effective_attachment}")

    if not user_message.photo:
        await user_message.reply_text("Veuillez envoyer une *capture d'écran valide* de votre paiement (une photo, pas un fichier ou du texte).", parse_mode="Markdown")
        return WAITING_PAYMENT_PROOF

    transaction = context.user_data.get("current_transaction")
    if not transaction:
        logger.warning(f"handle_payment_proof: Aucune transaction en cours pour l'utilisateur {user_message.from_user.id}.")
        await user_message.reply_text("Erreur : Aucune transaction en cours trouvée. Veuillez recommencer le processus d'achat.",
                                         reply_markup=ReplyKeyboardMarkup([
                                             [KeyboardButton("🎬 Films"), KeyboardButton("📺 Séries")],
                                             [KeyboardButton("🛒 Panier"), KeyboardButton("❓ Aide")]
                                         ], resize_keyboard=True))
        return ConversationHandler.END

    photo_file_id = user_message.photo[-1].file_id

    admin_text = (
        "⚠️ *Nouvelle Transaction reçue* ⚠️\n\n"
        f"🆔 *Transaction ID* : `{transaction['id']}`\n"
        f"👤 *Client* : [{user_message.from_user.full_name}](tg://user?id={user_message.from_user.id}) (@{transaction['user_username']})\n"
        f"🎬 *Contenu* : {transaction['item_title']}\n"
        f"💰 *Montant déclaré* : {transaction['item_price']} FCFA\n\n"
        "Preuve de paiement reçue."
    )

    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_file_id,
            caption=admin_text,
            parse_mode="Markdown"
        )
        await user_message.reply_text(
            "📨 Votre preuve de paiement a été envoyée à l'administrateur.\n"
            "Vous recevrez vos liens d'accès après vérification et validation. Merci de patienter."
        )
        
        # Réinitialiser l'état de conversation pour permettre de nouveaux achats
        if "current_transaction" in context.user_data:
            del context.user_data["current_transaction"]
            
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de la preuve de paiement à l'admin (UserID: {user_message.from_user.id}): {e}")
        await user_message.reply_text(
            "Une erreur est survenue lors de l'envoi de votre preuve. Veuillez réessayer ou contacter le support."
        )
        return ConversationHandler.END

async def cancel_transaction(update: Update, context: CallbackContext):
    """Annule la transaction en cours"""
    query = update.callback_query
    await query.answer()

    if "current_transaction" in context.user_data:
        transaction_id = context.user_data["current_transaction"]["id"]
        if transaction_id in catalog["transactions"]:
            catalog["transactions"][transaction_id]["status"] = "cancelled"
            logger.info(f"Transaction {transaction_id} annulée par l'utilisateur {query.from_user.id}.")
        del context.user_data["current_transaction"]

    try:
        await query.edit_message_text("❌ Transaction annulée. Vous pouvez maintenant effectuer un nouvel achat.")
    except BadRequest:
        pass
    return ConversationHandler.END

async def paginate_films(update: Update, context: CallbackContext):
    """Gère l'affichage des films avec pagination"""
    message_to_edit = None
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message_to_edit = query.message
    elif update.message:
        message_to_edit = update.message
    else:
        logger.error("paginate_films appelée sans message ni callback_query")
        return

    current_page = context.user_data.get('films_current_page', 0)
    all_films = catalog["films"]
    items_per_page = 10
    
    start_index = current_page * items_per_page
    end_index = min(start_index + items_per_page, len(all_films))

    if not all_films:
        text = "Aucun film disponible pour le moment."
        if update.callback_query:
            try:
                await message_to_edit.edit_text(text)
            except BadRequest:
                pass
        else:
            await message_to_edit.reply_text(text)
        return

    films_on_page = all_films[start_index:end_index]
    buttons = []
    
    for film in films_on_page:
        buttons.append([
            InlineKeyboardButton(
                f"{film['title']} ({film['year']})",
                callback_data=f"detail_film_{film['id']}"
            )
        ])

    total_pages = (len(all_films) + items_per_page - 1) // items_per_page

    if total_pages > 1:
        pagination_buttons = []
        if current_page > 0:
            pagination_buttons.append(InlineKeyboardButton("◀️ Précédent", callback_data="page_prev_films"))
        
        if current_page < total_pages - 1:
            pagination_buttons.append(InlineKeyboardButton("Suivant ▶️", callback_data="page_next_films"))
        
        if pagination_buttons:
            buttons.append(pagination_buttons)

    # Ajouter le bouton de recherche en bas
    buttons.append([InlineKeyboardButton("🔍 Rechercher des films", callback_data="search_films")])
    buttons.append([InlineKeyboardButton("🔙 Retour au menu principal", callback_data="start_menu_inline")])

    text = f"🎬 Films Disponibles (Page {current_page + 1}/{total_pages if total_pages > 0 else 1}) :"
    
    if update.callback_query:
        try:
            await message_to_edit.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown"
            )
        except BadRequest:
            pass
    else:
        await message_to_edit.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )

async def list_films(update: Update, context: CallbackContext):
    """Initialise la pagination et affiche la première page des films"""
    context.user_data['films_current_page'] = 0
    await paginate_films(update, context)

async def page_next_films(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    context.user_data['films_current_page'] = context.user_data.get('films_current_page', 0) + 1
    await paginate_films(update, context)

async def page_prev_films(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    context.user_data['films_current_page'] = max(0, context.user_data.get('films_current_page', 0) - 1)
    await paginate_films(update, context)

async def paginate_series(update: Update, context: CallbackContext):
    """Gère l'affichage des séries avec pagination"""
    message_to_edit = None
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message_to_edit = query.message
    elif update.message:
        message_to_edit = update.message
    else:
        logger.error("paginate_series appelée sans message ni callback_query")
        return

    current_page = context.user_data.get('series_current_page', 0)
    all_series = catalog["series"]
    items_per_page = 10
    
    start_index = current_page * items_per_page
    end_index = min(start_index + items_per_page, len(all_series))

    if not all_series:
        text = "Aucune série disponible pour le moment."
        if update.callback_query:
            try:
                await message_to_edit.edit_text(text)
            except BadRequest:
                pass
        else:
            await message_to_edit.reply_text(text)
        return

    series_on_page = all_series[start_index:end_index]
    buttons = []
    
    for serie in series_on_page:
        buttons.append([
            InlineKeyboardButton(
                f"{serie['title']} ({len(serie.get('seasons', []))} saisons)",
                callback_data=f"list_seasons_{serie['id']}"
            )
        ])

    total_pages = (len(all_series) + items_per_page - 1) // items_per_page

    if total_pages > 1:
        pagination_buttons = []
        if current_page > 0:
            pagination_buttons.append(InlineKeyboardButton("◀️ Précédent", callback_data="page_prev_series"))
        
        if current_page < total_pages - 1:
            pagination_buttons.append(InlineKeyboardButton("Suivant ▶️", callback_data="page_next_series"))
        
        if pagination_buttons:
            buttons.append(pagination_buttons)

    # Ajouter le bouton de recherche en bas
    buttons.append([InlineKeyboardButton("🔍 Rechercher des séries", callback_data="search_series")])
    buttons.append([InlineKeyboardButton("🔙 Retour au menu principal", callback_data="start_menu_inline")])

    text = f"📺 Séries Disponibles (Page {current_page + 1}/{total_pages if total_pages > 0 else 1}) :"
    
    if update.callback_query:
        try:
            await message_to_edit.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except BadRequest:
            pass
    else:
        await message_to_edit.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

async def list_series(update: Update, context: CallbackContext):
    """Initialise la pagination et affiche la première page des séries"""
    context.user_data['series_current_page'] = 0
    await paginate_series(update, context)

async def page_next_series(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    context.user_data['series_current_page'] = context.user_data.get('series_current_page', 0) + 1
    await paginate_series(update, context)

async def page_prev_series(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    context.user_data['series_current_page'] = max(0, context.user_data.get('series_current_page', 0) - 1)
    await paginate_series(update, context)

async def start_menu_inline(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    buttons = [
        [KeyboardButton("🎬 Films"), KeyboardButton("📺 Séries")],
        [KeyboardButton("🛒 Panier"), KeyboardButton("❓ Aide")]
    ]
    reply_markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)
    
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="🍿 Bienvenue dans votre cinéma virtuel !\nUtilisez les boutons ci-dessous pour naviguer :",
        reply_markup=reply_markup
    )
    
    try:
        await query.delete_message()
    except Exception as e:
        logger.warning(f"Impossible de supprimer le message après retour au menu principal: {e}")

async def show_film_detail(update: Update, context: CallbackContext):
    """Affiche les détails d'un film avec option d'ajout au panier"""
    query = update.callback_query
    await query.answer()

    film_id = int(query.data.split("_")[-1])
    film = next((f for f in catalog["films"] if f["id"] == film_id), None)

    if not film:
        try:
            await query.edit_message_text("Film introuvable.")
        except BadRequest:
            pass
        return

    text_caption = (
        f"🎬 *{film['title']} ({film['year']})*\n"
        f"📖 *Synopsis* : {film['description']}\n"
        f"⭐ *Genre* : {film.get('genre', 'Non spécifié')}\n"
        f"⏱️ *Durée* : {film.get('duration', 'Non spécifié')}\n"
        f"💰 *Prix* : {film['price']} FCFA\n"
    )

    buttons = [
        [InlineKeyboardButton("➕ Ajouter au panier", callback_data=f"add_film_{film_id}")],
        [InlineKeyboardButton("👁️ Voir le panier", callback_data="view_cart")],
        [InlineKeyboardButton("🔙 Retour à la liste", callback_data="back_to_films")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)

    if film.get('image_url'):
        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=film['image_url'],
                caption=text_caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            await query.delete_message()
            return
        except Exception as e:
            logger.warning(f"Impossible d'envoyer la photo pour le film {film['title']}: {e}. Envoi du texte uniquement.")

    try:
        await query.edit_message_text(
            text_caption,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    except BadRequest:
        pass

async def show_series_detail(update: Update, context: CallbackContext):
    """Affiche les détails d'une série avec options d'ajout au panier"""
    query = update.callback_query
    await query.answer()

    series_id = int(query.data.split("_")[-1])
    series = next((s for s in catalog["series"] if s["id"] == series_id), None)

    if not series:
        try:
            await query.edit_message_text("Série introuvable.")
        except BadRequest:
            pass
        return

    text = f"📺 *{series['title']}*\n\n{series.get('description', 'Pas de description disponible.')}\n\n"
    buttons = []

    total_series_price = 0
    for season in series.get('seasons', []):
        for episode in season.get('episodes', []):
            total_series_price += episode.get('price', 0)

    if total_series_price > 0:
        text += f"💰 *Prix de la série complète* : {total_series_price} FCFA\n\n"
        buttons.append([InlineKeyboardButton("🛒 Ajouter la série complète", callback_data=f"add_series_{series_id}")])

    if series.get('seasons'):
        text += "*Saisons disponibles :*\n"
        for season in series["seasons"]:
            season_price_display = sum(ep.get('price', 0) for ep in season.get('episodes', []))
            season_button_text = f"Saison {season['number']}"
            if season_price_display > 0:
                season_button_text += f" ({season_price_display} FCFA)"
            
            buttons.append([
                InlineKeyboardButton(
                    season_button_text,
                    callback_data=f"season_{series_id}_{season['number']}"
                )
            ])

    buttons.append([InlineKeyboardButton("🔙 Retour à la liste", callback_data="back_to_series")])

    if series.get('cover_url'):
        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=series['cover_url'],
                caption=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            await query.delete_message()
            return
        except Exception as e:
            logger.warning(f"Impossible d'envoyer la photo pour la série {series['title']}: {e}. Envoi du texte uniquement.")

    try:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except BadRequest:
        pass

async def show_season_detail(update: Update, context: CallbackContext):
    """Affiche les détails d'une saison avec option d'ajout au panier"""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    series_id = int(parts[1])
    season_number = int(parts[2])

    series = next((s for s in catalog["series"] if s["id"] == series_id), None)
    if not series:
        try:
            await query.edit_message_text("Série introuvable.")
        except BadRequest:
            pass
        return

    season = next((s for s in series.get("seasons", []) if s["number"] == season_number), None)
    if not season:
        try:
            await query.edit_message_text("Saison introuvable.")
        except BadRequest:
            pass
        return

    text = f"📺 *{series['title']} - Saison {season_number}*\n\n"
    
    season_price_for_purchase = sum(ep.get('price', 0) for ep in season.get('episodes', []))
    logger.info(f"Prix calculé pour Saison {season_number} de {series['title']}: {season_price_for_purchase}")

    if "episodes" in season and season["episodes"]:
        text += "*Épisodes :*\n"
        for ep in season["episodes"]:
            text += f"- {ep['title']} ({ep.get('duration', '??')} min) - {ep.get('price', '0')} FCFA\n"
    else:
        text += "Pas d'épisodes disponibles pour cette saison."

    if season_price_for_purchase > 0:
        text += f"\n💰 *Prix de la saison complète* : {season_price_for_purchase} FCFA"

    buttons = []
    if season_price_for_purchase > 0:
        buttons.append([InlineKeyboardButton("➕ Ajouter cette saison", callback_data=f"add_season_{series_id}_{season_number}")])

    buttons.append([InlineKeyboardButton("👁️ Voir le panier", callback_data="view_cart")])
    buttons.append([InlineKeyboardButton("🔙 Retour aux saisons", callback_data=f"list_seasons_{series_id}")])

    try:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except BadRequest:
        pass

async def handle_text_messages(update: Update, context: CallbackContext):
    """Gère les messages texte génériques"""
    text = update.message.text.lower()

    if any(greeting in text for greeting in ["salut", "bonjour", "hello", "hey"]):
        await update.message.reply_text("👋 Bonjour ! Comment puis-je vous aider ? Utilisez les boutons ci-dessous ou /aide.",
                                         reply_markup=ReplyKeyboardMarkup([
                                             [KeyboardButton("🎬 Films"), KeyboardButton("📺 Séries")],
                                             [KeyboardButton("🛒 Panier"), KeyboardButton("❓ Aide")]
                                         ], resize_keyboard=True))
    elif "film" in text:
        await list_films(update, context)
    elif "série" in text or "serie" in text:
        await list_series(update, context)
    elif "panier" in text:
        await view_cart(update, context)
    else:
        await update.message.reply_text("Je n'ai pas compris votre demande. Utilisez les boutons ou tapez /aide pour des informations.",
                                         reply_markup=ReplyKeyboardMarkup([
                                             [KeyboardButton("🎬 Films"), KeyboardButton("📺 Séries")],
                                             [KeyboardButton("🛒 Panier"), KeyboardButton("❓ Aide")]
                                         ], resize_keyboard=True))

async def back_to_films(update: Update, context: CallbackContext):
    """Retour à la liste des films"""
    query = update.callback_query
    await query.answer()
    context.user_data['films_current_page'] = 0
    await paginate_films(update, context)

async def back_to_series(update: Update, context: CallbackContext):
    """Retour à la liste des séries"""
    query = update.callback_query
    await query.answer()
    context.user_data['series_current_page'] = 0
    await paginate_series(update, context)

# ==================== FONCTIONS DE RECHERCHE ====================
async def start_search(update: Update, context: CallbackContext):
    """Démarre le processus de recherche"""
    query = update.callback_query
    await query.answer()
    
    search_type = query.data.split("_")[1]  # "films" ou "series"
    context.user_data['search_type'] = search_type
    context.user_data['search_mode'] = True
    
    await query.edit_message_text(
        f"🔍 Recherche de {search_type}\n\nVeuillez entrer votre terme de recherche:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Annuler", callback_data=f"cancel_search_{search_type}")]
        ])
    )

async def handle_search_query(update: Update, context: CallbackContext):
    """Traite la requête de recherche"""
    if not context.user_data.get('search_mode'):
        # Si on n'est pas en mode recherche, laisser les autres handlers gérer
        return
    
    search_query = update.message.text.lower()
    search_type = context.user_data.get('search_type')
    
    if search_type == "films":
        results = [f for f in catalog["films"] if search_query in f['title'].lower() or 
                  search_query in f.get('description', '').lower() or 
                  search_query in f.get('genre', '').lower()]
    else:  # series
        results = [s for s in catalog["series"] if search_query in s['title'].lower() or 
                  search_query in s.get('description', '').lower() or 
                  search_query in s.get('genre', '').lower()]
    
    context.user_data['search_mode'] = False
    context.user_data['search_results'] = results
    context.user_data['search_current_page'] = 0
    
    if not results:
        await update.message.reply_text(
            f"Aucun {search_type[:-1]} trouvé pour '{search_query}'.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Retour", callback_data=f"back_to_{search_type}")]
            ])
        )
        return
    
    # Afficher les résultats de recherche
    await show_search_results(update, context, search_type)

async def show_search_results(update: Update, context: CallbackContext, search_type: str):
    """Affiche les résultats de recherche avec pagination"""
    results = context.user_data.get('search_results', [])
    current_page = context.user_data.get('search_current_page', 0)
    items_per_page = 10
    
    start_index = current_page * items_per_page
    end_index = min(start_index + items_per_page, len(results))
    results_on_page = results[start_index:end_index]
    
    text = f"🔍 Résultats de recherche ({len(results)} {search_type} trouvés):\n\n"
    
    buttons = []
    for item in results_on_page:
        if search_type == "films":
            buttons.append([InlineKeyboardButton(
                f"{item['title']} ({item['year']})", 
                callback_data=f"detail_film_{item['id']}"
            )])
        else:  # series
            buttons.append([InlineKeyboardButton(
                f"{item['title']} ({len(item.get('seasons', []))} saisons)", 
                callback_data=f"list_seasons_{item['id']}"
            )])
    
    # Boutons de pagination pour les résultats de recherche
    total_pages = (len(results) + items_per_page - 1) // items_per_page
    if total_pages > 1:
        pagination_buttons = []
        if current_page > 0:
            pagination_buttons.append(InlineKeyboardButton("◀️ Précédent", callback_data="search_prev_page"))
        if current_page < total_pages - 1:
            pagination_buttons.append(InlineKeyboardButton("Suivant ▶️", callback_data="search_next_page"))
        if pagination_buttons:
            buttons.append(pagination_buttons)
    
    buttons.append([InlineKeyboardButton("🔙 Retour à la liste complète", callback_data=f"back_to_{search_type}")])
    
    if isinstance(update, Update) and update.message:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except BadRequest:
            pass

async def search_next_page(update: Update, context: CallbackContext):
    """Page suivante des résultats de recherche"""
    query = update.callback_query
    await query.answer()
    
    current_page = context.user_data.get('search_current_page', 0)
    context.user_data['search_current_page'] = current_page + 1
    
    search_type = context.user_data.get('search_type', 'films')
    await show_search_results(update, context, search_type)

async def search_prev_page(update: Update, context: CallbackContext):
    """Page précédente des résultats de recherche"""
    query = update.callback_query
    await query.answer()
    
    current_page = context.user_data.get('search_current_page', 0)
    context.user_data['search_current_page'] = max(0, current_page - 1)
    
    search_type = context.user_data.get('search_type', 'films')
    await show_search_results(update, context, search_type)

async def cancel_search(update: Update, context: CallbackContext):
    """Annule la recherche et retourne à la liste normale"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['search_mode'] = False
    search_type = query.data.split("_")[-1]
    
    if search_type == "films":
        await paginate_films(update, context)
    else:  # series
        await paginate_series(update, context)

async def back_to_list_from_search(update: Update, context: CallbackContext):
    """Retourne à la liste normale depuis les résultats de recherche"""
    query = update.callback_query
    await query.answer()
    
    list_type = query.data.split("_")[-1]  # "films" ou "series"
    
    if list_type == "films":
        context.user_data['films_current_page'] = 0
        await paginate_films(update, context)
    else:  # series
        context.user_data['series_current_page'] = 0
        await paginate_series(update, context)

def main():
    """Configure et lance le bot"""
    load_catalog()

    application = Application.builder().token(TOKEN).build()

    # ConversationHandler pour le processus de paiement
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(checkout_cart, pattern="^checkout_cart$")
        ],
        states={
            WAITING_PAYMENT_PROOF: [
                MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_payment_proof),
                CallbackQueryHandler(cancel_transaction, pattern="^cancel_payment$")
            ]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_transaction),
            CommandHandler('start', start),
            CommandHandler('aide', help_command),
            MessageHandler(filters.Regex(r"^🎬 Films$"), list_films),
            MessageHandler(filters.Regex(r"^📺 Séries$"), list_series),
            MessageHandler(filters.Regex(r"^🛒 Panier$"), view_cart),
            MessageHandler(filters.Regex(r"^❓ Aide$"), help_command)
        ],
        allow_reentry=True
    )
    application.add_handler(conv_handler)

    # Commandes de base
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("aide", help_command))
    application.add_handler(CommandHandler("films", list_films))
    application.add_handler(CommandHandler("series", list_series))
    application.add_handler(CommandHandler("send_links", send_access_links))

    # Handlers pour les boutons du ReplyKeyboardMarkup
    application.add_handler(MessageHandler(filters.Regex(r"^🎬 Films$"), list_films))
    application.add_handler(MessageHandler(filters.Regex(r"^📺 Séries$"), list_series))
    application.add_handler(MessageHandler(filters.Regex(r"^🛒 Panier$"), view_cart))
    application.add_handler(MessageHandler(filters.Regex(r"^❓ Aide$"), help_command))

    # Handlers de documents (admin)
    application.add_handler(MessageHandler(filters.Document.ALL & filters.User(ADMIN_ID), handle_admin_document))

    # Handlers pour le panier - AJOUT DE LA SUPPRESSION D'ARTICLES
    application.add_handler(CallbackQueryHandler(add_to_cart, pattern=r"^(add_film_|add_series_|add_season_)\d+(_\d+)?$"))
    application.add_handler(CallbackQueryHandler(remove_from_cart, pattern=r"^remove_from_cart_\d+$"))
    application.add_handler(CallbackQueryHandler(view_cart, pattern="^view_cart$"))
    application.add_handler(CallbackQueryHandler(clear_cart, pattern="^clear_cart$"))
    application.add_handler(CallbackQueryHandler(continue_shopping, pattern="^continue_shopping$"))

    # Autres Handlers de CallbackQuery
    application.add_handler(CallbackQueryHandler(show_film_detail, pattern=r"^detail_film_"))
    application.add_handler(CallbackQueryHandler(show_series_detail, pattern=r"^list_seasons_"))
    application.add_handler(CallbackQueryHandler(show_season_detail, pattern=r"^season_"))

    # Handlers de pagination des films
    application.add_handler(CallbackQueryHandler(page_next_films, pattern=r"^page_next_films$"))
    application.add_handler(CallbackQueryHandler(page_prev_films, pattern=r"^page_prev_films$"))

    # Handlers de pagination des séries
    application.add_handler(CallbackQueryHandler(page_next_series, pattern=r"^page_next_series$"))
    application.add_handler(CallbackQueryHandler(page_prev_series, pattern=r"^page_prev_series$"))

    # Handler pour revenir au menu de démarrage
    application.add_handler(CallbackQueryHandler(start_menu_inline, pattern=r"^start_menu_inline$"))

    # Handlers de retour
    application.add_handler(CallbackQueryHandler(back_to_films, pattern=r"^back_to_films$"))
    application.add_handler(CallbackQueryHandler(back_to_series, pattern=r"^back_to_series$"))

    # Handlers pour la recherche
    application.add_handler(CallbackQueryHandler(start_search, pattern=r"^search_films$"))
    application.add_handler(CallbackQueryHandler(start_search, pattern=r"^search_series$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_query))

    # Handlers pour la pagination des résultats de recherche
    application.add_handler(CallbackQueryHandler(search_next_page, pattern=r"^search_next_page$"))
    application.add_handler(CallbackQueryHandler(search_prev_page, pattern=r"^search_prev_page$"))
    application.add_handler(CallbackQueryHandler(cancel_search, pattern=r"^cancel_search_"))
    application.add_handler(CallbackQueryHandler(back_to_list_from_search, pattern=r"^back_to_films$|^back_to_series$"))

    # Handler pour les messages texte génériques
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    logger.info("Bot démarré et écoute les mises à jour...")
    application.run_polling()

if __name__ == "__main__":
    main()