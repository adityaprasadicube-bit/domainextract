import json
import hashlib

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import User  # ✅ MongoEngine model
from .license_utils import validate_license


# 🔐 Password hashing (same as signup)
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


@csrf_exempt
def login_view(request):

    print("\n========== LOGIN REQUEST START ==========\n")

    if request.method != "POST":
        print("❌ Invalid method:", request.method)
        return JsonResponse({
            "status": "error",
            "message": "Invalid request method"
        }, status=405)

    try:
        body = json.loads(request.body)
        print("📥 Incoming body:", body)

        mobile = body.get("username")  # frontend uses username
        password = body.get("password")

        print("📱 Mobile:", mobile)
        print("🔑 Password present:", bool(password))

        # -----------------------------
        # Validate input
        # -----------------------------
        if not mobile or not password:
            print("❌ Missing credentials")
            return JsonResponse({
                "status": "error",
                "message": "Username and password required"
            }, status=400)

        # -----------------------------
        # 🔐 STEP 1: LICENSE CHECK
        # -----------------------------
        # -----------------------------
        # 🔐 STEP 1: LICENSE CHECK
        # -----------------------------
        valid, msg, is_expired = validate_license()
        print("🔐 License check:", valid, msg, "| Expired:", is_expired)

        if not valid:
            print("❌ License invalid")
            return JsonResponse({
                "status": "error",
                "message": msg
            }, status=403)

        # ⚠️ Allow login even if expired
        if is_expired:
            print("⚠️ License expired but login allowed")

        # -----------------------------
        # 👤 STEP 2: FIND USER (Mongo)
        # -----------------------------
        user = User.objects(mobile=mobile).first()

        if not user:
            print("❌ User not found")
            return JsonResponse({
                "status": "error",
                "message": "User not found"
            }, status=401)

        # -----------------------------
        # 🔑 STEP 3: PASSWORD CHECK
        # -----------------------------
        if user.password != hash_password(password):
            print("❌ Invalid password")
            return JsonResponse({
                "status": "error",
                "message": "Invalid credentials"
            }, status=401)

        # -----------------------------
        # ✅ SUCCESS
        # -----------------------------
        print("✅ Login successful")

        return JsonResponse({
            "status": "success",
            "message": "Login successful",
            "data": {
                "mobile": user.mobile
            }
        })

    except Exception as e:
        print("🔥 LOGIN ERROR:", str(e))
        return JsonResponse({
            "status": "error",
            "message": "Something went wrong",
            "error": str(e)
        }, status=500)