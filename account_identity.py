"""Account identity from deployment configuration and authenticated Telegram data."""
CONFIRMED_ACCOUNTS = {}


def configure_admin(username,display_name):
    CONFIRMED_ACCOUNTS.clear()
    CONFIRMED_ACCOUNTS[username.casefold()]={'name':display_name,'grammatical_gender':'male','creator':True,'source':'owner_configured'}


def seed_identities(memory):
    memory['confirmed_accounts']={handle:dict(value) for handle,value in CONFIRMED_ACCOUNTS.items()}


def expected_identity(message):
    handle=(message.from_user.username or '').casefold()
    value=CONFIRMED_ACCOUNTS.get(handle)
    if value:
        identity=dict(value,account=handle)
        identity['name']=identity['name'] or getattr(message.from_user,'first_name','користувач')
        return identity
    if not handle:return None
    return {'name':getattr(message.from_user,'first_name','користувач'),'account':handle,'creator':False,'source':'authenticated_telegram'}


def pin_identity(memory,message):
    expected=expected_identity(message)
    if expected:memory.setdefault('account_identities',{})[str(message.from_user.id)]=expected


def identity_instruction(memory,message):
    stored=memory.get('account_identities',{}).get(str(message.from_user.id))
    current=expected_identity(message)
    identity=current or stored
    if not identity:return 'Не визначай стать співрозмовника за текстом книги чи власними попередніми відповідями.'
    text=f"Співрозмовник — {identity['name']}. "
    if identity.get('grammatical_gender')=='male':
        text+='Він чоловік: звертайся до нього лише в чоловічому роді: «ти надіслав», «ти сказав», «ти готовий». '
    if identity.get('creator'):text+='Цей користувач — творець Міссі Блек/Катрусі. '
    text+='Це підтверджені дані акаунта; старі помилкові звертання не є джерелом його особистості. '
    return text
