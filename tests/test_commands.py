from bot.main import BOT_COMMANDS


def test_customer_command_menu_contains_help_support_and_documents() -> None:
    commands = {item.command for item in BOT_COMMANDS}
    assert {"start", "help", "support", "paysupport", "documents"} <= commands
    assert {"privacy", "agreement", "offer", "returns", "contacts", "prices"} <= commands
