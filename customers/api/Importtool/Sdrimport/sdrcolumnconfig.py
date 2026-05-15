import json
import os
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.conf import settings

CONFIG_FILE =     os.path.join(settings.BASE_DIR,
    "api", "data", "column_config.json"
)


class ColumnConfigAPI(APIView):
    def post(self, request):
        report = request.data.get("report")
        action = request.data.get("action")
        columns = request.data.get("columns", [])

        if not report or not action:
            return Response(
                {"error": "report and action are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Load existing config
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
        else:
            config = {}

        # Get current columns
        current_columns = set(config.get(report, []))

        # Perform action
        if action == "add":
            current_columns.update(columns)

        elif action == "remove":
            current_columns.difference_update(columns)

        else:
            return Response(
                {"error": "Invalid action. Use 'add' or 'remove'"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Update config
        config[report] = list(current_columns)

        # Save back to file
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)

        return Response({
            "message": "Column config updated",
            "report": report,
            "columns": config[report]
        }, status=status.HTTP_200_OK)
