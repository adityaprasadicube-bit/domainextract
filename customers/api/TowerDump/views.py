from django.shortcuts import render
from datetime import datetime
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .Dynamicfilters.Providerotherstate import get_Providerandotherstateinformation
from .Sumary.sumary import get_towerdump_summary


class TowerDumpSummaryView(APIView):
    """
    API to summarize TowerDump CDR records
    """

    def post(self, request):
        try:
            seq_ids = request.data.get("seq_ids")
            from_date = request.data.get("from_date")
            to_date = request.data.get("to_date")
            filter_type = request.data.get("filter")
            include_sdr = request.data.get('',False)

            print(seq_ids,from_date,to_date)

            if not seq_ids or not from_date or not to_date:
                return Response(
                    {"error": "Missing required parameters (seq_ids, from_date, to_date)"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            from_dt = datetime.fromisoformat(from_date)
            to_dt = datetime.fromisoformat(to_date)
            print(from_dt)
            print(to_dt)

            results = get_towerdump_summary(seq_ids, from_dt, to_dt,filter_type,include_sdr)
            sorted_data = sorted([json_data for json_data in results], key=lambda x: x['Total Calls'],
                                 reverse=True)
            return Response(sorted_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProviderotherstateAPIView(APIView):
    """
    API to summarize TowerDump CDR records
    """

    def post(self, request):
        try:
            # -------------------------------
            # 1. Validate and parse request data
            # -------------------------------
            seq_ids = request.data.get("seq_ids")
            from_date = request.data.get("from_date")
            to_date = request.data.get("to_date")

            # Validate presence
            if not seq_ids or not from_date or not to_date:
                return Response(
                    {"error": "Missing required parameters (seq_ids, from_date, to_date)"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate seq_ids is a list and not empty
            if not isinstance(seq_ids, list) or len(seq_ids) == 0:
                return Response(
                    {"error": "seq_ids must be a non-empty list"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Parse dates with error handling
            try:
                from_dt = datetime.fromisoformat(from_date)
                to_dt = datetime.fromisoformat(to_date)
            except ValueError as e:
                return Response(
                    {"error": f"Invalid date format: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate date range
            if from_dt > to_dt:
                return Response(
                    {"error": "from_date must be before or equal to to_date"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # -------------------------------
            # 2. Get TowerDump summary
            # -------------------------------
            results = get_Providerandotherstateinformation(seq_ids, from_dt, to_dt)

            # Sort by total_calls (matching the actual key from your function)
            sorted_data = sorted(results, key=lambda x: x.get('total_calls', 0), reverse=True)

            return Response(sorted_data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in ProviderotherstateAPIView: {str(e)}", exc_info=True)

            return Response(
                {"error": "An error occurred while processing your request"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )