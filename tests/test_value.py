from database.models import Product
from services.value_service import best_value_ids, parse_pack, value_line


def product(pid, offer, price_minor):
    return Product(id=pid, name=f"Game {offer}", price_minor=price_minor, raw_payload={"offer": {"name": offer}})


def test_parse_pack_names():
    assert parse_pack(product(1, "60 UC", 100)).amount == 60
    pack = parse_pack(product(1, "78 + 8 Diamonds", 100))
    assert (pack.amount, pack.unit, pack.icon) == (86, "Diamonds", "💎")
    assert parse_pack(product(1, "1,800 UC", 100)).amount == 1800
    assert parse_pack(product(1, "60 WOW Coins", 100)).unit == "WOW Coins"
    assert parse_pack(product(1, "Prime (1 Month)", 100)) is None
    assert parse_pack(product(1, "Elite Pass LV1-50", 100)) is None
    assert parse_pack(product(1, "Weekly Deal Pack 1", 100)) is None


def test_best_value_per_currency():
    packs = [product(1, "60 UC", 9900), product(2, "325 UC", 48900), product(3, "60 WOW Coins", 9900), product(4, "Prime (1 Month)", 9900)]
    # 325 UC: 150.5 kop/UC beats 165 kop/UC; WOW Coins has nothing to compare with.
    assert best_value_ids(packs) == {2}
    assert "🔥 Самый выгодный пакет: дешевле за единицу на 9%" in value_line(packs[1], packs)
    assert value_line(packs[0], packs) == "📊 1.65 ₽ за 1 UC"
    assert value_line(packs[3], packs) is None
