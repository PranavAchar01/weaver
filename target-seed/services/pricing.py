RATES = {"posters": 4.0, "flyers": 0.4, "banners": 30.0, "cards": 0.2}


def quote(item, qty, rush=False):
    total = RATES.get(item, 1.0) * qty
    return round(total + (15 if rush else 0), 2)
