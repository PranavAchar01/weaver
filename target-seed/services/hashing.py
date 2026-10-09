import hashlib


def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()


def check_password(password, digest):
    return hash_password(password) == digest
