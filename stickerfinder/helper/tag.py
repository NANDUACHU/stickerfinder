"""Helper functions for tagging."""
from sqlalchemy import func
from collections import OrderedDict

from stickerfinder.sentry import sentry
from stickerfinder.helper.telegram import call_tg_func
from stickerfinder.helper.keyboard import (
    main_keyboard,
    get_tagging_keyboard,
    get_fix_sticker_tags_keyboard,
)
from stickerfinder.helper import (
    tag_text,
    blacklist,
    reward_messages,
)
from stickerfinder.models import (
    Change,
    Tag,
    Sticker,
    StickerSet,
)


def current_sticker_tags_message(sticker, send_set_info=False):
    """Create a message displaying the current text and tags."""
    if len(sticker.tags) == 0 and sticker.text is None:
        return None
    elif len(sticker.tags) > 0 and sticker.text is None:
        message = f'Current tags are: \n {sticker.tags_as_text()}'
    elif len(sticker.tags) == 0 and sticker.text is not None:
        message = f'Current text is: \n {sticker.text}'
    else:
        message = f'Current tags and text are : \n {sticker.tags_as_text()} \n {sticker.text}'

    if send_set_info:
        set_info = f'From sticker set: {sticker.sticker_set.title} ({sticker.sticker_set.name}) \n'
        return set_info + message

    return message


def send_tag_messages(chat, tg_chat, send_set_info=False):
    """Send next sticker and the tags of this sticker."""
    # If we don't have a message, we need to add the inline keyboard to the sticker
    # Otherwise attach it to the following message.
    message = current_sticker_tags_message(chat.current_sticker, send_set_info=send_set_info)
    keyboard = get_tagging_keyboard()

    if not message:
        response = call_tg_func(tg_chat, 'send_sticker',
                                args=[chat.current_sticker.file_id],
                                kwargs={'reply_markup': keyboard})

        chat.last_sticker_message_id = response.message_id

    else:
        call_tg_func(tg_chat, 'send_sticker', args=[chat.current_sticker.file_id])

    if message:
        response = call_tg_func(tg_chat, 'send_message', [message], {'reply_markup': keyboard})
        chat.last_sticker_message_id = response.message_id


def handle_next(session, chat, tg_chat):
    """Handle the /next call or the 'next' button click."""
    # We are tagging a whole sticker set. Skip the current sticker
    if chat.full_sticker_set:
        # Check there is a next sticker
        stickers = chat.current_sticker_set.stickers
        for index, sticker in enumerate(stickers):
            if sticker == chat.current_sticker and index+1 < len(stickers):
                # We found the next sticker. Send the messages and return
                chat.current_sticker = stickers[index+1]
                send_tag_messages(chat, tg_chat)

                return

        # There are no stickers left, reset the chat and send success message.
        chat.current_sticker_set.completely_tagged = True
        chat.cancel()
        call_tg_func(tg_chat, 'send_message', ['The full sticker set is now tagged.'],
                     {'reply_markup': main_keyboard})

    # Find a random sticker with no changes
    elif chat.tagging_random_sticker:
        sticker = session.query(Sticker) \
            .outerjoin(Sticker.changes) \
            .join(Sticker.sticker_set) \
            .filter(Change.id.is_(None)) \
            .filter(StickerSet.banned.is_(False)) \
            .filter(StickerSet.nsfw.is_(False)) \
            .order_by(func.random()) \
            .limit(1) \
            .one_or_none()

        # No stickers for tagging left :)
        if not sticker:
            call_tg_func(tg_chat, 'send_message',
                         ['It looks like all stickers are already tagged :).'],
                         {'reply_markup': main_keyboard})
            chat.cancel()

        # Found a sticker. Send the messages
        chat.current_sticker = sticker
        send_tag_messages(chat, tg_chat, send_set_info=True)


def initialize_set_tagging(bot, tg_chat, session, name, chat, user):
    """Initialize the set tag functionality of a chat."""
    sticker_set = StickerSet.get_or_create(session, name, chat, user)
    if sticker_set.complete is False:
        return "Sticker set {name} is currently being added."

    # Chat now expects an incoming tag for the next sticker
    chat.expecting_sticker_set = False
    chat.full_sticker_set = True
    chat.current_sticker_set = sticker_set
    chat.current_sticker = sticker_set.stickers[0]

    call_tg_func(tg_chat, 'send_message', [tag_text])
    send_tag_messages(chat, tg_chat)


def get_tags_from_text(text, limit=15):
    """Extract and clean tags from incoming string."""
    text = text.lower().strip()

    # Clean the text
    for ignored in ['\n', ',', '.', ';', ':', '!', '?']:
        text = text.replace(ignored, ' ')

    tags = [tag.strip() for tag in text.split(' ') if tag.strip() != '']

    # Deduplicate tags
    tags = list(OrderedDict.fromkeys(tags))

    return tags[:limit]


def tag_sticker(session, text, sticker, user,
                tg_chat, chat=None, keep_old=False):
    """Tag a single sticker."""
    text = text.lower()
    # Remove the /tag command
    if text.startswith('/tag'):
        text = text.split(' ')[1:]
        call_tg_func(tg_chat, 'send_message', ["You don't need to add the /tag command ;)"])

    incoming_tags = get_tags_from_text(text)

    # Only use the first few tags. This should prevent abuse from tag spammers.
    incoming_tags = incoming_tags[:15]
    # Clean the tags from unwanted words
    incoming_tags = [tag for tag in incoming_tags if tag not in blacklist]

    if len(user.changes) in reward_messages:
        reward = reward_messages[len(user.changes)]
        call_tg_func(tg_chat, 'send_message', [reward])

        sentry.captureMessage(
            f'User hit {len(user.changes)} changes!', level='info',
            extra={
                'user': user,
                'changes': len(user.changes),
            },
        )

    if len(incoming_tags) > 0:
        # Create tags
        tags = []
        for incoming_tag in incoming_tags:
            tag = Tag.get_or_create(session, incoming_tag)
            if tag not in tags:
                tags.append(tag)
            session.add(tag)

        # Keep old sticker tags if they are emojis
        for tag in sticker.tags:
            if tag.name in sticker.original_emojis:
                tags.append(tag)

        # Get the old tags for tracking
        old_tags_as_text = sticker.tags_as_text()

        if keep_old:
            for tag in tags:
                if tag not in sticker.tags:
                    sticker.tags.append(tag)
        else:
            # Remove replace old tags
            sticker.tags = tags

        # Create a change for logging
        if old_tags_as_text != sticker.tags_as_text():
            change = Change(user, sticker, old_tags_as_text)
            session.add(change)

    session.commit()
    if chat and chat.last_sticker_message_id:
        # Change the inline keyboard to allow fast fixing of the sticker's tags
        keyboard = get_fix_sticker_tags_keyboard(chat.current_sticker.file_id)
        call_tg_func(tg_chat.bot, 'edit_message_reply_markup',
                     [tg_chat.id, chat.last_sticker_message_id],
                     {'reply_markup': keyboard})

        # Reset last sticker message id
        chat.last_sticker_message_id = None
