from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import subprocess
import os


@csrf_exempt
def get_system_id(request):
    try:
        # ✅ 1. Try Docker ENV (PRIMARY)
        machine_id = os.getenv("MACHINE_ID")

        if machine_id:
            source = "env"
        else:
            # ✅ 2. Fallback for local Windows
            try:
                machine_id = subprocess.check_output(
                    'powershell -command "(Get-CimInstance Win32_ComputerSystemProduct).UUID"',
                    shell=True
                ).decode().strip()
                source = "powershell"
            except Exception as e:
                print("❌ PowerShell failed:", str(e))
                machine_id = None
                source = "failed"

        return JsonResponse({
            "status": "success",
            "system_id": machine_id,
            "source": source  # 👈 for debugging (optional)
        })

    except Exception as e:
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=500)