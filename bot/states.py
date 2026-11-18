from aiogram.fsm.state import State, StatesGroup


class Search(StatesGroup):
    waiting_query = State()


class Support(StatesGroup):
    waiting_message = State()
    admin_reply = State()


class Checkout(StatesGroup):
    waiting_promo = State()
    waiting_email = State()


class CartInput(StatesGroup):
    waiting_value = State()
