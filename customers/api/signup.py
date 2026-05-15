import json
import base64
import re
import hashlib

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import User  # ✅ MongoEngine model
from .license_utils import validate_license_data, LICENSE_FILE, get_machine_id


# 🔐 Password hashing
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


@csrf_exempt
def signup(request):

    print("\n========== SIGNUP REQUEST START ==========\n")

    if request.method != "POST":
        print("❌ Invalid method:", request.method)
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        body = json.loads(request.body)
        print("📥 Incoming body:", body)

        mobile = body.get("mobile")
        password = body.get("password")
        license_key = body.get("license_key")

        # 🔥 Clean license key (important)
        if license_key:
            license_key = re.sub(r'\s+', '', license_key)

        print("📱 Mobile:", mobile)
        print("🔑 Password present:", bool(password))
        print("🔐 Raw License Key length:", len(license_key) if license_key else 0)

        if not mobile or not password or not license_key:
            print("❌ Missing required fields")
            return JsonResponse({
                "status": "error",
                "message": "Mobile, password and license required"
            }, status=400)

        # -----------------------------
        # 🔐 Decode license key
        # -----------------------------
        try:
            decoded = base64.b64decode(license_key).decode()
            print("\n🔓 Decoded License JSON:\n", decoded)

            license_obj = json.loads(decoded)
            print("✅ License JSON parsed successfully")

            # 🔥 Clean signature also (extra safety)
            if "signature" in license_obj:
                license_obj["signature"] = re.sub(r'\s+', '', license_obj["signature"])

        except Exception as e:
            print("❌ Base64 decode or JSON parse failed:", str(e))
            return JsonResponse({
                "status": "error",
                "message": "Invalid license format"
            }, status=400)

        # -----------------------------
        # 🔍 DEBUG LICENSE DETAILS
        # -----------------------------
        data = license_obj.get("data", {})
        print("\n========== LICENSE DEBUG ==========")
        print("📄 License Data:", data)
        print("🖥️ License Machine ID:", data.get("machine_id"))

        current_machine = get_machine_id()
        print("💻 Current Machine ID:", current_machine)

        print("📅 Expiry:", data.get("expires_at"))
        print("==================================\n")

        # -----------------------------
        # 🔐 Validate license
        # -----------------------------
        valid, msg, is_expired = validate_license_data(license_obj)
        print("🔍 Validation Result:", valid, "| Message:", msg)

        if not valid:
            print("❌ License validation failed")
            return JsonResponse({
                "status": "error",
                "message": msg
            }, status=403)

        # -----------------------------
        # 👤 Check user exists (Mongo)
        # -----------------------------
        if User.objects(mobile=mobile).first():
            print("❌ User already exists")
            return JsonResponse({
                "status": "error",
                "message": "User already exists"
            }, status=400)

        # -----------------------------
        # 💾 Save license file
        # -----------------------------
        with open(LICENSE_FILE, "w") as f:
            f.write(license_key)

        print("💾 License saved to file")

        # -----------------------------
        # 👤 Create user (Mongo)
        # -----------------------------
        User(
            mobile=mobile,
            password=hash_password(password)
        ).save()

        print("✅ User created in MongoDB")

        print("\n========== SIGNUP SUCCESS ==========\n")

        return JsonResponse({
            "status": "success",
            "message": "Signup successful ✅"
        })

    except Exception as e:
        print("🔥 UNEXPECTED ERROR:", str(e))
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=500)