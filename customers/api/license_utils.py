import json
import subprocess
import os
import base64
import hashlib
from datetime import datetime

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .dateencryptdecrypt import encrypt_value, decrypt_value
from .models import SystemMeta


# -----------------------------
# CONFIG
# -----------------------------
DATA_DIR = os.getenv("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)

LICENSE_FILE = os.path.join(DATA_DIR, "license.key")
RUNTIME_FILE = os.path.join(DATA_DIR, ".sys_cache_hidden")

SECRET = "cdr_secure_runtime_key"  # 🔐 change in production


# -----------------------------
# LOAD PUBLIC KEY
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_KEY_PATH = os.path.join(BASE_DIR, "public_key.pem")

with open(PUBLIC_KEY_PATH, "rb") as f:
    PUBLIC_KEY = serialization.load_pem_public_key(f.read())


# -----------------------------
# MACHINE ID
# -----------------------------
def get_machine_id():
    machine_id = os.getenv("MACHINE_ID")
    if machine_id:
        return machine_id

    try:
        output = subprocess.check_output(
            'powershell -command "(Get-CimInstance Win32_ComputerSystemProduct).UUID"',
            shell=True
        ).decode().strip()
        return output
    except:
        return "UNKNOWN"


# -----------------------------
# HASH (ANTI-TAMPER)
# -----------------------------
def generate_hash(data):
    raw = f"{data['time']}{data['elapsed']}{data['machine']}{SECRET}"
    return hashlib.sha256(raw.encode()).hexdigest()


# -----------------------------
# FILE HELPERS (INVISIBLE BACKUP)
# -----------------------------
def save_runtime_file(data):
    try:
        with open(RUNTIME_FILE, "w") as f:
            f.write(encrypt_value(json.dumps(data)))
    except:
        pass


def load_runtime_file():
    try:
        if not os.path.exists(RUNTIME_FILE):
            return None

        with open(RUNTIME_FILE, "r") as f:
            return json.loads(decrypt_value(f.read()))
    except:
        return None


# -----------------------------
# VALIDATE LICENSE FROM FILE
# -----------------------------
def validate_license():
    try:
        if not os.path.exists(LICENSE_FILE):
            return False, "License not activated", False

        with open(LICENSE_FILE, "r") as f:
            content = f.read().strip()

        license_obj = None

        try:
            decoded = base64.b64decode(content).decode()
            license_obj = json.loads(decoded)
            print("✅ License format: BASE64")
        except:
            pass

        if license_obj is None:
            try:
                license_obj = json.loads(content)
                print("⚠️ License format: JSON (old)")
            except:
                return False, "Invalid license format", False

        return validate_license_data(license_obj)

    except Exception as e:
        print("❌ validate_license error:", str(e))
        return False, "License invalid", False


# -----------------------------
# VALIDATE LICENSE OBJECT
# -----------------------------
def validate_license_data(license_obj):
    try:
        data = license_obj["data"]

        # 🔐 SIGNATURE VERIFY
        signature = bytes.fromhex(license_obj["signature"])
        message = json.dumps(data, separators=(',', ':')).encode()

        PUBLIC_KEY.verify(
            signature,
            message,
            padding.PKCS1v15(),
            hashes.SHA256()
        )

        now = datetime.now()

        # 🔐 ACTIVATION START CHECK
        issued_at = datetime.fromisoformat(data["issued_at"])
        if now < issued_at:
            return False, "System time manipulation detected ❌", False

        # 🔐 MACHINE CHECK
        if data["machine_id"] != get_machine_id():
            return False, "Invalid machine", False

        # 🔐 EXPIRY LOCK
        expired_record = SystemMeta.objects(key="expired_flag").first()
        if expired_record and expired_record.value == "true":
            return True, "License expired", True

        expiry = datetime.fromisoformat(data["expires_at"])

        # 🔥 TIME + ELAPSED + ANTI-TAMPER
        valid_time, runtime_expired = check_system_time_valid(expiry)

        if not valid_time:
            return False, "System time manipulation detected ❌", False

        is_expired = now > expiry or runtime_expired

        if is_expired:
            SystemMeta.objects(key="expired_flag").update_one(
                set__value="true",
                upsert=True
            )
            return True, "License expired", True

        return True, "Valid", False

    except Exception as e:
        print("❌ validate_license_data error:", str(e))
        return False, "Invalid license", False


# -----------------------------
# SYSTEM TIME + ELAPSED + BACKUP
# -----------------------------
def check_system_time_valid(expiry_time):
    now = datetime.now()

    try:
        db_record = SystemMeta.objects(key="runtime").first()
        file_record = load_runtime_file()

        # 🚨 ANTI-DELETE
        if (db_record and not file_record) or (file_record and not db_record):
            print("❌ Runtime tampering (missing storage)")
            return False, False

        # -----------------------------
        # FIRST RUN
        # -----------------------------
        if not db_record and not file_record:
            data = {
                "time": now.isoformat(),
                "elapsed": 0,
                "machine": get_machine_id()
            }
            data["hash"] = generate_hash(data)

            encrypted = encrypt_value(json.dumps(data))

            SystemMeta.objects(key="runtime").update_one(
                set__value=encrypted,
                upsert=True
            )

            save_runtime_file(data)

            return True, False

        # -----------------------------
        # LOAD BOTH
        # -----------------------------
        db_data = json.loads(decrypt_value(db_record.value))
        file_data = file_record

        # 🔥 CONSISTENCY CHECK
        if db_data != file_data:
            print("❌ DB & File mismatch detected")
            return False, False

        # 🔐 HASH CHECK
        if db_data.get("hash") != generate_hash(db_data):
            print("❌ Runtime tampering detected")
            return False, False

        last_time = datetime.fromisoformat(db_data["time"])
        elapsed = db_data["elapsed"]
        machine = db_data["machine"]

        # 🔐 MACHINE CHECK
        if machine != get_machine_id():
            return False, False

        # 🔥 TIME DELTA
        delta = (now - last_time).total_seconds()

        if delta < 0:
            print("❌ Time rollback detected")
            return False, False

        # ✅ ADD ELAPSED
        elapsed += delta

        # 🔥 EXPIRY CHECK
        is_expired = now > expiry_time

        # 🔥 UPDATE DATA
        new_data = {
            "time": now.isoformat(),
            "elapsed": elapsed,
            "machine": machine
        }
        new_data["hash"] = generate_hash(new_data)

        encrypted = encrypt_value(json.dumps(new_data))

        # 🔥 SAVE BOTH
        SystemMeta.objects(key="runtime").update_one(
            set__value=encrypted,
            upsert=True
        )

        save_runtime_file(new_data)

        return True, is_expired

    except Exception as e:
        print("❌ Runtime error:", str(e))
        return False, False