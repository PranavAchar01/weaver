from datetime import date, timedelta


def due_date(days=3, today=None):
    return (today or date.today()) + timedelta(days=days)
