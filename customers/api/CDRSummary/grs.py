from django.utils.dateparse import parse_datetime
from mongoengine import get_db
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
import re
from datetime import timedelta

# Import your utilities
from ..utilities import fetch_isd_json, fetch_landline_json


# -------------------------------------------------------------------------
# Helper Function: Number Classification
# -------------------------------------------------------------------------
def classify_number(bp_num, lrn_code, mobile_map, lrn_map, sms_map, landline_json, sorted_ll_codes, isd_json,
                    sorted_isd_codes):
    phone_type = "Unknown"
    service_provider = ""
    state_region = ""

    if not bp_num:
        return phone_type, service_provider, state_region

    if bp_num.isdigit():
        if len(bp_num) == 10 and bp_num[0] in '6789':
            if lrn_code and len(str(lrn_code)) == 4:
                phone_type = "Mobile(LRN)"
                try:
                    lrn_info = lrn_map.get(int(lrn_code))
                    if lrn_info:
                        service_provider = lrn_info.get('operator', '')
                        state_region = lrn_info.get('circle', '')
                except:
                    pass
            else:
                phone_type = "Mobile"
                try:
                    prefix = int(bp_num[:4])
                    mob_info = mobile_map.get(prefix)
                    if mob_info:
                        service_provider = mob_info.get('Operator', '')
                        state_region = mob_info.get('Circle', '')
                except:
                    pass
        elif len(bp_num) == 10 and bp_num.startswith('140'):
            phone_type = "Telemarketing"
            service_provider = "Telemarketing"
        elif len(bp_num) == 10 and (bp_num.startswith('1800') or bp_num.startswith('1860')):
            phone_type = "Toll Free"
            service_provider = "Toll Free"
        elif len(bp_num) == 10:
            matched_ll = False
            for ll_code in sorted_ll_codes:
                if bp_num.startswith(ll_code):
                    details = landline_json[ll_code]
                    phone_type = "Landline"
                    service_provider = f"{details.get('State', '')}-{details.get('City', '')}"
                    state_region = details.get('State', '')
                    matched_ll = True
                    break
            if not matched_ll:
                phone_type = "Landline (Unknown)"
        elif len(bp_num) > 10:
            if not (bp_num.startswith('1800') or bp_num.startswith('1860')):
                matched_isd = False
                for isd_code in sorted_isd_codes:
                    if bp_num.startswith(isd_code):
                        details = isd_json[isd_code]
                        phone_type = "ISD"
                        name = details.get('name', '')
                        code = details.get('code', '')
                        service_provider = f"{name}-{code}"
                        state_region = name
                        matched_isd = True
                        break
                if not matched_isd:
                    phone_type = "ISD (Unknown)"
        elif len(bp_num) == 5 and bp_num.startswith('5'):
            phone_type = "SMS Short Code"
            service_provider = "SMS Short Code"
        elif len(bp_num) in (3, 4):
            phone_type = "Customer Care"
            service_provider = "Customer Care"
    else:
        if '-' in bp_num:
            parts = bp_num.split('-')
            if len(parts) > 1:
                header = parts[1].strip()
                phone_type = "Transactional/Service"
                service_provider = header
                sms_info = sms_map.get(header)
                if sms_info:
                    service_provider = sms_info.get('address', service_provider)
                    if sms_info.get('type'):
                        phone_type = sms_info.get('type')
        elif bp_num.startswith('*') or bp_num.startswith('#'):
            phone_type = "MMI/USSD"
            service_provider = "MMI/USSD"
        elif any(c.isalpha() for c in bp_num):
            phone_type = "Service"
            service_provider = bp_num
    return phone_type, service_provider, state_region


def extract_roam_code(cgi_or_imsi):
    if not cgi_or_imsi: return None
    val_str = str(cgi_or_imsi)
    if len(val_str) < 5: return None
    mccmnc = val_str[:6] if len(val_str) > 5 else val_str[:5]
    if mccmnc.isdigit():
        if len(mccmnc) == 6:
            val_int = int(mccmnc)
            if val_int < 405750 and (val_int < 405025 or val_int > 405047):
                mccmnc = mccmnc[:5]
        return mccmnc
    return None


