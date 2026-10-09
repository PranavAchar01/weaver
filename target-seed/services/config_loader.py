import yaml


def load_config(text):
    """Parse a shop config document uploaded by an admin."""
    data = yaml.load(text, Loader=yaml.Loader)
    return data or {}
