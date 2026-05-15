from requests import delete

from .Summary import get_CDR_summary
from django.shortcuts import render
from datetime import datetime, timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from mongoengine import connect, get_db
from ..models import LRNCode, MobileOperator, ImeiDetails
from ..serializers import LRNCodeSerializer, MobileOperatorSerializer, DeviceInfoSerializer

from .smsprocesscing import process_sms_details_provider_only


class CDRSummaryView(APIView):
    def post(self, request):
        try:
            # Get request data
            seq_ids = request.data.get("seq_ids")
            from_date = request.data.get("from_date")
            to_date = request.data.get("to_date")
            filter_type = request.data.get("filter_type", "normal")

            if not seq_ids:
                return Response(
                    {"error": "Missing required parameter seq_ids"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if isinstance(seq_ids, str):
                seq_ids = [seq_ids]

            from_dt = datetime.fromisoformat(from_date) if from_date else None
            to_dt = datetime.fromisoformat(to_date) if to_date else None

            # Fetch summary
            results = get_CDR_summary(
                seq_ids,
                from_dt,
                to_dt,
                ignore_dates=False,
                filter_type=filter_type
            )

            # Special handling for weekly report - group data by week ranges
            if filter_type == "weekly":
                from collections import defaultdict

                # First, we need to get FromDate from the first result to calculate week ranges
                if not results:
                    return Response([], status=status.HTTP_200_OK)

                # Group by CdrNo and WeekNum
                weekly_data = defaultdict(lambda: {
                    'CdrNo': '',
                    'WeekNum': 0,
                    'TotalCalls': 0,
                    'OutCalls': 0,
                    'InCalls': 0,
                    'OutSms': 0,
                    'InSms': 0,
                    'OtherCalls': 0,
                    'WifiCalls': 0,
                    'RoamCalls': 0,
                    'RoamSms': 0,
                    'TotalDuration': 0,
                    'TotalCellIDs': 0,
                    'TotalIMEIs': 0,
                    'TotalIMSIs': 0,
                    'FirstCallDate': None,
                    'LastCallDate': None,
                    'FirstCallTime': None,
                    'LastCallTime': None
                })

                for rec in results:
                    cdr_no = rec.get('CdrNo', '')
                    week_num = rec.get('WeekNum', 0)
                    key = (cdr_no, week_num)

                    if not weekly_data[key]['CdrNo']:
                        weekly_data[key]['CdrNo'] = cdr_no
                        weekly_data[key]['WeekNum'] = week_num

                    # Aggregate values
                    weekly_data[key]['TotalCalls'] += rec.get('TotalCalls', 0)
                    weekly_data[key]['OutCalls'] += rec.get('OutCalls', 0)
                    weekly_data[key]['InCalls'] += rec.get('InCalls', 0)
                    weekly_data[key]['OutSms'] += rec.get('OutSms', 0)
                    weekly_data[key]['InSms'] += rec.get('InSms', 0)
                    weekly_data[key]['OtherCalls'] += rec.get('OtherCalls', 0)
                    weekly_data[key]['WifiCalls'] += rec.get('WifiCalls', 0)
                    weekly_data[key]['RoamCalls'] += rec.get('RoamCalls', 0)
                    weekly_data[key]['RoamSms'] += rec.get('RoamSms', 0)
                    weekly_data[key]['TotalDuration'] += rec.get('TotalDuration', 0)

                    # Since aggregation now returns proper counts, just take the value directly
                    # (all records for the same week should have the same count after aggregation fix)
                    weekly_data[key]['TotalCellIDs'] = rec.get('TotalCellIDs', 0)
                    weekly_data[key]['TotalIMEIs'] = rec.get('TotalIMEIs', 0)
                    weekly_data[key]['TotalIMSIs'] = rec.get('TotalIMSIs', 0)

                    # Track first and last call dates
                    first_date_str = rec.get('FirstCallDate')
                    last_date_str = rec.get('LastCallDate')
                    first_time_str = rec.get('FirstCallTime')
                    last_time_str = rec.get('LastCallTime')

                    if first_date_str:
                        try:
                            first_dt = datetime.strptime(first_date_str, '%d-%m-%Y')
                            if weekly_data[key]['FirstCallDate'] is None or first_dt < weekly_data[key]['FirstCallDate']:
                                weekly_data[key]['FirstCallDate'] = first_dt
                                weekly_data[key]['FirstCallTime'] = first_time_str
                        except:
                            pass

                    if last_date_str:
                        try:
                            last_dt = datetime.strptime(last_date_str, '%d-%m-%Y')
                            if weekly_data[key]['LastCallDate'] is None or last_dt > weekly_data[key]['LastCallDate']:
                                weekly_data[key]['LastCallDate'] = last_dt
                                weekly_data[key]['LastCallTime'] = last_time_str
                        except:
                            pass

                # We need to get the FromDate to calculate proper week ranges
                # Fetch FromDate from DataNexus collection
                db = get_db()
                nexus_collection = db['DataNexus']

                # Get FromDate for each CDR number
                from_dates = {}
                for key, data in weekly_data.items():
                    cdr_no = data['CdrNo']
                    if cdr_no not in from_dates:
                        nexus_doc = nexus_collection.find_one({'CDRNo_Or_ImeiNo': cdr_no})
                        if nexus_doc and 'FromDate' in nexus_doc:
                            from_dates[cdr_no] = nexus_doc['FromDate']

                # Convert to list and format
                sorted_data = []
                for key, data in weekly_data.items():
                    # Calculate TotalDays
                    total_days = 0
                    if data['FirstCallDate'] and data['LastCallDate']:
                        total_days = (data['LastCallDate'] - data['FirstCallDate']).days + 1

                    # Get FromDate for this CDR
                    cdr_no = data['CdrNo']
                    from_date = from_dates.get(cdr_no)

                    # Week number from MongoDB is 0-based offset from FromDate's week, we need to make it 1-based
                    week_num = int(data['WeekNum']) + 1

                    # Determine the year for the week label based on the actual call dates
                    week_year = data['FirstCallDate'].year if data['FirstCallDate'] else datetime.now().year

                    week_label = f"{week_year}. W({week_num}). "

                    if data['FirstCallDate'] and data['LastCallDate']:
                        week_label += f"{data['FirstCallDate'].strftime('%d/%b/%Y')} - {data['LastCallDate'].strftime('%d/%b/%Y')}"
                    elif from_date:
                        # Calculate week start (Sunday) containing from_date
                        from_date_day_of_week = from_date.weekday()  # 0=Monday, 6=Sunday
                        from_date_sunday = from_date - timedelta(days=(from_date_day_of_week + 1) % 7)

                        # Calculate the actual week start for this week number
                        week_start = from_date_sunday + timedelta(weeks=(week_num - 1))
                        week_end = week_start + timedelta(days=6)
                        week_label += f"{week_start.strftime('%d/%b/%Y')} - {week_end.strftime('%d/%b/%Y')}"

                    row = {
                        'CdrNo': data['CdrNo'],
                        'Week': week_label,
                        'TotalCalls': data['TotalCalls'],
                        'OutCalls': data['OutCalls'],
                        'InCalls': data['InCalls'],
                        'OutSms': data['OutSms'],
                        'InSms': data['InSms'],
                        'OtherCalls': data['OtherCalls'],
                        'WifiCalls': data['WifiCalls'],
                        'RoamCalls': data['RoamCalls'],
                        'RoamSms': data['RoamSms'],
                        'TotalDuration': data['TotalDuration'],
                        'TotalCellIDs': data['TotalCellIDs'],
                        'TotalIMEIs': data['TotalIMEIs'],
                        'TotalIMSIs': data['TotalIMSIs'],
                        'TotalDays': total_days,
                        'FirstCallDate': data['FirstCallDate'].strftime('%d-%m-%Y') if data['FirstCallDate'] else '',
                        'FirstCallTime': data['FirstCallTime'] or '',
                        'LastCallDate': data['LastCallDate'].strftime('%d-%m-%Y') if data['LastCallDate'] else '',
                        'LastCallTime': data['LastCallTime'] or ''
                    }
                    sorted_data.append(row)

                # Sort by CdrNo and then by week number descending (newest first)
                sorted_data = sorted(sorted_data, key=lambda x: (
                    x.get('CdrNo', ''),
                    -float(x.get('Week', 'W(0)').split('W(')[1].split(')')[0]) if 'W(' in x.get('Week', '') else 0
                ))

                return Response(sorted_data, status=status.HTTP_200_OK)

            # Special handling for hourly report - pivot the data
            if filter_type == "hourly":
                # Group by CdrNo and Date, then pivot hours
                from collections import defaultdict

                pivoted = defaultdict(lambda: {
                    'CdrNo': '',
                    'Date': '',
                    'TotalCalls': 0
                })

                for rec in results:
                    key = (rec.get('CdrNo', ''), rec.get('Date', ''))
                    hour = rec.get('Hour', 0)
                    calls = rec.get('TotalCalls', 0)

                    if not pivoted[key]['CdrNo']:
                        pivoted[key]['CdrNo'] = rec.get('CdrNo', '')
                        pivoted[key]['Date'] = rec.get('Date', '')

                    # Create hour range column (e.g., "0 - 1", "1 - 2")
                    hour_col = f"{hour} - {hour + 1}"
                    pivoted[key][hour_col] = calls
                    pivoted[key]['TotalCalls'] += calls

                # Convert to list with proper column order
                sorted_data = []
                for key, data in pivoted.items():
                    row = {
                        'CdrNo': data['CdrNo'],
                        'Date': data['Date'],
                        'TotalCalls': data['TotalCalls']
                    }

                    # Add all 24 hours in order
                    for h in range(24):
                        hour_col = f"{h} - {h + 1}"
                        row[hour_col] = data.get(hour_col, 0)

                    sorted_data.append(row)

                # Sort by date (newest first)
                sorted_data = sorted(sorted_data,
                                     key=lambda x: datetime.strptime(x.get('Date', '01/01/1970'), '%d/%m/%Y'),
                                     reverse=True)

                return Response(sorted_data, status=status.HTTP_200_OK)

            # Special handling for weekday report - show each day of week as separate row
            elif filter_type == "weekday":
                # Sort by day of week order
                day_order = {"Sun": 1, "Mon": 2, "Tue": 3, "Wed": 4, "Thu": 5, "Fri": 6, "Sat": 7}
                sorted_data = sorted(results, key=lambda x: day_order.get(x.get('Day', 'Sun'), 8))

                # Format the Day column and recalculate TotalDays as weeks
                for rec in sorted_data:
                    day_name = rec.get('Day', 'Sun')
                    day_num = day_order.get(day_name, 1)
                    rec['Day'] = f"{day_num}. {day_name}"

                    # Recalculate TotalDays as number of weeks
                    first_date = rec.get('FirstCallDate')
                    last_date = rec.get('LastCallDate')
                    if first_date and last_date:
                        try:
                            first_dt = datetime.strptime(first_date, '%d-%m-%Y')
                            last_dt = datetime.strptime(last_date, '%d-%m-%Y')
                            days_diff = (last_dt - first_dt).days
                            rec['TotalDays'] = round(days_diff / 7) + 1  # Number of weeks
                        except:
                            rec['TotalDays'] = 0

                    # Remove Provider and Type if present
                    rec.pop('Provider', None)
                    rec.pop('Type', None)
                    rec.pop('DayOfWeek', None)

                return Response(sorted_data, status=status.HTTP_200_OK)

            # For daily and daily_hourly, return results directly without processing Provider/Type
            if filter_type in ["daily", "daily_hourly"]:
                # Sort by date for daily report
                if filter_type == "daily":
                    sorted_data = sorted(results,
                                         key=lambda x: datetime.strptime(x.get('Date', '01/01/1970'), '%d/%m/%Y'),
                                         reverse=True)
                elif filter_type == "daily_hourly":
                    sorted_data = sorted(results, key=lambda x: (x.get('Day', ''), x.get('Hour', 0)), reverse=True)
                else:
                    sorted_data = results

                return Response(sorted_data, status=status.HTTP_200_OK)

            # For other filter types, process Provider and Type
            # Sort by TotalCalls descending
            sorted_data = sorted(results, key=lambda x: x.get('TotalCalls', 0), reverse=True)

            # Prepare caches for LRN and MobileOperator
            lrns = {rec.get("LRN") for rec in sorted_data if rec.get("LRN")}
            b_party_codes = {rec.get("B_Party_Code") for rec in sorted_data if rec.get("B_Party_Code")}
            imeis = {rec.get("IMEI_TAC") for rec in sorted_data if rec.get("IMEI_TAC")}

            lrn_cache = {str(r.id): LRNCodeSerializer(r).data for r in LRNCode.objects.filter(id__in=lrns)}
            mobile_cache = {str(r.id): MobileOperatorSerializer(r).data for r in
                            MobileOperator.objects.filter(id__in=b_party_codes)}
            imeis_cache = {str(r.id): DeviceInfoSerializer(r).data for r in ImeiDetails.objects.filter(id__in=imeis)}

            # Assign Provider and Type
            for rec in sorted_data:
                b_party = str(rec.get("B_Party") or "")
                lrn_id = rec.get("LRN")
                mcode_id = rec.get("B_Party_Code")
                imei = rec.get("IMEI_TAC")

                if imei and str(imei) in imeis_cache:
                    imei_data = imeis_cache[str(imei)]
                    rec["IMEI_MANUFACTURER"] = f"{imei_data.get('manufacturer')},{imei_data.get('brand')}"
                    rec["Device_Type"] = imei_data.get('devicetype')

                if len(b_party) == 10 and b_party.startswith(('6', '7', '8', '9')):
                    if lrn_id and str(lrn_id) in lrn_cache:
                        lrn_data = lrn_cache[str(lrn_id)]
                        rec['Provider'] = f"{lrn_data.get('circle')}--{lrn_data.get('operator')}"
                        rec['Type'] = "Mobile"
                    elif mcode_id and str(mcode_id) in mobile_cache:
                        mcode_data = mobile_cache[str(mcode_id)]
                        rec['Provider'] = f"{mcode_data.get('Circle')}--{mcode_data.get('Operator')}"
                        rec['Type'] = "Mobile"
                elif b_party.startswith('140'):
                    rec['Provider'] = 'Telemarketing'
                    rec['Type'] = 'Telemarketing'
                elif b_party.startswith(('1800', '1860')):
                    rec['Provider'] = 'Toll Free'
                    rec['Type'] = 'Toll Free'
                elif '-' in b_party:
                    sms_info = process_sms_details_provider_only(b_party)
                    rec['Provider'] = sms_info['Provider']
                    rec['Type'] = sms_info['Type']
                else:
                    rec.update({'Provider': 'Unknown', 'Type': 'Unknown'})

            return Response(sorted_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)