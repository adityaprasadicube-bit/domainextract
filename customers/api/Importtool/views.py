"""
Django REST API views for CDR and IPDR file uploads
Process files directly from frontend without saving to disk
Store only filename in database
"""

import os
import logging
from datetime import datetime
from typing import List
from io import BytesIO

from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .utils.cdr_handling import file_categorisation_optimized
from .Ipdr.ipdr_format_handling import file_categorisation_ipdr
from .utils.upload_cdr_to_db import manage_indexes
from ..license_utils import validate_license

logger = logging.getLogger(__name__)

# -----------------------------
# HELPER FUNCTIONS
# -----------------------------

def process_uploaded_files_in_memory(uploaded_files) -> List[tuple]:
    """
    Process uploaded files directly in memory without saving to disk
    Returns list of tuples: [(file_object, filename), ...]

    This function is used by BOTH CDR and IPDR endpoints.
    """
    file_data = []

    for f in uploaded_files:
        # Read file content into memory
        file_content = BytesIO()
        for chunk in f.chunks():
            file_content.write(chunk)
        file_content.seek(0)  # Reset pointer to beginning

        # Store file object and original filename
        file_data.append((file_content, f.name))
        logger.info(f"Loaded file in memory: {f.name}")

    return file_data


# -----------------------------
# UPLOAD CDR FILE
# -----------------------------
@csrf_exempt
@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
def upload_cdr_file(request):
    start_time = datetime.now()

    try:
        # # 🔐 LICENSE CHECK (MUST BE FIRST)
        # valid, msg, is_expired = validate_license()
        # if not valid:
        #     return Response({"status": "error", "message": msg}, status=403)
        # if is_expired:
        #     return Response({"status": "error", "message": "License expired."}, status=403)

        crime_name = request.POST.get("crimename")
        area_location = request.POST.get("arealocation")
        uploaded_files = request.FILES.getlist("file")

        if not crime_name or not area_location or not uploaded_files:
            return Response({
                "status": "error",
                "message": "Missing Crime Name / Area Location / Files"
            }, status=400)

        file_data = process_uploaded_files_in_memory(uploaded_files)
        filenames = [filename for _, filename in file_data]

        logger.info(f"Processing {len(file_data)} CDR files: {filenames}")

        result = file_categorisation_optimized(
            file_data=file_data,
            crime_name=crime_name,
            area_location=area_location
        )

        print("the result is ", result)

        processing_time = (datetime.now() - start_time).total_seconds()

        if not file_data:
            return Response({
                "status": "error",
                "message": "No valid files received for processing"
            }, status=400)

        # ── result is now:
        # {
        #   "inserted": X, "duplicates": Y, "updated": Z, "skipped": W,
        #   "result": { "filename.csv": [{...}], ... }
        # }
        # Validate using the nested "result" dict, not the top-level keys.
        per_file = result.get("result", {})

        all_failed = True
        for fname, records in per_file.items():
            # Skip the grand-total keys (int values) that appear in archive responses
            if not isinstance(records, list):
                continue
            for r in records:
                if isinstance(r, dict) and r.get("status") != "failed":
                    all_failed = False
                    break
            if not all_failed:
                break

        if all_failed and per_file:
            return Response({
                "status": "error",
                "message": "File upload succeeded but processing failed",
                "files_received": len(file_data),
                "result": result
            }, status=500)

        return Response({
            "status": "success",
            "message": f"Processed {len(file_data)} CDR files in {processing_time:.2f}s",
            "files_processed": len(file_data),
            "filenames": filenames,
            "crime_name": crime_name,
            "area_location": area_location,
            "result": result
        }, status=200)

    except Exception as e:
        logger.error(f"Error in upload_cdr_file: {e}", exc_info=True)
        return Response({"status": "error", "message": str(e)}, status=500)


# -----------------------------
# UPLOAD IPDR FILE
# -----------------------------

@csrf_exempt
@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
def upload_ipdr_file(request):
    start_time = datetime.now()

    try:
        # valid, msg, is_expired = validate_license()
        # if not valid:
        #     return Response({"status": "error", "message": msg}, status=403)
        # if is_expired:
        #     return Response({"status": "error", "message": "License expired."}, status=403)

        crime_name = request.POST.get("crimename")
        area_location = request.POST.get("arealocation")
        uploaded_files = request.FILES.getlist("file")

        if not crime_name or not area_location or not uploaded_files:
            return Response({
                "status": "error",
                "message": "Missing Crime Name / Area Location / Files"
            }, status=400)

        file_data = process_uploaded_files_in_memory(uploaded_files)
        filenames = [filename for _, filename in file_data]

        logger.info(f"Processing {len(file_data)} IPDR files: {filenames}")

        result = file_categorisation_ipdr(
            file_data=file_data,
            crime_name=crime_name,
            area_location=area_location
        )

        processing_time = (datetime.now() - start_time).total_seconds()

        if not file_data:
            return Response({
                "status": "error",
                "message": "No valid files received for processing"
            }, status=400)

        if not result:
            return Response({
                "status": "error",
                "message": "File upload succeeded but processing failed",
                "files_received": len(file_data)
            }, status=500)

        return Response({
            "status": "success",
            "message": f"Processed {len(file_data)} IPDR files in {processing_time:.2f}s",
            "files_processed": len(file_data),
            "filenames": filenames,
            "crime_name": crime_name,
            "area_location": area_location,
            "result": result
        }, status=200)

    except Exception as e:
        logger.error(f"Error in upload_ipdr_file: {e}", exc_info=True)
        return Response({"status": "error", "message": str(e)}, status=500)


# -----------------------------
# MANAGE DATABASE INDEXES
# -----------------------------

@csrf_exempt
@api_view(['POST'])
def manage_database_indexes(request):
    """
    Manage MongoDB indexes (Admin only)
    Supports both CDR and IPDR collections
    """
    action = request.query_params.get('action', 'rebuild')
    if action not in ['drop', 'rebuild']:
        return Response(
            {"status": "error", "message": "Invalid action. Use 'drop' or 'rebuild'"},
            status=400
        )

    try:
        manage_indexes(action=action)
        return Response(
            {"status": "success", "message": f"Indexes {action} completed", "action": action},
            status=200
        )
    except Exception as e:
        logger.error(f"Index management failed: {str(e)}", exc_info=True)
        return Response({"status": "error", "message": str(e)}, status=500)