from bot.handlers.store import DOCUMENT_BUILDERS, documents_keyboard


def callback_data(markup) -> set[str]:
    return {button.callback_data for row in markup.inline_keyboard for button in row}


def test_all_document_buttons_have_a_document() -> None:
    document_codes = {data.removeprefix("doc:") for data in callback_data(documents_keyboard()) if data.startswith("doc:")}
    # Checkout points to the payment conditions rather than making users hunt for them.
    document_codes.add("payments")
    assert document_codes <= DOCUMENT_BUILDERS.keys()


def test_checkout_payment_conditions_are_the_public_offer() -> None:
    assert DOCUMENT_BUILDERS["payments"] is DOCUMENT_BUILDERS["offer"]
