import copy
from collections import defaultdict, Counter
from datetime import datetime
from django.utils.dateparse import parse_datetime
from mongoengine import InvalidQueryError, get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Nexus, CallDetailRecord, CellTower, MobileOperator, MccMnc, ImeiDetails, \
    CrimeInformation, UserAccess, LRNCode, SMSHeader
from .serializers import*
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from datetime import datetime, time
from .utilities import fetch_landline_json, fetch_isd_json


class CommonMethodMixin:
    def common_method(self, nexus_data):
        cdrdb = get_db('cdr_db')
        cdrcollection= cdrdb['CallDetailRecords']
        imsi_code_numbers = set()
        ap_code_numbers = set()
        tac_numbers = set()
        crime_ids = set()
        user_ids = set()

        for cdr in nexus_data:
            crime_ids.add(cdr["CrimeID"])
            user_ids.add(cdr["UserAccessID"])


            if cdr.get('ImsiCode'):
                if cdr['ImsiCode']:
                    imsi_code_numbers.add(cdr["ImsiCode"])

            if cdr['RecordType'] == "CDR":
                if cdr.get('Tac_Or_Mobile_Code'):
                    if cdr['Tac_Or_Mobile_Code']:
                        ap_code_numbers.add(cdr["Tac_Or_Mobile_Code"])
            else:
                if cdr.get('Tac_Or_Mobile_Code'):
                    if cdr['Tac_Or_Mobile_Code']:
                        tac_numbers.add(cdr["Tac_Or_Mobile_Code"])

        if len(ap_code_numbers) > 0:
            ap_codes = MobileOperator.objects.filter(id__in=ap_code_numbers)
            ap_codesdata = MobileOperatorSerializer(ap_codes, many=True).data
            lookupAp = {item["id"]: item for item in ap_codesdata}

        if len(imsi_code_numbers) > 0:
            imsi_codes = MccMnc.objects.filter(mccmnc_temp__in=imsi_code_numbers)
            imsi_codesdata = MccMncSerializer(imsi_codes, many=True).data
            lookupImsi = {item["mccmnc_temp"]: item for item in imsi_codesdata}

        if len(tac_numbers) > 0:
            tac_codes = ImeiDetails.objects.filter(id__in=tac_numbers)
            tac_codesdata = DeviceInfoSerializer(tac_codes, many=True).data
            lookupTac = {item["id"]: item for item in tac_codesdata}

        if len(crime_ids) > 0:
            crime_ids_t = CrimeInformation.objects.filter(id__in=crime_ids)
            crime_idsdata = CrimeInformationSerializer(crime_ids_t, many=True).data
            lookupCrimeID = {item["id"]: item for item in crime_idsdata}

        if len(user_ids) > 0:
            user_ids_t = UserAccess.objects.filter(id__in=user_ids)
            user_idsdata = UserAccessSerializer(user_ids_t, many=True).data
            lookupUserID = {item["id"]: item for item in user_idsdata}

        for cdr in nexus_data:

            cdr['Totalcount'] = cdrcollection.count_documents({
                'seq_id': cdr['id']})
            cdr['TotalSMS'] = cdrcollection.count_documents({
                'seq_id': cdr['id'],
                'Call_Type': {'$in': ['SMS_IN', 'SMS_OUT']}
            })

            cdr['IncommingCalls'] = cdrcollection.count_documents({
                'seq_id': cdr['id'],
                'Call_Type': {'$in': ['CALL_IN']}
            })

            cdr['OutGoingCalls'] = cdrcollection.count_documents({
                'seq_id': cdr['id'],
                'Call_Type': {'$in': ['CALL_OUT']}
            })

            if cdr['IncommingCalls'] and cdr['OutGoingCalls']:
                cdr['OtherCalls'] = cdr['Totalcount'] - (cdr['IncommingCalls'] + cdr['OutGoingCalls'])


            if len(lookupUserID) > 0:
                UserAccessID = cdr["UserAccessID"]
                if UserAccessID in lookupUserID:
                    cdr['UserID'] = lookupUserID[UserAccessID]['UserID']

            if len(lookupCrimeID) > 0:
                CrimeID = cdr["CrimeID"]
                if CrimeID in lookupCrimeID:
                    cdr['Crime'] = lookupCrimeID[CrimeID]['Crime']
                    cdr['AreaLocation'] = lookupCrimeID[CrimeID]['AreaLocation']

            if cdr.get('ImsiCode'):
                if cdr['ImsiCode']:
                    Imsi_Code = cdr["ImsiCode"]
                    if len(lookupImsi) > 0:
                        if Imsi_Code in lookupImsi:
                            circle = lookupImsi[Imsi_Code]['circle']
                            operator = lookupImsi[Imsi_Code]['operator']
                            # cdr['Provider'] = circle + '-' + operator
                            # cdr['Type'] = 'Mobile-IMSI'
                            # cdr['Circle'] = circle
                            # cdr['Operator'] = operator

                            cdr.update({'Provider': circle + '-' + operator, 'Type': 'Mobile-IMSI', 'Circle': circle, 'Operator': operator})

            if cdr['RecordType'] == "CDR":
                if not cdr.get('Provider'):
                    if cdr.get('Tac_Or_Mobile_Code'):
                        if cdr['Tac_Or_Mobile_Code']:
                            if len(lookupAp) > 0:
                                a_mobile_code = cdr["Tac_Or_Mobile_Code"]
                                if a_mobile_code in lookupAp:
                                    circle = lookupAp[a_mobile_code]['Circle']
                                    operator = lookupAp[a_mobile_code]['Operator']
                                    cdr.update({'Provider': circle + '-' + operator,
                                                             'Type': 'Mobile-Code', 'Circle': circle,
                                                             'Operator': operator})
            else:
                if cdr.get('Tac_Or_Mobile_Code'):
                    if cdr['Tac_Or_Mobile_Code']:
                        if len(lookupTac) > 0:
                            tac_code = cdr.get("Tac_Or_Mobile_Code")
                            if tac_code:
                                if tac_code in lookupTac:
                                    cdr.update(lookupTac[tac_code])


        return nexus_data

