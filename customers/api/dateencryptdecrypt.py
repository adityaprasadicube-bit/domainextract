import base64

SECRET = "cdr_secure_key"

def encrypt_value(value):
    raw = (value + SECRET).encode()
    return base64.b64encode(raw).decode()

def decrypt_value(value):
    decoded = base64.b64decode(value).decode()
    return decoded.replace(SECRET, "")