"""Explicit grants for group/channel messages. Private assistant replies stay private."""
from contextlib import contextmanager
from contextvars import ContextVar
from aiogram.client.session.middlewares.base import BaseRequestMiddleware

_GRANTED_CHAT=ContextVar('explicit_group_send_grant',default=None)


class OutboundDenied(RuntimeError):pass


@contextmanager
def allow_group_send(chat_id):
    token=_GRANTED_CHAT.set(str(chat_id))
    try:yield
    finally:_GRANTED_CHAT.reset(token)


class OutboundAuthorization(BaseRequestMiddleware):
    async def __call__(self,make_request,bot,method):
        name=getattr(method,'__api_method__','')
        # Sending, forwarding, copying and message edits all need an explicit group grant.
        if name.startswith(('send','forward','copy','editMessage')) or name in ('stopPoll','pinChatMessage','unpinChatMessage','deleteMessage'):
            chat=getattr(method,'chat_id',None)
            if chat is None and getattr(method,'inline_message_id',None):raise OutboundDenied('INLINE_SEND_NOT_AUTHORIZED')
            if chat is not None:
                external=not (str(chat).isdigit() and int(chat)>0)
                if external and _GRANTED_CHAT.get()!=str(chat):raise OutboundDenied('GROUP_SEND_NOT_AUTHORIZED')
        return await make_request(bot,method)
