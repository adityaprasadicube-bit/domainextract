from datetime import datetime
from django.http import JsonResponse


class APILifetimeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

        # Set allowed date range here
        self.start_date = datetime(2024, 6, 1, 0, 0, 0)
        self.end_date = datetime(2026, 12, 31, 23, 59, 59)

    def __call__(self, request):
        now = datetime.now()
        if not (self.start_date <= now <= self.end_date):
            return JsonResponse({
                "status": 403,
                "message": "API access has expired. Please contact support."
            }, status=403)

        response = self.get_response(request)
        return response