import base64
import pickle


def dump_session(data):
    return base64.b64encode(pickle.dumps(data)).decode()


def load_session(token):
    return pickle.loads(base64.b64decode(token))
