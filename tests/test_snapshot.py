from decimal import Decimal

from bot.handlers.store import validate_field
from services.snapshot_service import Pricing, image_url, platform_label, product_name, region_label, region_order


def test_pricing_rounds_up_to_whole_roubles():
    assert Pricing(Decimal("95"), Decimal("15")).rub_minor("1.50") == 16400
    assert Pricing(Decimal("100"), Decimal("0")).rub_minor("1.00") == 10000


def test_region_labels():
    assert region_label("us") == "🇺🇸 US"
    assert region_label(None, "Auto") == "🌍 Global · авто"
    assert region_label("CIS") == "🌐 СНГ"


def test_recipient_field_validation():
    assert validate_field("telegram_username", "durov_bot") == ("@durov_bot", "")
    assert validate_field("telegram_username", "@ab")[0] is None
    assert validate_field("player_id", "5123456789") == ("5123456789", "")
    assert validate_field("player_id", "a\nb")[0] is None


def test_russian_plurals():
    from bot.handlers.store import offers_count
    assert [offers_count(n) for n in (1, 2, 5, 11, 21, 104)] == ["1 предложение", "2 предложения", "5 предложений", "11 предложений", "21 предложение", "104 предложения"]


def test_real_supplier_shapes():
    """Shapes taken from a --probe run against the live API (2026-10-14)."""
    assert image_url("/api/media/x.webp") == "https://resell.codes/api/media/x.webp"
    assert image_url("https://cdn/x.png") == "https://cdn/x.png"
    assert region_label("UK") == "🇬🇧 GB"
    assert region_label("GLOBAL", "Fast") == "🌍 Global · быстро"
    assert region_order("US") < region_order("AE") < region_order("BH")
    assert product_name("PlayStation", "70 USD") == "PlayStation 70 USD"
    assert product_name("PUBG Mobile", "PUBG Mobile 60 UC") == "PUBG Mobile 60 UC"


def test_key_platforms_skip_steam():
    assert platform_label("Steam") is None
    assert platform_label("") is None
    assert platform_label("Xbox One / Series X|S") == "🟩 Xbox"
    assert platform_label("Battle.net") == "🔷 Battle.net"
    assert platform_label("Origin") == "🟧 EA app"
    assert platform_label("Some Launcher") == "Some Launcher"