class NexusListView(CommonMethodMixin, APIView):
    @swagger_auto_schema(
        operation_description="Retrieve all Nexus records",
        responses={200: NexusSerializer(many=True)}
    )
    def get(self, request):
        try:
            nexus = Nexus.objects.all()
        except Nexus.DoesNotExist:
            return Response({"error": "Nexus records not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = NexusSerializer(nexus, many=True)
        nexus_data = serializer.data

        nexus_data = self.common_method(nexus_data)

        return Response(nexus_data)


class NexusDetailView(CommonMethodMixin, APIView):
    @swagger_auto_schema(
        operation_description="Retrieve a single Nexus record by ID",
        responses={200: NexusSerializer()}
    )
    def get(self, request, pk):
        print(f"Received Nexus ID: {pk}")
        try:
            nexus = Nexus.objects.get(id=pk)
        except Nexus.DoesNotExist:
            return Response({"error": "Nexus record not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = NexusSerializer(nexus)
        nexus_data = serializer.data

        nexus_data = self.common_method(nexus_data)

        return Response(nexus_data)


class CallDetailRecordListView(APIView):

    @swagger_auto_schema(
        operation_summary="Get all call detail records",
        responses={200: CallDetailRecordSerializer(many=True)}
    )
    def get(self, request):
        records = CallDetailRecord.objects.all()
        serializer = CallDetailRecordSerializer(records, many=True)
        return Response(serializer.data)


class CallDetailRecordDetailView(APIView):
    @swagger_auto_schema(request_body=CDRFilterSerializer)
    def post(self, request):

        try:
            pk = request.data.get('seq_id')
            filtervalue = request.data.get('filter')

            nexus_data = Nexus.objects.get(id=pk)
            nexus_serializer = NexusSerializer(nexus_data,many=False)
            crime_id = nexus_serializer.data.get('CrimeID')

            # Optional: cast to ObjectId if needed

            crime_info = CrimeInformation.objects.get(id=crime_id)


            crime_serializer = CrimeInformationSerializer(crime_info,many=False).data

            if filtervalue:
                from_date = parse_datetime(request.data.get('from_date'))
                to_date = parse_datetime(request.data.get('to_date'))
                min_duration = request.data.get('min_duration')
                max_duration = request.data.get('max_duration')\

                cdrs = CallDetailRecord.objects.filter(seq_id=pk)


                if from_date and to_date:
                    cdrs = cdrs.filter(SDateTime__gte=from_date, SDateTime__lte=to_date)

                if min_duration is not None and max_duration is not None:
                    cdrs = cdrs.filter(Duration__gte=min_duration, Duration__lte=max_duration)
            else:
                cdrs = CallDetailRecord.objects.filter(seq_id=pk)

            if not cdrs:
                return Response({'error': 'Record not found'}, status=404)

            cdrs = cdrs.order_by('SDateTime')

            serializer = CallDetailRecordSerializer(cdrs, many=True)

            cdr_info=serializer.data

            # region matching sets
            isd_json = None
            sorted_isd_codes = None

            landline_json = None
            sorted_ll_codes = None

            roam_code_numbers = set()
            imsi_code_numbers = set()
            ap_code_numbers = set()

            lrn_code_numbers = set()
            bp_code_numbers = set()
            sms_headers = set()
            tac_numbers = set()

            cell_ids = set()
            # endregion
            lookupTower = {}
            lookupLrn = {}  # ✅ FIX
            lookupBp = {}
            lookupSms = {}
            lookupAp = {}
            lookupImsi = {}
            lookupTac = {}
            lookupRoam = {}
            for cdr in cdr_info:

                # region adding cellids to set
                First_CGI = cdr.get("First_CGI")
                cell_ids.add(First_CGI)
                cell_ids.add(cdr["Last_CGI"])
                if First_CGI:
                    if len(First_CGI) >= 5:
                        mccmnc = ''
                        if len(First_CGI) > 5:
                            mccmnc = First_CGI[:6]
                        else:
                            mccmnc = First_CGI[:5]
                        if mccmnc.isdigit():
                            if len(mccmnc) == 6:
                                if int(mccmnc) < 405750 and (int(mccmnc) < 405025 or int(mccmnc) > 405047):
                                    mccmnc = mccmnc[:5]
                                roam_code_numbers.add(mccmnc)
                                cdr["RoamCode"] = mccmnc
                # endregion

                # region add a-party and b-party and imei and imsi numbers to set
                if cdr.get('IMEI_TAC'):
                    if cdr['IMEI_TAC']:
                        tac_numbers.add(cdr["IMEI_TAC"])

                if cdr.get('IMSI_CODE'):
                    if cdr['IMSI_CODE']:
                        imsi_code_numbers.add(cdr["IMSI_CODE"])
                    elif cdr.get('a_mobile_code'):
                        if cdr['a_mobile_code']:
                            ap_code_numbers.add(cdr["a_mobile_code"])

                bp_num = cdr["B_Party"]
                if bp_num.isdigit():
                    if len(bp_num) == 10 and bp_num[0] in '6789':
                        Lrn_code = cdr["LRN"]
                        if Lrn_code and len(Lrn_code) == 4:
                            lrn_code_numbers.add(Lrn_code)
                        else:
                            b_mobile_code = cdr["b_mobile_code"]
                            bp_code_numbers.add(b_mobile_code)

                    elif len(bp_num) == 10 and bp_num.startswith('140'):
                        cdr['B_Party_Detail'] = {'Provider': 'Telemarketing', 'Type': 'Telemarketing'}

                    elif len(bp_num) == 10 and (bp_num.startswith('1800') or bp_num.startswith('1860')):
                        cdr['B_Party_Detail'] = {'Provider': 'Toll Free', 'Type': 'Toll Free'}

                    elif len(bp_num) == 10:
                        if not landline_json:
                            landline_json = fetch_landline_json()
                            sorted_ll_codes = sorted(landline_json.keys(), key=len, reverse=True)
                        for ll_code in sorted_ll_codes:
                            if bp_num.startswith(ll_code):
                                City = landline_json[ll_code]['City']
                                State = landline_json[ll_code]['State']
                                cdr['B_Party_Detail'] = {'Provider': State + '-' + City, 'Type': 'Landline',
                                                         'City': City,
                                                         'State': State}
                                break

                    elif len(bp_num) > 10:
                        if not isd_json:
                            isd_json = fetch_isd_json()
                            sorted_isd_codes = sorted(isd_json.keys(), key=len, reverse=False)
                        for isd_code in sorted_isd_codes:
                            if bp_num.startswith(isd_code):
                                name = isd_json[isd_code]['name']
                                code = isd_json[isd_code]['code']
                                cdr['B_Party_Detail'] = {'Provider': name + '-' + code, 'Type': 'ISD', 'Country': name,
                                                         'Code': code}
                                break

                    elif len(bp_num) == 5 and bp_num.startswith('5'):
                        cdr['B_Party_Detail'] = {'Provider': 'SMS Short Code', 'Type': 'SMS Short Code'}

                    elif len(bp_num) in (3, 4):
                        cdr['B_Party_Detail'] = {'Provider': 'Customer Care', 'Type': 'Customer Care'}
                else:
                    if '-' in bp_num:
                        sms_code_ary = bp_num.split('-')
                        if len(sms_code_ary) > 1:
                            cdr['SMS_Code'] = sms_code_ary[1]
                            sms_headers.add(sms_code_ary[1])
                            if len(sms_code_ary) > 2:
                                cdr['SMS_Type'] = sms_code_ary[2]
                    elif bp_num.startswith('*') or bp_num.startswith('#'):
                        cdr['B_Party_Detail'] = {'Provider': 'MMI/USSD', 'Type': 'MMI/USSD'}
                    elif any(c.isalpha() for c in bp_num):
                        cdr['B_Party_Detail'] = {'Provider': 'Service', 'Type': 'Service'}
                    else:
                        cdr['B_Party_Detail'] = {'Provider': 'Unknown', 'Type': 'Unknown'}
                # endregion

            # region get mappings
            if len(cell_ids) > 0:
                towers = CellTower.objects.filter(id__in=cell_ids)
                towersdata = CellTowerSerializer(towers, many=True).data
                lookupTower = {item["id"]: item for item in towersdata}

            if len(lrn_code_numbers) > 0:
                lrn_codes = LRNCode.objects.filter(id__in=lrn_code_numbers)
                lrn_codesdata = LRNCodeSerializer(lrn_codes, many=True).data
                lookupLrn = {item["id"]: item for item in lrn_codesdata}

            if len(bp_code_numbers) > 0:
                bp_codes = MobileOperator.objects.filter(id__in=bp_code_numbers)
                bp_codesdata = MobileOperatorSerializer(bp_codes, many=True).data
                lookupBp = {item["id"]: item for item in bp_codesdata}

            if len(sms_headers) > 0:
                sms_codes = SMSHeader.objects.filter(id__in=sms_headers)
                sms_codesdata = SMSHeaderSerializer(sms_codes, many=True).data
                lookupSms = {item["id"]: item for item in sms_codesdata}

            if len(ap_code_numbers) > 0:
                ap_codes = MobileOperator.objects.filter(id__in=ap_code_numbers)
                ap_codesdata = MobileOperatorSerializer(ap_codes, many=True).data
                lookupAp = {item["id"]: item for item in ap_codesdata}

            if len(imsi_code_numbers) > 0:
                imsi_codes = MccMnc.objects.filter(mccmnc_temp__in=imsi_code_numbers)
                imsi_codesdata = MccMncSerializer(imsi_codes, many=True).data
                lookupImsi = {item["mccmnc_temp"]: item for item in imsi_codesdata}

            if len(tac_numbers) > 0:
                tac_codes = ImeiDetails.objects.filter(id__in=tac_numbers)
                tac_codesdata = DeviceInfoSerializer(tac_codes, many=True).data
                lookupTac = {item["id"]: item for item in tac_codesdata}

            if len(roam_code_numbers) > 0:
                roam_codes = MccMnc.objects.filter(mccmnc_temp__in=roam_code_numbers)
                roam_codesdata = MccMncSerializer(roam_codes, many=True).data
                lookupRoam = {item["mccmnc_temp"]: item for item in roam_codesdata}

            # endregion

            day_calls = []
            night_calls = []

            # Define time boundaries
            day_start = time(6, 0, 0)
            day_end = time(17, 59, 59)
            day_max_summary_map = defaultdict(lambda: {
                "Cell ID": None,
                "Tower Address": None,
                "Latitude": None,
                "Longitude": None,
                "Azimuth": None,
                "Roaming": None,
                "Total Calls": 0,
                "Total Days": set(),
                "First Call DateTime": None,
                "Last Call DateTime": None
            })
            night_max_summary_map = defaultdict(lambda: {
                "Cell ID": None,
                "Tower Address": None,
                "Latitude": None,
                "Longitude": None,
                "Azimuth": None,
                "Roaming": None,
                "Total Calls": 0,
                "Total Days": set(),
                "First Call DateTime": None,
                "Last Call DateTime": None
            })

            cdr_isd_list=[]
            cdr_mapping_list=[]

            groupedHomeWork = defaultdict(list)

            for cdr in cdr_info:
                if len(lookupRoam) > 0:
                    roamcode = cdr.get("RoamCode")
                    if roamcode:
                        if roamcode in lookupRoam:
                            cdr["RoamingCircle"] = lookupRoam[roamcode]['circle']
                            cdr["RoamingOperator"] = lookupRoam[roamcode]['operator']
                        else:
                            cdr["RoamingCircle"] = 'Unknown'
                            cdr["RoamingOperator"] = 'Unknown'
                    else:
                        cdr["RoamingCircle"] = 'Unknown'
                        cdr["RoamingOperator"] = 'Unknown'
                else:
                    cdr["RoamingCircle"] = 'Unknown'
                    cdr["RoamingOperator"] = 'Unknown'

                if len(lookupTac) >0:
                    tac_code = cdr.get("IMEI_TAC")
                    if tac_code:
                        if tac_code in lookupTac:

                            cdr["IMEI Manufacturer"] = lookupTac[tac_code]['manufacturer']
                            cdr["Device Type"] = lookupTac[tac_code]['devicetype']
                        else:
                            cdr["IMEI Manufacturer"] = 'Unknown'
                            cdr["Device Type"] = 'Unknown'

                    else:
                        cdr["IMEI Manufacturer"] = 'Unknown'
                        cdr["Device Type"] = 'Unknown'
                else:
                    cdr["IMEI Manufacturer"] = 'Unknown'
                    cdr["Device Type"] = 'Unknown'

                if nexus_serializer.data.get('RecordType') != 'IMEI':
                    if len(lookupTac) > 0:
                        tac_code = cdr.get("IMEI_TAC")
                        if tac_code:
                            if tac_code in lookupTac:
                                cdr["IMEI_Detail"] = lookupTac[tac_code]

                if len(lookupTower) > 0:
                    fcgi = cdr.get("First_CGI")
                    lcgi = cdr.get("Last_CGI")
                    if fcgi and fcgi == lcgi:
                        if fcgi in lookupTower:
                            cdr["First_CGI_Detail"] = lookupTower[fcgi]
                    else:
                        if fcgi and fcgi in lookupTower:
                            cdr["First_CGI_Detail"] = lookupTower[fcgi]
                        if lcgi and lcgi in lookupTower:
                            cdr["Last_CGI_Detail"] = lookupTower[lcgi]

                if cdr.get('IMSI_CODE'):
                    if cdr["IMSI_CODE"]:
                        if len(lookupImsi) > 0:
                            Imsi_Code = cdr["IMSI_CODE"]
                            if Imsi_Code in lookupImsi:
                                circle = lookupImsi[Imsi_Code]['circle']
                                operator = lookupImsi[Imsi_Code]['operator']
                                cdr['A_Party_Detail'] = {'Provider': circle + '-' + operator, 'Type': 'Mobile-IMSI',
                                                         'Circle': circle, 'Operator': operator}
                    elif cdr.get('a_mobile_code'):
                        if cdr["a_mobile_code"]:
                            if len(lookupAp) > 0:
                                a_mobile_code = cdr["a_mobile_code"]
                                if a_mobile_code in lookupAp:
                                    circle = lookupAp[a_mobile_code]['Circle']
                                    operator = lookupAp[a_mobile_code]['Operator']
                                    cdr['A_Party_Detail'] = {'Provider': circle + '-' + operator,
                                                             'Type': 'Mobile-Code', 'Circle': circle,
                                                             'Operator': operator}

                Lrn_code = cdr.get("LRN")

                b_mobile_code = cdr["b_mobile_code"]
                if Lrn_code and len(Lrn_code) == 4:
                    if len(lookupLrn) > 0:
                        if Lrn_code in lookupLrn:
                            circle = lookupLrn[Lrn_code]['circle']
                            operator = lookupLrn[Lrn_code]['operator']
                            cdr['B_Party_Detail'] = {'Provider': circle + '-' + operator, 'Type': 'Mobile-LRN',
                                                     'Circle': circle, 'Operator': operator}
                        else:
                            cdr['B_Party_Detail'] = {'Provider': 'Mobile-LRN-Unknown', 'Type': 'Mobile-LRN-Unknown'}
                    else:
                        cdr['B_Party_Detail'] = {'Provider': 'Mobile-LRN-Unknown', 'Type': 'Mobile-LRN-Unknown'}
                elif b_mobile_code and len(b_mobile_code) == 4:
                    if len(lookupBp) > 0:
                        if b_mobile_code in lookupBp:
                            circle = lookupBp[b_mobile_code]['Circle']
                            operator = lookupBp[b_mobile_code]['Operator']
                            cdr['B_Party_Detail'] = {'Provider': circle + '-' + operator, 'Type': 'Mobile-Code',
                                                     'Circle': circle, 'Operator': operator}
                        else:
                            cdr['B_Party_Detail'] = {'Provider': 'Mobile-Code-Unknown', 'Type': 'Mobile-Code-Unknown'}
                    else:
                        cdr['B_Party_Detail'] = {'Provider': 'Mobile-Code-Unknown', 'Type': 'Mobile-Code-Unknown'}
                else:
                    bp_num = cdr["B_Party"]
                    if '-' in bp_num:
                        if len(lookupSms) > 0:
                            sms_code = cdr['SMS_Code']
                            if sms_code in lookupSms:
                                address = lookupSms[sms_code]['address']
                                smstype = None
                                if cdr.get('SMS_Type'):
                                    sms_type_c = cdr['SMS_Type']
                                    if sms_type_c == 'P':
                                        smstype = 'Promotional/Service'
                                    elif sms_type_c == 'S':
                                        smstype = 'Service Implicit'
                                    elif sms_type_c == 'T':
                                        smstype = 'Transactional'
                                    elif sms_type_c == 'G':
                                        smstype = 'Government'
                                if not smstype:
                                    smstype = lookupSms[sms_code]['type']
                                cdr['B_Party_Detail'] = {'Provider': address + '-' + smstype, 'Type': smstype,
                                                         'Address': address}
                            else:
                                cdr['B_Party_Detail'] = {'Provider': 'SMS-Header-Unknown', 'Type': 'SMS-Header-Unknown'}

                groupedHomeWork[cdr["SDate"]].append(cdr)
                # region of day and night map
                sdt_str = cdr.get("SDateTime")
                if not sdt_str:
                    continue

                sdt = datetime.strptime(sdt_str, "%Y-%m-%dT%H:%M:%SZ")
                s_time = sdt.time()
                s_date = sdt.date()

                if day_start <= s_time <= day_end:
                    # day_calls.append(cdr)
                    # region max stay
                    cell_id = cdr.get('First_CGI')
                    if cell_id:
                        record = day_max_summary_map[cell_id]
                        if record["Cell ID"] is None:
                            record["Cell ID"] = cell_id
                            RoamingCircle = cdr.get('RoamingCircle')
                            if RoamingCircle:
                                record["Roaming"] = RoamingCircle
                            First_CGI_Detail = cdr.get('First_CGI_Detail')
                            if First_CGI_Detail:
                                TowerAddress = First_CGI_Detail.get('ADDRESS')
                                if TowerAddress:
                                    record["Tower Address"] = TowerAddress
                                    record["Latitude"] = First_CGI_Detail.get('LATITUDE')
                                    record["Longitude"] = First_CGI_Detail.get('LONGITUDE')
                                    record["Azimuth"] = First_CGI_Detail.get('AZIMUTH')
                        # Count calls

                        record["Total Calls"] += 1

                        # Track days
                        record["Total Days"].add(cdr['SDate'])

                        # Track first call datetime
                        if record["First Call DateTime"] is None or cdr['SDateTime'] < record["First Call DateTime"]:
                            record["First Call DateTime"] = cdr['SDateTime']

                        # Track last call datetime
                        if record["Last Call DateTime"] is None or cdr['SDateTime'] > record["Last Call DateTime"]:
                            record["Last Call DateTime"] = cdr['SDateTime']
                    # endregion max stay
                else:
                    # night_calls.append(cdr)
                    # region max stay
                    cell_id = cdr.get('First_CGI')
                    if cell_id:
                        record = night_max_summary_map[cell_id]
                        if record["Cell ID"] is None:
                            record["Cell ID"] = cell_id
                            RoamingCircle = cdr.get('RoamingCircle')
                            if RoamingCircle:
                                record["Roaming"] = RoamingCircle
                            First_CGI_Detail = cdr.get('First_CGI_Detail')
                            if First_CGI_Detail:
                                TowerAddress = First_CGI_Detail.get('ADDRESS')
                                if TowerAddress:
                                    record["Tower Address"] = TowerAddress
                                    record["Latitude"] = First_CGI_Detail.get('LATITUDE')
                                    record["Longitude"] = First_CGI_Detail.get('LONGITUDE')
                                    record["Azimuth"] = First_CGI_Detail.get('AZIMUTH')
                        # Count calls

                        record["Total Calls"] += 1

                        # Track days
                        record["Total Days"].add(cdr['SDate'])

                        # Track first call datetime
                        if record["First Call DateTime"] is None or cdr['SDateTime'] < record["First Call DateTime"]:
                            record["First Call DateTime"] = cdr['SDateTime']

                        # Track last call datetime
                        if record["Last Call DateTime"] is None or cdr['SDateTime'] > record["Last Call DateTime"]:
                            record["Last Call DateTime"] = cdr['SDateTime']
                    # endregion max stay

                # endregion of day and night map

                # region mapping
                cgi = cdr.get('First_CGI_Detail')

                cdr_mapping = { }
                cdr_mapping["CdrNo"] = nexus_serializer.data.get("CDRNo_Or_ImeiNo")
                cdr_mapping["B Party"] = cdr["B_Party"]
                cdr_mapping["Date"] = s_date.strftime("%d-%m-%Y")
                cdr_mapping["Time"] = s_time.strftime("%H:%M:%S")
                cdr_mapping["Duration"] = cdr["Duration"]
                cdr_mapping["Call Type"] = cdr["Call_Type"]
                cdr_mapping["First Cell ID"] = cdr.get("First_CGI")
                cdr_mapping["First Cell ID Address"] = cdr["First_CGI_Detail"]['ADDRESS'] if cdr.get("First_CGI_Detail") else None
                cdr_mapping["Last Cell ID"] = cdr.get("Last_CGI")
                cdr_mapping["Last Cell ID Address"] = cdr["Last_CGI_Detail"]['ADDRESS'] if cdr.get("Last_CGI_Detail") else None
                cdr_mapping["IMEI"] = cdr.get("IMEI")
                cdr_mapping["IMEI Manufacturer"] = cdr["IMEI_Detail"]['manufacturer'] if cdr.get("IMEI_Detail") else None
                cdr_mapping["Device Type"] = cdr["IMEI_Detail"]['devicetype'] if cdr.get("IMEI_Detail") else None
                cdr_mapping["Main City (First CellID)"] = cdr["First_CGI_Detail"]['MAIN_CITY'] if cdr.get("First_CGI_Detail") else None
                cdr_mapping["Sub City (First CellID)"] = cdr["First_CGI_Detail"]['SUB_CITY'] if cdr.get("First_CGI_Detail") else None
                cdr_mapping["Lat-Long-Azimuth (First CellID)"] = f"{cgi.get('LATITUDE', '')} {cgi.get('LONGITUDE', '')} {cgi.get('AZIMUTH', '')}" if cgi else None


                cdr_mapping["Crime"] = crime_serializer['Crime']
                cdr_mapping["Roam"] = cdr.get("RoamingCircle")
                cdr_mapping["IMSI"] = cdr.get("IMSI")
                cdr_mapping["Circle"] = cdr['A_Party_Detail']['Circle']
                cdr_mapping["Operator"] = cdr['A_Party_Detail']['Operator']
                cdr_mapping["LRN"] = cdr.get("LRN")
                cdr_mapping["CallForward"] = cdr.get("CallForward")
                cdr_mapping["Location"] =  (f"https://maps.google.com/maps/?q={cgi['LATITUDE']},{cgi['LONGITUDE']}" if cgi.get('LATITUDE') else '') if  cgi else None

                cdr_mapping_list.append(cdr_mapping)
                b_party_detail =cdr.get('B_Party_Detail')
                if b_party_detail:
                    if b_party_detail.get('Type') == 'ISD':
                        cdr_isd_list.append(cdr_mapping)

                if day_start <= s_time <= day_end:
                    day_calls.append(cdr_mapping)
                else:
                    night_calls.append(cdr_mapping)

                # endregion mapping

            # region OFF/Un-Used Period
            off_un_used_period = []
            for i in range(1, len(cdr_info)):
                prev = cdr_info[i - 1]
                curr = cdr_info[i]

                t1 = datetime.strptime(prev["SDateTime"], "%Y-%m-%dT%H:%M:%SZ")
                t2 = datetime.strptime(curr["SDateTime"], "%Y-%m-%dT%H:%M:%SZ")

                if (t2 - t1).total_seconds() >= 86400:
                    period_str = f"{t1.strftime('%d/%m/%Y %H:%M:%S')} -- {t2.strftime('%d/%m/%Y %H:%M:%S')}"
                    td =t2 - t1
                    total_seconds = int(td.total_seconds())
                    days = total_seconds // 86400
                    hours = (total_seconds % 86400) // 3600
                    minutes = (total_seconds % 3600) // 60
                    formated_time = f"{days} D, {hours} H, {minutes} M"

                    First_CGI_Detail_Address_P = prev.get("First_CGI")
                    First_CGI_Detail_P = prev.get('First_CGI_Detail')
                    if First_CGI_Detail_P:
                        if First_CGI_Detail_P.get('Address'):
                            if First_CGI_Detail_Address_P is not None and First_CGI_Detail_P.get('Address') is not None:
                                First_CGI_Detail_Address_P = First_CGI_Detail_Address_P + " -- " + First_CGI_Detail_P.get('Address')

                    First_CGI_Detail_Address_C = curr.get("First_CGI")
                    First_CGI_Detail_C = curr.get('First_CGI_Detail')
                    if First_CGI_Detail_C:
                        if First_CGI_Detail_C.get('Address'):
                            if First_CGI_Detail_Address_C is not None and First_CGI_Detail_C.get('Address') is not None:
                                First_CGI_Detail_Address_C = First_CGI_Detail_Address_C + " -- " + First_CGI_Detail_C.get('Address')

                    off_un_used_period.append({
                        "Period": period_str,
                        "Total Days": formated_time,
                        "IMEI(OFF)": prev.get("IMEI"),
                        "IMEI(ON)": curr.get("IMEI"),
                        "IMSI(OFF)": prev.get("IMSI"),
                        "IMSI(ON)": curr.get("IMSI"),
                        "Location(OFF)": First_CGI_Detail_Address_P,
                        "Location(ON)": First_CGI_Detail_Address_C,
                        "Circle(OFF)": prev.get("RoamingCircle"),
                        "Circle(ON)": curr.get("RoamingCircle"),
                    })
            # endregion OFF/Un-Used Period

            # region home location based on first and last call
            cellid_summary_b_fl_call_map = defaultdict(lambda: {
                "Cell ID": None,
                "Tower Address": None,
                "Latitude": None,
                "Longitude": None,
                "Azimuth": None,
                "Roaming": None,
                "Total Calls": 0,
                "Total Days": set(),
                "First Call DateTime": None,
                "Last Call DateTime": None
            })
            resultHomeWork = []
            for records in groupedHomeWork.values():
                if len(records) == 1:
                    resultHomeWork.append(records[0])  # Only one record, add it once
                else:
                    resultHomeWork.append(records[0])  # First
                    resultHomeWork.append(records[-1])  # Last
            for cdr in resultHomeWork:
                cell_id = cdr.get('First_CGI')
                if cell_id:
                    record = cellid_summary_b_fl_call_map[cell_id]
                    if record["Cell ID"] is None:
                        record["Cell ID"] = cell_id
                        RoamingCircle = cdr.get('RoamingCircle')
                        if RoamingCircle:
                            record["Roaming"] = RoamingCircle
                        First_CGI_Detail = cdr.get('First_CGI_Detail')
                        if First_CGI_Detail:
                            TowerAddress = First_CGI_Detail.get('Address')
                            if TowerAddress:
                                record["Tower Address"] = TowerAddress
                                record["Latitude"] = First_CGI_Detail.get('Lat')
                                record["Longitude"] = First_CGI_Detail.get('Long')
                                record["Azimuth"] = First_CGI_Detail.get('Azimuth')
                    # Count calls

                    record["Total Calls"] += 1

                    # Track days
                    record["Total Days"].add(cdr['SDate'])

                    # Track first call datetime
                    if record["First Call DateTime"] is None or cdr['SDateTime'] < record["First Call DateTime"]:
                        record["First Call DateTime"] = cdr['SDateTime']

                    # Track last call datetime
                    if record["Last Call DateTime"] is None or cdr['SDateTime'] > record["Last Call DateTime"]:
                        record["Last Call DateTime"] = cdr['SDateTime']
                # endregion max stay
            # endregion home location based on first and last call

            summary_map = defaultdict(lambda: {
                "Cdrno":nexus_serializer.data.get("CDRNo_Or_ImeiNo"),
                "B Party": None,
                "Provider": None,
                "Type": None,
                "Total Calls": 0,
                "Out Calls": 0,
                "In Calls": 0,
                "Out Sms": 0,
                "In Sms": 0,
                "Other Calls": 0,
                "Roam Calls": 0,
                "Roam Sms": 0,
                "Total Duration": 0,
                "Total Days": set(),
                "Total CellIds": set(),
                "Total imei": set(),
                "Total Imsi": set(),
                "First Call DateTime": None,
                "Last Call DateTime": None
            })
            cellid_summary_map = defaultdict(lambda: {
                "Cell ID": None,
                "Tower Address": None,
                "Latitude": None,
                "Longitude": None,
                "Azimuth": None,
                "Roaming": None,
                "Total Calls": 0,
                "Total Days": set(),
                "First Call DateTime": None,
                "Last Call DateTime": None
            })


            other_state_contact_summary_map = defaultdict(lambda: {
                "Circle": None,
                "Total Calls": 0,
                "Out Calls": 0,
                "In Calls": 0,
                "Out Sms": 0,
                "In Sms": 0,
                "Other Calls": 0,
                "Total Duration": 0
            })

            # region roaming period
            roamperiods = []
            First_CGI_R = cdr_info[0].get('First_CGI')
            if not First_CGI_R:
                First_CGI_R = 'Unknown'

            First_CGI_Detail_Address = 'Unknown'
            First_CGI_Detail_R = cdr_info[0].get('First_CGI_Detail')
            if First_CGI_Detail_R:
                if First_CGI_Detail_R.get('Address'):
                    First_CGI_Detail_Address = First_CGI_Detail_R['Address']

            curRoam = {
                'roaming': cdr_info[0]['RoamingCircle'],
                'start_dt': cdr_info[0]['SDateTime'],
                'end_dt': cdr_info[0]['SDateTime'],
                'start_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                'end_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                'records': [cdr_info[0]]
            }
            # endregion roaming period

            # region imei period

            imeiperiods = []
            First_CGI_R = cdr_info[0].get('First_CGI')
            if not First_CGI_R:
                First_CGI_R = 'Unknown'

            First_CGI_Detail_Address = 'Unknown'
            First_CGI_Detail_R = cdr_info[0].get('First_CGI_Detail')
            if First_CGI_Detail_R:
                if First_CGI_Detail_R.get('Address'):
                    First_CGI_Detail_Address = First_CGI_Detail_R['Address']

            imeiRoam = {
                'imei': cdr_info[0]['IMEI'],
                'manufacturer': cdr_info[0]["IMEI Manufacturer"],
                'devicetype': cdr_info[0]['Device Type'],
                'start_dt': cdr_info[0]['SDateTime'],
                'end_dt': cdr_info[0]['SDateTime'],
                'start_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                'end_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                'records': [cdr_info[0]]
            }
            # endregion imei period

            # region imsi period

            imsiperiods = []
            First_CGI_R = cdr_info[0].get('First_CGI')
            if not First_CGI_R:
                First_CGI_R = 'Unknown'

            First_CGI_Detail_Address = 'Unknown'
            First_CGI_Detail_R = cdr_info[0].get('First_CGI_Detail')
            if First_CGI_Detail_R:
                if First_CGI_Detail_R.get('Address'):
                    First_CGI_Detail_Address = First_CGI_Detail_R['Address']

            imsiRoam = {
                'imsi': cdr_info[0]['IMSI'],
                'start_dt': cdr_info[0]['SDateTime'],
                'end_dt': cdr_info[0]['SDateTime'],
                'start_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                'end_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                'records': [cdr_info[0]]
            }
            # endregion imei period

            isFirstRec = True
            for cdr in cdr_info:

                # region summary
                b_party = cdr["B_Party"]
                record = summary_map[b_party]

                # Populate basic details once
                if record["B Party"] is None:
                    record["B Party"] = b_party
                    if cdr.get('B_Party_Detail') and cdr['B_Party_Detail'].get('Provider'):
                        record["Provider"] = cdr['B_Party_Detail']["Provider"]
                    if cdr.get('B_Party_Detail') and cdr['B_Party_Detail'].get('Type'):
                        record["Type"] = cdr['B_Party_Detail']["Type"]

                # Call count and duration
                record["Total Calls"] += 1
                record["Total Duration"] += cdr['Duration'] or 0

                # Call type logic
                ct = cdr['Call_Type']
                if ct == "CALL_OUT":
                    record["Out Calls"] += 1
                elif ct == "CALL_IN":
                    record["In Calls"] += 1
                elif ct == "SMS_OUT":
                    record["Out Sms"] += 1
                elif ct == "SMS_IN":
                    record["In Sms"] += 1
                else:
                    record["Other Calls"] += 1

                if cdr.get('First_CGI') and cdr['IMSI_CODE']:

                    if cdr['IMSI_CODE'] not in cdr['First_CGI'][:6]:

                        if ct in ("CALL_OUT", "CALL_IN"):
                            record["Roam Calls"] += 1
                        elif ct in ("SMS_OUT", "SMS_IN"):
                            record["Roam Sms"] += 1

                # Unique fields
                record["Total Days"].add(cdr['SDate'])
                if not record["First Call DateTime"] or cdr['SDateTime'] < record["First Call DateTime"]:
                    record["First Call DateTime"] = __import__("datetime").datetime.strptime(cdr['SDateTime'], "%Y-%m-%dT%H:%M:%SZ").strftime("%d-%m-%Y %H:%M:%S")
                if not record["Last Call DateTime"] or cdr['SDateTime'] > record["Last Call DateTime"]:
                    record["Last Call DateTime"] = __import__("datetime").datetime.strptime(cdr['SDateTime'], "%Y-%m-%dT%H:%M:%SZ").strftime("%d-%m-%Y %H:%M:%S")

                if cdr['First_CGI']:
                    record["Total CellIds"].add(cdr['First_CGI'])
                if cdr['IMEI']:
                    record["Total imei"].add(cdr['IMEI'])
                if cdr["IMSI"]:
                    record["Total Imsi"].add(cdr["IMSI"])
                # endregion summary

                # region max stay
                cell_id = cdr.get('First_CGI')
                if cell_id:
                    record = cellid_summary_map[cell_id]
                    if record["Cell ID"] is None:
                        record["Cell ID"] = cell_id
                        RoamingCircle = cdr.get('RoamingCircle')
                        if RoamingCircle:
                            record["Roaming"] = RoamingCircle
                        First_CGI_Detail = cdr.get('First_CGI_Detail')
                        if First_CGI_Detail:
                            TowerAddress = First_CGI_Detail.get('Address')
                            if TowerAddress:
                                record["Tower Address"] = TowerAddress
                                record["Latitude"] = First_CGI_Detail.get('Lat')
                                record["Longitude"] = First_CGI_Detail.get('Long')
                                record["Azimuth"] = First_CGI_Detail.get('Azimuth')
                    # Count calls

                    record["Total Calls"] += 1

                    # Track days
                    record["Total Days"].add(cdr['SDate'])

                    # Track first call datetime
                    if record["First Call DateTime"] is None or cdr['SDateTime'] < record["First Call DateTime"]:
                        record["First Call DateTime"] = __import__("datetime").datetime.strptime(cdr['SDateTime'], "%Y-%m-%dT%H:%M:%SZ").strftime("%d-%m-%Y %H:%M:%S")

                    # Track last call datetime
                    if record["Last Call DateTime"] is None or cdr['SDateTime'] > record["Last Call DateTime"]:
                        record["Last Call DateTime"] = __import__("datetime").datetime.strptime(cdr['SDateTime'], "%Y-%m-%dT%H:%M:%SZ").strftime("%d-%m-%Y %H:%M:%S")
                # endregion max stay

                # region other state contact summary
                if cdr.get('B_Party_Detail') and cdr['B_Party_Detail'].get('Circle'):
                    b_party_circle = cdr["B_Party_Detail"]["Circle"]
                    record = other_state_contact_summary_map[b_party_circle]
                    if record["Circle"] is None:
                        record["Circle"] = b_party_circle

                    # Call count and duration
                    record["Total Calls"] += 1
                    record["Total Duration"] += cdr['Duration'] or 0

                    # Call type logic
                    ct = cdr['Call_Type']
                    if ct == "CALL_OUT":
                        record["Out Calls"] += 1
                    elif ct == "CALL_IN":
                        record["In Calls"] += 1
                    elif ct == "SMS_OUT":
                        record["Out Sms"] += 1
                    elif ct == "SMS_IN":
                        record["In Sms"] += 1
                    else:
                        record["Other Calls"] += 1
                # endregion other state contact summary

                # region roaming period
                if isFirstRec == False:
                    First_CGI_R = cdr.get('First_CGI')
                    if not First_CGI_R:
                        First_CGI_R = 'Unknown'

                    First_CGI_Detail_Address = 'Unknown'
                    First_CGI_Detail_R = cdr.get('First_CGI_Detail')
                    if First_CGI_Detail_R:
                        if First_CGI_Detail_R.get('Address'):
                            First_CGI_Detail_Address = First_CGI_Detail_R['Address']

                    if cdr['RoamingCircle'] == curRoam['roaming']:
                        curRoam['end_dt'] = cdr['SDateTime']
                        curRoam['end_loc'] = First_CGI_R + ' -- ' + First_CGI_Detail_Address
                        curRoam['records'].append(cdr)
                    else:
                        roamperiods.append(curRoam)
                        curRoam = {
                            'roaming': cdr['RoamingCircle'],
                            'start_dt': cdr['SDateTime'],
                            'end_dt': cdr['SDateTime'],
                            'start_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                            'end_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                            'records': [cdr]
                        }

                    if cdr['IMEI'] == imeiRoam['imei']:
                        imeiRoam['end_dt'] = cdr['SDateTime']
                        imeiRoam['end_loc'] = First_CGI_R + ' -- ' + First_CGI_Detail_Address
                        imeiRoam['records'].append(cdr)
                    else:
                        imeiperiods.append(imeiRoam)
                        imeiRoam = {
                            'imei': cdr['IMEI'],
                            'manufacturer': cdr["IMEI Manufacturer"],
                            'devicetype': cdr['Device Type'],
                            'start_dt': cdr['SDateTime'],
                            'end_dt': cdr['SDateTime'],
                            'start_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                            'end_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                            'records': [cdr]
                        }

                    if cdr['IMSI'] == imsiRoam['imsi']:
                        imsiRoam['end_dt'] = cdr['SDateTime']
                        imsiRoam['end_loc'] = First_CGI_R + ' -- ' + First_CGI_Detail_Address
                        imsiRoam['records'].append(cdr)
                    else:
                        imsiperiods.append(imsiRoam)
                        imsiRoam = {
                            'imsi': cdr['IMSI'],
                            'start_dt': cdr['SDateTime'],
                            'end_dt': cdr['SDateTime'],
                            'start_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                            'end_loc': First_CGI_R + ' -- ' + First_CGI_Detail_Address,
                            'records': [cdr]
                        }
                # endregion roaming period
                isFirstRec = False

            roamperiods.append(curRoam)
            imeiperiods.append(imeiRoam)
            imsiperiods.append(imsiRoam)

            # region roaming period
            roamingreport = []
            for p in roamperiods:
                # Ensure start_dt/end_dt are datetime objects
                if isinstance(p['start_dt'], str):
                    p['start_dt'] = datetime.fromisoformat(p['start_dt'].replace("Z", "+00:00"))

                if isinstance(p['end_dt'], str):
                    p['end_dt'] = datetime.fromisoformat(p['end_dt'].replace("Z", "+00:00"))

                recs = p['records']
                days = (p['end_dt'].date() - p['start_dt'].date()).days + 1
                total_calls = len(recs)

                # Count by calltype
                cnt = Counter(r['Call_Type'] for r in recs)
                out_calls = cnt.get('CALL_OUT', 0)
                in_calls = cnt.get('CALL_IN', 0)
                out_sms = cnt.get('SMS_OUT', 0)
                in_sms = cnt.get('SMS_IN', 0)
                other_calls = total_calls - (out_calls + in_calls + out_sms + in_sms)

                # Sum durations (only for voice calls)
                total_duration = sum(
                    r.get('Duration', 0)
                    for r in recs
                )

                roamingreport.append({
                    'Roaming': p['roaming'],
                    'Period': f"{p['start_dt'].strftime('%d/%m/%Y %H:%M')} --- "
                              f"{p['end_dt'].strftime('%d/%m/%Y %H:%M')}",
                    'Total Calls': total_calls,
                    'Total Days': days,
                    'Out Calls': out_calls,
                    'In Calls': in_calls,
                    'Out Sms': out_sms,
                    'In Sms': in_sms,
                    'Other Calls': other_calls,
                    'Total Duration': total_duration,
                    'First Location': p['start_loc'],
                    'Last Location': p['end_loc'],
                })
            # endregion roaming period

            # region imei period
            imeireport = []
            for p in imeiperiods:
                # Ensure start_dt/end_dt are datetime objects
                if isinstance(p['start_dt'], str):
                    p['start_dt'] = datetime.fromisoformat(p['start_dt'].replace("Z", "+00:00"))
                if isinstance(p['end_dt'], str):
                    p['end_dt'] = datetime.fromisoformat(p['end_dt'].replace("Z", "+00:00"))

                recs = p['records']
                days = (p['end_dt'].date() - p['start_dt'].date()).days + 1
                total_calls = len(recs)

                # Count by calltype
                cnt = Counter(r['Call_Type'] for r in recs)
                out_calls = cnt.get('CALL_OUT', 0)
                in_calls = cnt.get('CALL_IN', 0)
                out_sms = cnt.get('SMS_OUT', 0)
                in_sms = cnt.get('SMS_IN', 0)
                other_calls = total_calls - (out_calls + in_calls + out_sms + in_sms)

                # Sum durations (only for voice calls)
                total_duration = sum(
                    r.get('Duration', 0)
                    for r in recs
                )

                imeireport.append({
                    'IMEI': p['imei'],
                    "IMEI Manufacturer": p["manufacturer"],
                    'Device Type': p['devicetype'],
                    'Period': f"{p['start_dt'].strftime('%d/%m/%Y %H:%M')} --- "
                              f"{p['end_dt'].strftime('%d/%m/%Y %H:%M')}",
                    'Total Calls': total_calls,
                    'Total Days': days,
                    'Out Calls': out_calls,
                    'In Calls': in_calls,
                    'Out Sms': out_sms,
                    'In Sms': in_sms,
                    'Other Calls': other_calls,
                    'Total Duration': total_duration,
                    'First Location': p['start_loc'],
                    'Last Location': p['end_loc'],
                })
            # endregion imei period

            # region imsi period
            imsireport = []
            for p in imsiperiods:
                # Ensure start_dt/end_dt are datetime objects
                if isinstance(p['start_dt'], str):
                    p['start_dt'] = datetime.fromisoformat(p['start_dt'].replace("Z", "+00:00"))
                if isinstance(p['end_dt'], str):
                    p['end_dt'] = datetime.fromisoformat(p['end_dt'].replace("Z", "+00:00"))


                recs = p['records']
                days = (p['end_dt'].date() - p['start_dt'].date()).days + 1
                total_calls = len(recs)

                # Count by calltype
                cnt = Counter(r['Call_Type'] for r in recs)
                out_calls = cnt.get('CALL_OUT', 0)
                in_calls = cnt.get('CALL_IN', 0)
                out_sms = cnt.get('SMS_OUT', 0)
                in_sms = cnt.get('SMS_IN', 0)
                other_calls = total_calls - (out_calls + in_calls + out_sms + in_sms)

                # Sum durations (only for voice calls)
                total_duration = sum(
                    r.get('Duration', 0)
                    for r in recs
                )

                imsireport.append({
                    'IMSI': p['imsi'],
                    'Period': f"{p['start_dt'].strftime('%d/%m/%Y %H:%M')} --- "
                              f"{p['end_dt'].strftime('%d/%m/%Y %H:%M')}",
                    'Total Calls': total_calls,
                    'Total Days': days,
                    'Out Calls': out_calls,
                    'In Calls': in_calls,
                    'Out Sms': out_sms,
                    'In Sms': in_sms,
                    'Other Calls': other_calls,
                    'Total Duration': total_duration,
                    'First Location': p['start_loc'],
                    'Last Location': p['end_loc'],
                })
            # endregion imsi period

            summary_data = sorted([
                {
                    **item,
                    "Total Days": len(item["Total Days"]),
                    "Total CellIds": len(item["Total CellIds"]),
                    "Total imei": len(item["Total imei"]),
                    "Total Imsi": len(item["Total Imsi"])
                }
                for item in summary_map.values()
            ], key=lambda x: x["Total Calls"], reverse=True)

            max_calls_data = sorted([
                {
                    "B Party": item["B Party"],
                    "Provider": item["Provider"],
                    "Total Calls": item["Total Calls"]
                }
                for item in summary_data
            ], key=lambda x: x["Total Calls"], reverse=True)

            max_duration_data = sorted([
                {
                    "B Party": item["B Party"],
                    "Provider": item["Provider"],
                    "Total Duration": item["Total Duration"]
                }
                for item in summary_data
            ], key=lambda x: x["Total Duration"], reverse=True)

            cellid_summary_data = sorted([
                {
                    **item,
                    "Total Days": len(item["Total Days"])
                }
                for item in cellid_summary_map.values()
            ], key=lambda x: x["Total Calls"], reverse=True)

            cellid_summary_b_fl_call_map_data = sorted([
                {
                    **item,
                    "Total Days": len(item["Total Days"])
                }
                for item in cellid_summary_b_fl_call_map.values()
            ], key=lambda x: x["Total Calls"], reverse=True)
            if len(cellid_summary_b_fl_call_map_data) > 0:
                cellid_summary_b_fl_call_map_data = cellid_summary_b_fl_call_map_data[0]

            other_state_contact_summary_data = sorted([
                {
                    **item
                }
                for item in other_state_contact_summary_map.values()
            ], key=lambda x: x["Total Calls"], reverse=True)

            day_max_summary_data = sorted([
                {
                    **item,
                    "Total Days": len(item["Total Days"])
                }
                for item in day_max_summary_map.values()
            ], key=lambda x: x["Total Calls"], reverse=True)
            night_max_summary_data = sorted([
                {
                    **item,
                    "Total Days": len(item["Total Days"])
                }
                for item in night_max_summary_map.values()
            ], key=lambda x: x["Total Calls"], reverse=True)

            roamingreport.sort(key=lambda x: x['Total Calls'], reverse=True)
            imeireport.sort(key=lambda x: x['Total Calls'], reverse=True)
            imsireport.sort(key=lambda x: x['Total Calls'], reverse=True)

            night_max=copy.copy(night_max_summary_data[0]if night_max_summary_data else [] )
            day_max=copy.copy(day_max_summary_data[0]if day_max_summary_data else [] )
            cellid_max=copy.copy(cellid_summary_data[0]if cellid_summary_data else [] )

            Work_Home_Location =[]
            if len(night_max_summary_data) > 0:
                Work_Home_Location.append(night_max)
                Work_Home_Location[len(Work_Home_Location) - 1]["Location"] = 'Home'
            if len(day_max_summary_data) > 0:
                Work_Home_Location.append(day_max)
                Work_Home_Location[len(Work_Home_Location) - 1]["Location"] = 'Work'
            if len(cellid_summary_data) > 0:
                Work_Home_Location.append(cellid_max)
                Work_Home_Location[len(Work_Home_Location) - 1]["Location"] = 'Max Stay'


            return Response({
                "1. Mapping": cdr_mapping_list,
                "2. Summary": summary_data,
                "3. Max Calls": max_calls_data,
                "4. Max Duration": max_duration_data,
                "5. Max Stay": cellid_summary_data,
                "6. Other State Contact Summary": other_state_contact_summary_data,
                "7. Roaming Period": roamingreport,
                "8. IMEI Period": imeireport,
                "9. IMSI Period": imsireport,
                "10. OFF/Un-Used Period": off_un_used_period,
                "11. Night Mapping": night_calls,
                "12. Night Max Stay": night_max_summary_data,
                '13. Day Mapping': day_calls,
                "14. Day Max Stay": day_max_summary_data,
                "15. Work/Home Location":Work_Home_Location,
                "16. Home Location Based on Day First and Last Call": cellid_summary_b_fl_call_map_data,
                "17. ISD Calls":cdr_isd_list
            })
        except InvalidQueryError as e:
            return Response({'error': str(e)}, status=400)


class CellTowerListView(APIView):
    @swagger_auto_schema(
        operation_description="Retrieve all Cell Tower records",
        responses={200: CellTowerSerializer(many=True)}
    )
    def get(self, request):
        cell_tower = CellTower.objects.all()
        serializer = CellTowerSerializer(cell_tower, many=True)
        return Response(serializer.data)


class CellTowerDetailView(APIView):
    @swagger_auto_schema(
        operation_description="Retrieve a single Cell Tower record by ID",
        responses={200: CellTowerSerializer()}
    )
    def get(self, request, pk):
        print(f"Received Cell Tower ID: {pk}")
        try:
            cell_tower = CellTower.objects.get(id=pk)
        except CellTower.DoesNotExist:
            return Response({"error": "Cell Tower not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = CellTowerSerializer(cell_tower)
        return Response(serializer.data)