class GeneralReportView(APIView):
    def post(self, request):
        # 1. Setup & Inputs
        raw_seq_ids = request.data.get('seq_id')
        if not raw_seq_ids:
            return Response({'error': 'Please Select Atleast one CDR'}, status=status.HTTP_400_BAD_REQUEST)

        seq_ids = raw_seq_ids if isinstance(raw_seq_ids, list) else [raw_seq_ids]
        primary_seq_id = seq_ids[0]

        from_date = parse_datetime(request.data.get('from_date')) if request.data.get('from_date') else None
        to_date = parse_datetime(request.data.get('to_date')) if request.data.get('to_date') else None
        min_duration = request.data.get('min_duration')
        max_duration = request.data.get('max_duration')

        try:
            min_duration = int(min_duration) if min_duration else None
            max_duration = int(max_duration) if max_duration else None
        except ValueError:
            return Response({'error': 'Duration must be a number'}, status=status.HTTP_400_BAD_REQUEST)

        report = {}
        db_cdr = get_db(alias='cdr_db')
        db_source = db_cdr.client['CDR_SOURCE']
        sdr_db = db_cdr.client['subscriber_data']

        # 2. Load Static JSON Data
        try:
            landline_json = fetch_landline_json()
            sorted_ll_codes = sorted(landline_json.keys(), key=len, reverse=True)
            isd_json = fetch_isd_json()
            sorted_isd_codes = sorted(isd_json.keys(), key=len, reverse=True)
        except:
            landline_json, sorted_ll_codes, isd_json, sorted_isd_codes = {}, [], {}, []

        # 3. Build Match Query
        cdr_match_query = {"seq_id": {"$in": seq_ids}}
        if from_date or to_date:
            cdr_match_query["SDateTime"] = {k: v for k, v in [("$gte", from_date), ("$lte", to_date)] if v}
        if min_duration or max_duration:
            cdr_match_query["Duration"] = {k: v for k, v in [("$gte", min_duration), ("$lte", max_duration)] if v}

        # =========================================================
        # PART A: AGGREGATED REPORTS (1-9)
        # =========================================================

        # --- 1. CDR Number Info ---
        cdr_info_list = []
        nexus_doc = db_cdr.DataNexus.find_one({"_id": primary_seq_id})
        nexus_target_number = nexus_doc.get('CDRNo_Or_ImeiNo', '') if nexus_doc else ''

        if nexus_doc:
            case_name = ""
            user_name = ""
            circle_info = ""
            operator_info = ""

            if 'CrimeID' in nexus_doc:
                crime_doc = db_cdr.CrimeRegistry.find_one({"_id": nexus_doc['CrimeID']})
                if crime_doc: case_name = crime_doc.get('Crime', '')

            if 'UserAccessID' in nexus_doc and db_source is not None:
                mapping_doc = db_cdr.UserAccessMapping.find_one({"_id": nexus_doc['UserAccessID']})
                if mapping_doc and 'UserID' in mapping_doc:
                    login_doc = db_source.Logins.find_one({"_id": mapping_doc['UserID']})
                    if login_doc: user_name = login_doc.get('name', '')

            imsi_code = nexus_doc.get('ImsiCode', '')
            if imsi_code and db_source is not None:
                mcc_doc = db_source.MccMnc.find_one({"mccmnc_temp": imsi_code})
                if mcc_doc:
                    circle_info = mcc_doc.get('circle', '')
                    operator_info = mcc_doc.get('operator', '')

            start_period = nexus_doc.get('FromDate')
            end_period = nexus_doc.get('ToDate')
            import_time = nexus_doc.get('InsertedAt')

            cdr_info_list = [
                {"Information": "Case Name", "Value": case_name},
                {"Information": "File Name", "Value": nexus_doc.get('FileName', '')},
                {"Information": "State / Region", "Value": circle_info},
                {"Information": "Service Provider", "Value": operator_info},
                {"Information": "Date & Time Of Import",
                 "Value": import_time.strftime('%Y-%m-%d %H:%M:%S') if import_time else ""},
                {"Information": "User Name", "Value": user_name},
                {"Information": "Start Period",
                 "Value": start_period.strftime('%d/%m/%Y %H:%M:%S') if start_period else ""},
                {"Information": "End Period", "Value": end_period.strftime('%d/%m/%Y %H:%M:%S') if end_period else ""},
                {"Information": "Target Number", "Value": nexus_target_number},
                {"Information": "Name", "Value": ""},
                {"Information": "Address", "Value": ""},
                {"Information": "DOA", "Value": ""}
            ]
        report['1.CDR Number Info'] = cdr_info_list

        # --- 2. Communication Summary ---
        summary_pipeline = [
            {"$match": cdr_match_query},
            {"$group": {"_id": "$Call_Type", "Number Of Calls": {"$sum": 1}, "Total Duration": {"$sum": "$Duration"}}},
            {"$project": {"_id": 0, "Call Type": "$_id", "Number Of Calls": 1, "Total Duration": 1}},
            {"$sort": {"Number Of Calls": -1}}
        ]
        report['2.Communication Summary'] = list(db_cdr.CallDetailRecords.aggregate(summary_pipeline))

        # --- 3. Other Party Frequency Wise ---
        frequency_pipeline = [
            {"$match": cdr_match_query},
            {"$group": {
                "_id": "$B_Party", "Frequency": {"$sum": 1},
                "minDate": {"$min": "$SDateTime"}, "maxDate": {"$max": "$SDateTime"}
            }},
            {"$project": {
                "_id": 0, "Other Party": "$_id", "Frequency": 1,
                "From Date": {"$dateToString": {"format": "%d-%m-%Y %H:%M:%S", "date": "$minDate"}},
                "To Date": {"$dateToString": {"format": "%d-%m-%Y %H:%M:%S", "date": "$maxDate"}},
                "Remarks": {"$literal": ""}, "Name": {"$literal": ""}
            }},
            {"$sort": {"Frequency": -1}}
        ]
        report['3.Other party frequency wise'] = list(db_cdr.CallDetailRecords.aggregate(frequency_pipeline))

        # --- 4. Other Party Duration Wise ---
        duration_pipeline = [
            {"$match": cdr_match_query},
            {"$group": {
                "_id": "$B_Party", "Frequency": {"$sum": 1}, "Duration": {"$sum": "$Duration"},
                "minDate": {"$min": "$SDateTime"}, "maxDate": {"$max": "$SDateTime"}
            }},
            {"$project": {
                "_id": 0, "Other Party": "$_id", "Frequency": 1, "Duration": 1,
                "From Date": {"$dateToString": {"format": "%d-%m-%Y %H:%M:%S", "date": "$minDate"}},
                "To Date": {"$dateToString": {"format": "%d-%m-%Y %H:%M:%S", "date": "$maxDate"}},
                "Remarks": {"$literal": ""}, "Name": {"$literal": ""}
            }},
            {"$sort": {"Duration": -1}}
        ]
        report['4.Other party duration wise'] = list(db_cdr.CallDetailRecords.aggregate(duration_pipeline))

        # --- 5 & 6. Day & Night Halt ---
        day_halt_pipeline = [
            {"$match": cdr_match_query},
            {"$match": {
                "$expr": {"$and": [{"$gte": [{"$hour": "$SDateTime"}, 6]}, {"$lt": [{"$hour": "$SDateTime"}, 18]}]}}},
            {"$group": {"_id": "$First_CGI", "Day_Frq_Of_Call": {"$sum": 1}}},
            {"$sort": {"Day_Frq_Of_Call": -1}}, {"$limit": 5}
        ]
        top_day_results = list(db_cdr.CallDetailRecords.aggregate(day_halt_pipeline))

        night_halt_pipeline = [
            {"$match": cdr_match_query},
            {"$match": {
                "$expr": {"$or": [{"$gte": [{"$hour": "$SDateTime"}, 18]}, {"$lt": [{"$hour": "$SDateTime"}, 6]}]}}},
            {"$group": {"_id": "$First_CGI", "Night_Frq_Of_Call": {"$sum": 1}}},
            {"$sort": {"Night_Frq_Of_Call": -1}}, {"$limit": 5}
        ]
        top_night_results = list(db_cdr.CallDetailRecords.aggregate(night_halt_pipeline))

        all_cgi_ids = list(
            set([i['_id'] for i in top_day_results if i['_id']] + [i['_id'] for i in top_night_results if i['_id']]))
        halt_cell_map = {}
        if all_cgi_ids:
            try:
                ssd_db = db_cdr.client['ssd_logs']
                cell_cursor = ssd_db.cellid_info.find({"_id": {"$in": all_cgi_ids}})
                for doc in cell_cursor: halt_cell_map[doc['_id']] = doc
            except:
                pass

        def format_halt(res_list, freq_key):
            out = []
            for item in res_list:
                det = halt_cell_map.get(item['_id'], {})
                out.append({
                    "First Cell ID": item['_id'],
                    "First Cell ID Address": det.get("ADDRESS", "Unknown"),
                    freq_key: item[freq_key],
                    "Latitude": det.get("LATITUDE", ""), "Longitude": det.get("LONGITUDE", ""),
                    "Azimuth": det.get("AZIMUTH", "")
                })
            return out

        report['5.Top 5 Day Halt'] = format_halt(top_day_results, "Day_Frq_Of_Call")
        report['6.Top 5 Night Halt'] = format_halt(top_night_results, "Night_Frq_Of_Call")

        # --- 7. IMEI Usage Summary ---
        imei_pipeline = [
            {"$match": cdr_match_query},
            {"$group": {"_id": "$IMEI", "Frequency": {"$sum": 1}, "minDate": {"$min": "$SDateTime"},
                        "maxDate": {"$max": "$SDateTime"}}},
            {"$sort": {"Frequency": -1}}, {"$limit": 5},
            {"$project": {
                "_id": 0, "IMEI": "$_id", "TAC": {"$substrCP": ["$_id", 0, 8]},
                "Frequency": 1,
                "From Date": {"$dateToString": {"format": "%d-%m-%Y %H:%M:%S", "date": "$minDate"}},
                "To Date": {"$dateToString": {"format": "%d-%m-%Y %H:%M:%S", "date": "$maxDate"}}
            }}
        ]
        imei_results_top5 = list(db_cdr.CallDetailRecords.aggregate(imei_pipeline))

        tac_ids_top5 = [int(i['TAC']) for i in imei_results_top5 if i.get('TAC') and i['TAC'].isdigit()]
        imei_info_map_top5 = {}
        if tac_ids_top5 and db_source is not None:
            try:
                for d in db_source.ImeiMapping.find({"_id": {"$in": tac_ids_top5}}): imei_info_map_top5[
                    str(d['_id'])] = d
            except:
                pass

        final_imei_top5 = []
        for i in imei_results_top5:
            det = imei_info_map_top5.get(i.get('TAC'), {})
            final_imei_top5.append({
                **i, "Make & Model": f"{det.get('manufacturer', '')} {det.get('brand', '')}".strip()
            })
        report['7.Top 5 IMEI Usage Summary'] = final_imei_top5

        # --- 8 & 9. ISD Calls (Freq & Duration) ---
        isd_base_pipeline = [
            {"$match": cdr_match_query},
            {"$match": {"$expr": {"$gt": [{"$strLenCP": "$B_Party"}, 10]}}},
            {"$group": {
                "_id": "$B_Party", "Frequency": {"$sum": 1}, "TotalDuration": {"$sum": "$Duration"},
                "minDate": {"$min": "$SDateTime"}, "maxDate": {"$max": "$SDateTime"}
            }}
        ]

        isd_results = list(db_cdr.CallDetailRecords.aggregate(isd_base_pipeline))

        isd_freq_list = []
        isd_dur_list = []

        for item in isd_results:
            bp_num = item['_id']
            if not bp_num or bp_num.startswith('1800') or bp_num.startswith('1860'): continue

            country = "Unknown"
            matched = False
            for code in sorted_isd_codes:
                if bp_num.startswith(code):
                    country = isd_json[code].get('name', 'Unknown')
                    matched = True
                    break

            if matched:
                row = {
                    "Other Party": bp_num, "Country": country,
                    "Frequency": item['Frequency'], "Duration": item['TotalDuration'],
                    "From Date": item['minDate'].strftime('%d-%m-%Y %H:%M:%S') if item['minDate'] else "",
                    "To Date": item['maxDate'].strftime('%d-%m-%Y %H:%M:%S') if item['maxDate'] else "",
                    "Remarks": ""
                }
                isd_freq_list.append(row)
                isd_dur_list.append(row)

        report['8. ISD Calls by Frequency'] = sorted(isd_freq_list, key=lambda x: x['Frequency'], reverse=True)
        report['9. ISD Calls by Duration'] = sorted(isd_dur_list, key=lambda x: x['Duration'], reverse=True)

        # --- PART B: DATA PREP FOR DETAILED REPORTS ---
        projection = {
            "A_Party": 1, "B_Party": 1, "SDateTime": 1, "Duration": 1,
            "Call_Type": 1, "First_CGI": 1, "Last_CGI": 1, "IMEI": 1,
            "IMSI": 1, "Roaming": 1, "LRN": 1, "CallForward": 1
        }
        raw_cdrs = list(db_cdr.CallDetailRecords.find(cdr_match_query, projection).sort("SDateTime", 1))

        # Bulk mappings for performance
        unique_cgis = {d.get('First_CGI') for d in raw_cdrs if d.get('First_CGI')} | {d.get('Last_CGI') for d in
                                                                                      raw_cdrs if d.get('Last_CGI')}
        cell_info_map = {d['_id']: d for d in
                         db_cdr.client['ssd_logs'].cellid_info.find({"_id": {"$in": list(unique_cgis)}})}

        # =========================================================
        # PART B: BULK DATA PREP (For Report 10 - 21)
        # =========================================================

        # Sort by Date is crucial for Report 11, 17, 19, 20, 21
        raw_cdrs = list(db_cdr.CallDetailRecords.find(cdr_match_query, projection).sort("SDateTime", 1))

        unique_cgis = set()
        unique_mobile_prefixes = set()
        unique_sms_headers = set()
        unique_lrn_codes = set()
        unique_tacs = set()
        unique_b_parties = set()
        unique_roam_codes = set()

        for doc in raw_cdrs:
            cgi = doc.get('First_CGI')
            if cgi:
                unique_cgis.add(cgi)
                r_code = extract_roam_code(cgi)
                if r_code:
                    unique_roam_codes.add(r_code)
                    doc['_roam_code'] = r_code

            if doc.get('Last_CGI'): unique_cgis.add(doc['Last_CGI'])

            imei = doc.get('IMEI')
            if imei and len(str(imei)) >= 8:
                try:
                    unique_tacs.add(int(str(imei)[:8]))
                except:
                    pass

            imsi = doc.get('IMSI')
            if imsi:
                h_code = extract_roam_code(imsi)
                if h_code: unique_roam_codes.add(h_code)

            bp_num = doc.get('B_Party', '')
            if not bp_num: continue
            unique_b_parties.add(bp_num)

            if len(bp_num) == 10 and bp_num.isdigit() and bp_num[0] in '6789':
                lrn = doc.get('LRN')
                if lrn and len(str(lrn)) == 4:
                    try:
                        unique_lrn_codes.add(int(lrn))
                    except:
                        pass
                else:
                    try:
                        unique_mobile_prefixes.add(int(bp_num[:4]))
                    except:
                        pass
            elif '-' in bp_num:
                parts = bp_num.split('-')
                if len(parts) > 1:
                    header = parts[1].strip()
                    if header: unique_sms_headers.add(header)
                else:
                    unique_sms_headers.add(bp_num.strip())

        # Bulk Lookups
        cell_info_map = {}
        if unique_cgis:
            try:
                for d in db_cdr.client['ssd_logs'].cellid_info.find({"_id": {"$in": list(unique_cgis)}}): cell_info_map[
                    d['_id']] = d
            except:
                pass

        mobile_code_map = {}
        if unique_mobile_prefixes and db_source is not None:
            try:
                for d in db_source.MobileCodes.find({"Code": {"$in": list(unique_mobile_prefixes)}}): mobile_code_map[
                    d['Code']] = d
            except:
                pass

        lrn_code_map = {}
        if unique_lrn_codes and db_source is not None:
            try:
                for d in db_source.LRNCodes.find({"_id": {"$in": list(unique_lrn_codes)}}): lrn_code_map[d['_id']] = d
            except:
                pass

        sms_header_map = {}
        if unique_sms_headers and db_source is not None:
            try:
                for d in db_source.SMSHeaders.find({"_id": {"$in": list(unique_sms_headers)}}): sms_header_map[
                    d['_id']] = d
            except:
                pass

        imei_map = {}
        if unique_tacs and db_source is not None:
            try:
                for d in db_source.ImeiMapping.find({"_id": {"$in": list(unique_tacs)}}): imei_map[d['_id']] = d
            except:
                pass

        watchlist_matches = []
        sdr_matches =[]
        sdr_col = sdr_db.subscribers if sdr_db is not None else db_cdr.subscribers
        if unique_b_parties:
            try:
                wl_coll = db_source.WatchList if db_source is not None else db_cdr.WatchList


                for d in wl_coll.find({"_id": {"$in": list(unique_b_parties)}}): watchlist_matches.append(d)
            except:
                pass
        print(watchlist_matches)

        mccmnc_map = {}
        if unique_roam_codes and db_source is not None:
            try:
                for d in db_source.MccMnc.find({"mccmnc_temp": {"$in": list(unique_roam_codes)}}):
                    mccmnc_map[d['mccmnc_temp']] = d
            except:
                pass
        # for record  in report['3.Other party frequency wise']:

        # ---------------------------------------------------------
        # 10. Other Parties in Watch List
        # ---------------------------------------------------------
        wl_report = []
        for item in report['3.Other party frequency wise']:
            for rec in watchlist_matches:

                if item['Other Party'] ==  rec['_id']:
                    item['Name'] = rec['Name']
                    wl_report.append({**item,**{'Name':rec['Name']}})
                else:

                    for d in sdr_col.find({"_id": {"$in": list(unique_b_parties)}}):
                        if item['Other Party'] ==  d['_id']:
                            item['Name'] = d['Name']

                    # if len(wl_report) >= 5: break
        report['10. Other Parties in Watch List'] = wl_report

        # ---------------------------------------------------------
        # 11. Formatted CDR
        # ---------------------------------------------------------
        formatted_cdr_list = []
        home_mccmnc = None
        for doc in raw_cdrs:
            if doc.get('IMSI'):
                home_mccmnc = extract_roam_code(doc['IMSI'])
                if home_mccmnc: break

        for doc in raw_cdrs:
            bp_num = doc.get("B_Party", "")
            lrn_code = doc.get("LRN", "")

            phone_type, service_provider, state_region = classify_number(
                bp_num, lrn_code,
                mobile_code_map, lrn_code_map, sms_header_map,
                landline_json, sorted_ll_codes, isd_json, sorted_isd_codes
            )

            imei_val = doc.get("IMEI", "")
            imei_mfr = ""
            device_type = ""
            if imei_val and len(str(imei_val)) >= 8:
                try:
                    det = imei_map.get(int(str(imei_val)[:8]))
                    if det:
                        imei_mfr = det.get('manufacturer', '')
                        device_type = det.get('devicetype', '')
                except:
                    pass

            roaming_status = ""
            visit_code = doc.get('_roam_code')
            if visit_code:
                circle_info = mccmnc_map.get(visit_code, {})
                roaming_status = circle_info.get('circle', '')

            s_date_time = doc.get("SDateTime")
            start_cell = doc.get("First_CGI", "")
            end_cell = doc.get("Last_CGI", "")

            formatted_cdr_list.append({
                "Target Number": doc.get("A_Party") or nexus_target_number,
                "Other Party": bp_num,
                "Date of Call": s_date_time.strftime("%d-%m-%Y") if s_date_time else "",
                "Time of Call": s_date_time.strftime("%H:%M:%S") if s_date_time else "",
                "Day of Week": s_date_time.strftime("%a") if s_date_time else "",
                "Duration (in Seconds)": doc.get("Duration", 0),
                "Call Type/Call Direction": doc.get("Call_Type", ""),
                "Starting Cell Tower ID": start_cell,
                "Ending Cell Tower ID": end_cell,
                "IMEI": imei_val,
                "IMEI Manufacturer": imei_mfr,
                "Device Type": device_type,
                "IMSI": doc.get("IMSI", ""),
                "Phone Type": phone_type,
                "Other Party Service Provider": service_provider,
                "Other Party State/Region": state_region,
                "Starting Cell Tower ID Address": cell_info_map.get(start_cell, {}).get("ADDRESS", ""),
                "Ending Cell Tower ID Address": cell_info_map.get(end_cell, {}).get("ADDRESS", ""),
                "Latitude": cell_info_map.get(start_cell, {}).get("LATITUDE", ""),
                "Longitude": cell_info_map.get(start_cell, {}).get("LONGITUDE", ""),
                "Azimuth": cell_info_map.get(start_cell, {}).get("AZIMUTH", ""),
                "Roaming": roaming_status,
                "LRN": doc.get("LRN", ""),
                "CallForward": doc.get("CallForward", ""),
                "Target Name": "", "Target Address": ""
            })
        report['11. Formatted CDR'] = formatted_cdr_list

        # ---------------------------------------------------------
        # 12. Other Party FRQ
        # ---------------------------------------------------------
        detailed_freq_pipeline = [
            {"$match": cdr_match_query},
            {"$group": {
                "_id": "$B_Party",
                "Total Duration": {"$sum": "$Duration"},
                "Call Frq": {"$sum": 1},
                "First Call Date": {"$min": "$SDateTime"},
                "Last Call Date": {"$max": "$SDateTime"},
                "Call Out": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_OUT"]}, 1, 0]}},
                "Call In": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_IN"]}, 1, 0]}},
                "Sms Out": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_OUT"]}, 1, 0]}},
                "Sms In": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_IN"]}, 1, 0]}},
                "Sample_LRN": {"$max": "$LRN"}
            }},
            {"$sort": {"Call Frq": -1}}
        ]
        raw_freq_results = list(db_cdr.CallDetailRecords.aggregate(detailed_freq_pipeline))

        detailed_freq_report = []
        for item in raw_freq_results:
            bp_num = item['_id'] or ""
            lrn_code = item.get("Sample_LRN")

            phone_type, service_provider, state_region = classify_number(
                bp_num, lrn_code,
                mobile_code_map, lrn_code_map, sms_header_map,
                landline_json, sorted_ll_codes, isd_json, sorted_isd_codes
            )

            total_dur = item.get("Total Duration", 0)
            call_frq = item.get("Call Frq", 1)
            min_d = item.get("First Call Date")
            max_d = item.get("Last Call Date")

            detailed_freq_report.append({
                "Other Party": bp_num,
                "Other Party DOA": "",
                "Other Party Service Provider": service_provider,
                "Other Party State/Region": state_region,
                "Number Type": phone_type,
                "Total Duration": total_dur,
                "Avg.Duration": round(total_dur / call_frq) if call_frq > 0 else 0,
                "Call Frq.": call_frq,
                "First Call Date": min_d.strftime("%d-%m-%Y %H:%M:%S") if min_d else "",
                "Last Call Date": max_d.strftime("%d-%m-%Y %H:%M:%S") if max_d else "",
                "WatchList Match": "YES" if bp_num in watchlist_matches else "NO",
                "Call Out": item.get("Call Out", 0),
                "Call In": item.get("Call In", 0),
                "Sms Out": item.get("Sms Out", 0),
                "Sms In": item.get("Sms In", 0),
                "Roaming": ""
            })
        report['12. Other Party FRQ'] = detailed_freq_report

        # ---------------------------------------------------------
        # 13. International Call FRQ
        # ---------------------------------------------------------
        isd_frq_report = []
        for item in detailed_freq_report:
            if item['Number Type'].startswith("ISD"):
                row = item.copy()
                row["Country"] = item['Other Party State/Region']
                row["Other Party Name"] = ""
                row["Other Party Address"] = ""
                isd_frq_report.append(row)
        report['13. International Call FRQ'] = isd_frq_report

        # ---------------------------------------------------------
        # 14. Other Circle Call FRQ
        # ---------------------------------------------------------
        other_circle_report = []
        for item in detailed_freq_report:
            if item['Other Party State/Region']:
                other_circle_report.append(item)
        report['14. Other Circle Call FRQ'] = other_circle_report

        # ---------------------------------------------------------
        # 15. Watchlist Number
        # ---------------------------------------------------------
        watchlist_full_report = []
        for item in detailed_freq_report:
            if item['WatchList Match'] == "YES":
                watchlist_full_report.append(item)
        report['15. Watchlist Number'] = watchlist_full_report

        # ---------------------------------------------------------
        # 16. IMEI FRQ
        # ---------------------------------------------------------
        imei_full_pipeline = [
            {"$match": cdr_match_query},
            {"$group": {"_id": "$IMEI", "Frequency": {"$sum": 1}, "minDate": {"$min": "$SDateTime"},
                        "maxDate": {"$max": "$SDateTime"}}},
            {"$sort": {"Frequency": -1}},
            {"$project": {
                "_id": 0, "IMEI": "$_id", "TAC": {"$substrCP": ["$_id", 0, 8]},
                "Frequency": 1,
                "From Date": {"$dateToString": {"format": "%d-%m-%Y %H:%M:%S", "date": "$minDate"}},
                "To Date": {"$dateToString": {"format": "%d-%m-%Y %H:%M:%S", "date": "$maxDate"}}
            }}
        ]
        imei_full_results = list(db_cdr.CallDetailRecords.aggregate(imei_full_pipeline))

        imei_frq_report = []
        for i in imei_full_results:
            wl_match = "YES" if i.get('IMEI') in watchlist_matches else "NO"

            tac_str = i.get('TAC')
            make_model = tac_str
            if tac_str and tac_str.isdigit():
                try:
                    details = imei_map.get(int(tac_str))
                    if details:
                        mfr = details.get('manufacturer', '')
                        brand = details.get('brand', '')
                        make_model = f"{mfr} {brand}".strip() or details.get('devicetype', tac_str)
                except:
                    pass

            row = {
                "IMEI": i.get('IMEI'),
                "TAC": make_model,
                "Call Frq.": i.get('Frequency'),
                "From Date": i.get('From Date'),
                "To Date": i.get('To Date'),
                "WatchList Match": wl_match
            }
            imei_frq_report.append(row)
        report['16. IMEI FRQ'] = imei_frq_report

        # ---------------------------------------------------------
        # 17. IMSI Period
        # ---------------------------------------------------------
        imsi_data = {}
        for doc in raw_cdrs:
            imsi = doc.get('IMSI')
            if not imsi: continue

            if imsi not in imsi_data:
                imsi_data[imsi] = {
                    "CdrNo": doc.get("A_Party") or nexus_target_number,
                    "IMSI": imsi,
                    "min_date": doc["SDateTime"],
                    "max_date": doc["SDateTime"],
                    "total_calls": 0, "total_duration": 0, "days_set": set(),
                    "first_loc": doc.get("First_CGI"), "last_loc": doc.get("Last_CGI"),
                    "out_calls": 0, "in_calls": 0, "out_sms": 0, "in_sms": 0, "other_calls": 0, "wifi_calls": 0
                }
            entry = imsi_data[imsi]

            if doc["SDateTime"] < entry["min_date"]:
                entry["min_date"] = doc["SDateTime"]
                entry["first_loc"] = doc.get("First_CGI")
            if doc["SDateTime"] > entry["max_date"]:
                entry["max_date"] = doc["SDateTime"]
                entry["last_loc"] = doc.get("Last_CGI")

            entry["days_set"].add(doc["SDateTime"].date())
            entry["total_duration"] += doc.get("Duration", 0)
            entry["total_calls"] += 1

            ct = (doc.get("Call_Type") or "").upper()
            if ct == "CALL_OUT":
                entry["out_calls"] += 1
            elif ct == "CALL_IN":
                entry["in_calls"] += 1
            elif ct == "SMS_OUT":
                entry["out_sms"] += 1
            elif ct == "SMS_IN":
                entry["in_sms"] += 1
            elif "WIFI" in ct:
                entry["wifi_calls"] += 1
            else:
                entry["other_calls"] += 1

        imsi_report = []
        for imsi, data in imsi_data.items():
            f_addr = cell_info_map.get(data["first_loc"], {}).get("ADDRESS", data["first_loc"])
            l_addr = cell_info_map.get(data["last_loc"], {}).get("ADDRESS", data["last_loc"])

            row = {
                "CdrNo": data["CdrNo"], "IMSI": data["IMSI"],
                "Period": f"{data['min_date'].strftime('%d/%m/%Y %H:%M')} --- {data['max_date'].strftime('%d/%m/%Y %H:%M')}",
                "Total Calls": data["total_calls"], "Days": len(data["days_set"]),
                "First Location": f"{data['first_loc']} -- {f_addr}",
                "Last Location": f"{data['last_loc']} -- {l_addr}",
                "Out Calls": data["out_calls"], "In Calls": data["in_calls"],
                "Out Sms": data["out_sms"], "In Sms": data["in_sms"],
                "Other Calls": data["other_calls"], "Wifi Calls": data["wifi_calls"],
                "Total Duration": data["total_duration"]
            }
            imsi_report.append(row)
        report['17. IMSI Period'] = imsi_report

        # ---------------------------------------------------------
        # 18. Location FRQ
        # ---------------------------------------------------------
        loc_pipeline = [
            {"$match": cdr_match_query},
            {"$group": {
                "_id": "$First_CGI",
                "Call Frq": {"$sum": 1},
                "minDate": {"$min": "$SDateTime"},
                "maxDate": {"$max": "$SDateTime"},
                "Day Call Frq": {"$sum": {"$cond": [
                    {"$and": [{"$gte": [{"$hour": "$SDateTime"}, 6]}, {"$lt": [{"$hour": "$SDateTime"}, 18]}]}, 1, 0]}},
                "Night Call Frq": {"$sum": {
                    "$cond": [{"$or": [{"$gte": [{"$hour": "$SDateTime"}, 18]}, {"$lt": [{"$hour": "$SDateTime"}, 6]}]},
                              1, 0]}}
            }},
            {"$sort": {"Call Frq": -1}}
        ]
        loc_results = list(db_cdr.CallDetailRecords.aggregate(loc_pipeline))

        loc_report = []
        for item in loc_results:
            cgi = item['_id']
            cell_det = cell_info_map.get(cgi, {})
            loc_report.append({
                "First Cell ID": cgi,
                "First Cell ID Address": cell_det.get("ADDRESS", ""),
                "Latitude": cell_det.get("LATITUDE", ""),
                "Longitude": cell_det.get("LONGITUDE", ""),
                "Azimuth": cell_det.get("AZIMUTH", ""),
                "First Call Date": item['minDate'].strftime('%d-%m-%Y %H:%M:%S') if item['minDate'] else "",
                "Last Call Date": item['maxDate'].strftime('%d-%m-%Y %H:%M:%S') if item['maxDate'] else "",
                "Call Frq.": item['Call Frq'],
                "Day Call Frq.": item['Day Call Frq'],
                "Night Call Frq.": item['Night Call Frq']
            })
        report['18. Location FRQ'] = loc_report

        # ---------------------------------------------------------
        # 19. Max Stay
        # ---------------------------------------------------------
        max_stay_pipeline = [
            {"$match": cdr_match_query},
            {"$group": {
                "_id": "$First_CGI",
                "Total Calls": {"$sum": 1},
                "minDate": {"$min": "$SDateTime"},
                "maxDate": {"$max": "$SDateTime"},
                "UniqueDays": {"$addToSet": {"$dateToString": {"format": "%Y-%m-%d", "date": "$SDateTime"}}}
            }},
            {"$sort": {"Total Calls": -1}}
        ]
        stay_results = list(db_cdr.CallDetailRecords.aggregate(max_stay_pipeline))

        max_stay_report = []
        for item in stay_results:
            cgi = item['_id']
            cell_det = cell_info_map.get(cgi, {})

            roaming_status = ""
            visit_code = extract_roam_code(cgi)

            if visit_code:
                info = mccmnc_map.get(visit_code, {})
                roaming_status = info.get('circle', '')

            min_d = item['minDate']
            max_d = item['maxDate']

            max_stay_report.append({
                "CdrNo": nexus_target_number,
                "Cell ID": cgi,
                "Total Calls": item['Total Calls'],
                "Days": len(item['UniqueDays']),
                "Tower Address": cell_det.get("ADDRESS", ""),
                "Latitude": cell_det.get("LATITUDE", ""),
                "Longitude": cell_det.get("LONGITUDE", ""),
                "Azimuth": cell_det.get("AZIMUTH", ""),
                "Roaming": roaming_status,
                "First Call Date": min_d.strftime('%d-%m-%Y') if min_d else "",
                "First Call Time": min_d.strftime('%H:%M:%S') if min_d else "",
                "Last Call Date": max_d.strftime('%d-%m-%Y') if max_d else "",
                "Last Call Time": max_d.strftime('%H:%M:%S') if max_d else ""
            })
        report['19. Max Stay'] = max_stay_report

        # ---------------------------------------------------------
        # 20. Roaming Period
        # ---------------------------------------------------------
        roaming_periods = []
        if home_mccmnc:
            current_period = None

            for doc in raw_cdrs:
                visit_code = doc.get('_roam_code')
                if not visit_code: continue

                circle_name = mccmnc_map.get(visit_code, {}).get('circle', 'Unknown')

                if current_period and current_period['Roaming'] == circle_name:
                    cp = current_period
                    cp['max_date'] = doc['SDateTime']
                    cp['last_loc'] = doc.get('First_CGI', '')
                    cp['total_calls'] += 1
                    cp['total_duration'] += doc.get('Duration', 0)
                    cp['days_set'].add(doc['SDateTime'].date())

                    ct = (doc.get("Call_Type") or "").upper()
                    if ct == "CALL_OUT":
                        cp["out_calls"] += 1
                    elif ct == "CALL_IN":
                        cp["in_calls"] += 1
                    elif ct == "SMS_OUT":
                        cp["out_sms"] += 1
                    elif ct == "SMS_IN":
                        cp["in_sms"] += 1
                    elif "WIFI" in ct:
                        cp["wifi_calls"] += 1
                    else:
                        cp["other_calls"] += 1
                else:
                    if current_period:
                        roaming_periods.append(current_period)

                    current_period = {
                        "CdrNo": doc.get("A_Party") or nexus_target_number,
                        "Roaming": circle_name,
                        "min_date": doc['SDateTime'],
                        "max_date": doc['SDateTime'],
                        "first_loc": doc.get('First_CGI', ''),
                        "last_loc": doc.get('First_CGI', ''),
                        "total_calls": 1,
                        "total_duration": doc.get('Duration', 0),
                        "days_set": {doc['SDateTime'].date()},
                        "out_calls": 0, "in_calls": 0, "out_sms": 0, "in_sms": 0, "other_calls": 0, "wifi_calls": 0
                    }
                    ct = (doc.get("Call_Type") or "").upper()
                    if ct == "CALL_OUT":
                        current_period["out_calls"] = 1
                    elif ct == "CALL_IN":
                        current_period["in_calls"] = 1
                    elif ct == "SMS_OUT":
                        current_period["out_sms"] = 1
                    elif ct == "SMS_IN":
                        current_period["in_sms"] = 1
                    elif "WIFI" in ct:
                        current_period["wifi_calls"] = 1
                    else:
                        current_period["other_calls"] = 1

            if current_period:
                roaming_periods.append(current_period)

        final_roaming_report = []
        for p in roaming_periods:
            f_addr = cell_info_map.get(p["first_loc"], {}).get("ADDRESS", p["first_loc"])
            l_addr = cell_info_map.get(p["last_loc"], {}).get("ADDRESS", p["last_loc"])

            final_roaming_report.append({
                "CdrNo": p['CdrNo'], "Roaming": p['Roaming'],
                "Period": f"{p['min_date'].strftime('%d/%m/%Y %H:%M')} --- {p['max_date'].strftime('%d/%m/%Y %H:%M')}",
                "Total Calls": p['total_calls'], "Days": len(p['days_set']),
                "First Location": f"{p['first_loc']} -- {f_addr}",
                "Last Location": f"{p['last_loc']} -- {l_addr}",
                "Out Calls": p['out_calls'], "In Calls": p['in_calls'],
                "Out Sms": p['out_sms'], "In Sms": p['in_sms'],
                "Other Calls": p['other_calls'], "Wifi Calls": p['wifi_calls'],
                "Total Duration": p['total_duration']
            })
        report['20. Roaming Period'] = final_roaming_report

        # ---------------------------------------------------------
        # 21. OFF/Un-Used Period (NEW)
        # ---------------------------------------------------------
        # Identifies gaps > 6 hours (21600 seconds) between consecutive CDRs
        off_period_report = []

        # Ensure we have enough records to compare
        if len(raw_cdrs) > 1:
            for i in range(1, len(raw_cdrs)):
                prev = raw_cdrs[i - 1]
                curr = raw_cdrs[i]

                # Gap Start: End of previous call (Start + Duration)
                # Gap End: Start of current call
                prev_end = prev['SDateTime'] + timedelta(seconds=prev.get('Duration', 0))
                curr_start = curr['SDateTime']

                gap_seconds = (curr_start - prev_end).total_seconds()

                # Threshold: 6 Hours (21600 seconds)
                if gap_seconds > 21600:
                    days = int(gap_seconds // 86400)
                    hours = int((gap_seconds % 86400) // 3600)
                    minutes = int((gap_seconds % 3600) // 60)

                    duration_str = f"{days} D, {hours} H, {minutes} M"

                    # Prepare details
                    prev_loc = prev.get('First_CGI', '')
                    prev_addr = cell_info_map.get(prev_loc, {}).get('ADDRESS', prev_loc)

                    curr_loc = curr.get('First_CGI', '')
                    curr_addr = cell_info_map.get(curr_loc, {}).get('ADDRESS', curr_loc)

                    # Resolve Circles
                    c_off = ""
                    c_on = ""

                    v_off = prev.get('_roam_code')
                    if v_off: c_off = mccmnc_map.get(v_off, {}).get('circle', '')

                    v_on = curr.get('_roam_code')
                    if v_on: c_on = mccmnc_map.get(v_on, {}).get('circle', '')

                    off_period_report.append({
                        "CdrNo": nexus_target_number,
                        "Period": f"{prev_end.strftime('%d/%m/%Y %H:%M:%S')} -- {curr_start.strftime('%d/%m/%Y %H:%M:%S')}",
                        "Total Days": duration_str,
                        "IMEI(OFF)": prev.get('IMEI', ''),
                        "IMEI(ON)": curr.get('IMEI', ''),
                        "IMSI(OFF)": prev.get('IMSI', ''),
                        "IMSI(ON)": curr.get('IMSI', ''),
                        "Location(OFF)": f"{prev_loc} -- {prev_addr}",
                        "Location(ON)": f"{curr_loc} -- {curr_addr}",
                        "Circle(OFF)": c_off,
                        "Circle(ON)": c_on
                    })

        report['21. OFF/Un-Used Period'] = off_period_report

        # =========================================================
        # 22. CONFERENCE CALL REPORT (GROUPED)
        # =========================================================
        conference_report = []
        # Filter for Voice Calls only
        voice_cdrs = [c for c in raw_cdrs if "CALL" in (c.get('Call_Type') or "").upper()]

        if len(voice_cdrs) > 1:
            groups = []
            visited = set()

            for i in range(len(voice_cdrs)):
                if i in visited: continue

                current_call = voice_cdrs[i]
                start_i = current_call['SDateTime']
                end_i = start_i + timedelta(seconds=current_call.get('Duration', 0))

                current_group = [i]
                for j in range(i + 1, len(voice_cdrs)):
                    start_j = voice_cdrs[j]['SDateTime']
                    # Overlap Logic: Next call starts before current group window ends
                    if start_j < end_i:
                        current_group.append(j)
                        # Extend window if this participant stays longer
                        end_j = start_j + timedelta(seconds=voice_cdrs[j].get('Duration', 0))
                        if end_j > end_i: end_i = end_j
                    else:
                        break

                if len(current_group) > 1:
                    groups.append(current_group)
                    visited.update(current_group)

            for idx, group_indices in enumerate(groups, 1):
                group_records = []
                unique_participants = set()

                for idx_in_group in group_indices:
                    doc = voice_cdrs[idx_in_group]
                    bp_num = doc.get("B_Party", "")
                    unique_participants.add(bp_num)

                    s_dt = doc.get("SDateTime")
                    start_cgi = doc.get("First_CGI", "")

                    group_records.append({
                        "A Party": doc.get("A_Party") or nexus_target_number,
                        "B Party": bp_num,
                        "Date": s_dt.strftime("%d-%m-%Y") if s_dt else "",
                        "Time": s_dt.strftime("%H:%M:%S") if s_dt else "",
                        "Duration": doc.get("Duration", 0),
                        "Call Type": doc.get("Call_Type", ""),
                        "First Cell ID Address": f'{start_cgi} -- {cell_info_map.get(start_cgi, {}).get("ADDRESS", "")}',
                    })

                group_info = {
                    "Involved Count": len(unique_participants) + 1,
                    "Group Title": f"SNO: {idx} ({len(group_records)})"
                }

                # Flatten the group into the main conference_report list
                for i in range(len(group_records)):
                    if i == 0:
                        conference_report.append({**group_info, **group_records[i]})
                    else:
                        conference_report.append({
                            "Involved Count": "",
                            "Group Title": "",
                            **group_records[i]
                        })


        report['22. Conference Call Report'] = conference_report



        return Response(report, status=status.HTTP_200_OK)