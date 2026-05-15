# from django.utils.dateparse import parse_datetime
# from mongoengine import InvalidQueryError
# from rest_framework.views import APIView
# from rest_framework.response import Response
# from rest_framework import status
# from datetime import datetime
#
# from .undertowercall import get_undertower_calls
# from ..summary.summary import get_towerdump_summary
# from ..towerdump_models.towerdump_model import TowerDumpNexus, TowerDumpDetailRecord
# from ...models import CrimeInformation, MobileOperator, MccMnc, ImeiDetails, UserAccess, SMSHeader, CellTower, LRNCode
# from ..serializers import *
# from drf_yasg.utils import swagger_auto_schema
# from drf_yasg import openapi
#
# from ...serializers import CrimeInformationSerializer, CellTowerSerializer, LRNCodeSerializer, MobileOperatorSerializer, \
#     MccMncSerializer, DeviceInfoSerializer, UserAccessSerializer
# from ...utilities import fetch_landline_json, fetch_isd_json
#
#
# class CommonMethodMixin:
#     def common_method(self, nexus_data):
#         imsi_set, ap_set, tac_set, crime_set, user_set = set(), set(), set(), set(), set()
#
#         for cdr in nexus_data:
#             crime_set.add(cdr["CrimeID"])
#             user_set.add(cdr["UserAccessID"])
#             if cdr.get('ImsiCode'):
#                 imsi_set.add(cdr['ImsiCode'])
#             code = cdr.get('Tac_Or_Mobile_Code')
#             if code:
#                 (ap_set if cdr['RecordType']=="CDR" else tac_set).add(code)
#
#         # Fetch lookups
#         lookupAp = {item["id"]: item for item in MobileOperatorSerializer(MobileOperator.objects.filter(id__in=ap_set), many=True).data} if ap_set else {}
#         lookupImsi = {item["mccmnc_temp"]: item for item in MccMncSerializer(MccMnc.objects.filter(mccmnc_temp__in=imsi_set), many=True).data} if imsi_set else {}
#         lookupTac = {item["id"]: item for item in DeviceInfoSerializer(ImeiDetails.objects.filter(id__in=tac_set), many=True).data} if tac_set else {}
#         lookupCrimeID = {item["id"]: item for item in CrimeInformationSerializer(CrimeInformation.objects.filter(id__in=crime_set), many=True).data} if crime_set else {}
#         lookupUserID = {item["id"]: item for item in UserAccessSerializer(UserAccess.objects.filter(id__in=user_set), many=True).data} if user_set else {}
#
#         # Update nexus data
#         for cdr in nexus_data:
#             if lookupUserID and cdr["UserAccessID"] in lookupUserID:
#                 cdr['UserID'] = lookupUserID[cdr["UserAccessID"]]['UserID']
#
#             if lookupCrimeID and cdr["CrimeID"] in lookupCrimeID:
#                 crime = lookupCrimeID[cdr["CrimeID"]]
#                 cdr['Crime'] = crime['Crime']
#                 cdr['AreaLocation'] = crime['AreaLocation']
#
#             # IMSI Provider
#             imsi = cdr.get('ImsiCode')
#             if imsi and lookupImsi and imsi in lookupImsi:
#                 info = lookupImsi[imsi]
#                 cdr.update({'Provider': f"{info['circle']}-{info['operator']}", 'Type': 'Mobile-IMSI', 'Circle': info['circle'], 'Operator': info['operator']})
#
#             # Mobile/TAC codes
#             if cdr['RecordType']=="CDR" and cdr.get('Tac_Or_Mobile_Code') and lookupAp:
#                 code = cdr['Tac_Or_Mobile_Code']
#                 if code in lookupAp:
#                     info = lookupAp[code]
#                     cdr.update({'Provider': f"{info['Circle']}-{info['Operator']}", 'Type': 'Mobile-Code', 'Circle': info['Circle'], 'Operator': info['Operator']})
#             elif cdr.get('Tac_Or_Mobile_Code') and lookupTac:
#                 code = cdr['Tac_Or_Mobile_Code']
#                 if code in lookupTac:
#                     cdr.update(lookupTac[code])
#
#         return nexus_data
#
#
# class TowerDumpNexusListView(CommonMethodMixin, APIView):
#     @swagger_auto_schema(responses={200: TowerDumpNexusSerializer(many=True)})
#     def get(self, request):
#         try:
#             nexus_data = TowerDumpNexusSerializer(TowerDumpNexus.objects.all(), many=True).data
#             nexus_data = self.common_method(nexus_data)
#             return Response(nexus_data)
#         except TowerDumpNexus.DoesNotExist:
#             return Response({"error": "Nexus records not found"}, status=404)
#
#
# class TowerDumpDetailRecordDetailView(APIView):
#     @swagger_auto_schema(request_body=TowerDumpFilterSerializer)
#     def post(self, request, lookupTower=None):
#         try:
#             pk = request.data.get('seq_id')
#             filtervalue = request.data.get('filter')
#
#             nexus = TowerDumpNexusSerializer(TowerDumpNexus.objects.get(id=pk), many=False)
#             crime_serializer = CrimeInformationSerializer(CrimeInformation.objects.get(id=nexus.data['CrimeID']), many=False).data
#
#             # Filter TowerDumpDetailRecord
#             qs = TowerDumpDetailRecord.objects.filter(seq_id=pk)
#             if filtervalue:
#                 from_date = parse_datetime(request.data.get('from_date'))
#                 to_date = parse_datetime(request.data.get('to_date'))
#                 min_dur = request.data.get('min_duration')
#                 max_dur = request.data.get('max_duration')
#                 if from_date and to_date:
#                     qs = qs.filter(SDateTime__gte=from_date, SDateTime__lte=to_date)
#                 if min_dur and max_dur:
#                     qs = qs.filter(Duration__gte=min_dur, Duration__lte=max_dur)
#
#             if not qs:
#                 return Response({'error': 'Record not found'}, 404)
#
#             towerdump_info = TowerDumpDetailRecordSerializer(qs.order_by("SDateTime"), many=True).data
#
#             # region Sets
#             roam_set, imsi_set, ap_set, lrn_set, bp_set, sms_set, tac_set, cell_set = set(), set(), set(), set(), set(), set(), set(), set()
#             for t in towerdump_info:
#                 fcgi = t.get("First_CGI")
#                 lcgi = t.get("Last_CGI")
#                 cell_set.update([fcgi, lcgi])
#                 if fcgi:
#                     code = fcgi[:6] if len(fcgi) > 5 else fcgi[:5]
#                     if code.isdigit(): roam_set.add(code); t["RoamCode"]=code
#                 if t.get('IMEI_TAC'): tac_set.add(t["IMEI_TAC"])
#                 if t.get('IMSI_CODE'): imsi_set.add(t["IMSI_CODE"])
#                 if t.get('a_mobile_code'): ap_set.add(t["a_mobile_code"])
#                 bp_num = t["B_Party"]
#                 if bp_num.isdigit():
#                     if len(bp_num)==10 and bp_num[0] in '6789':
#                         lrn_set.add(t.get("LRN"))
#                     else:
#                         bp_set.add(t.get("b_mobile_code"))
#             # endregion
#
#             # region Lookups
#             lookupTower = {i["id"]: i for i in CellTowerSerializer(CellTower.objects.filter(id__in=cell_set), many=True).data} if cell_set else {}
#             lookupLrn = {i["id"]: i for i in LRNCodeSerializer(LRNCode.objects.filter(id__in=lrn_set), many=True).data} if lrn_set else {}
#             lookupBp = {i["id"]: i for i in MobileOperatorSerializer(MobileOperator.objects.filter(id__in=bp_set), many=True).data} if bp_set else {}
#             lookupAp = {i["id"]: i for i in MobileOperatorSerializer(MobileOperator.objects.filter(id__in=ap_set), many=True).data} if ap_set else {}
#             lookupImsi = {i["mccmnc_temp"]: i for i in MccMncSerializer(MccMnc.objects.filter(mccmnc_temp__in=imsi_set), many=True).data} if imsi_set else {}
#             lookupRoam = {i["mccmnc_temp"]: i for i in MccMncSerializer(MccMnc.objects.filter(mccmnc_temp__in=roam_set), many=True).data} if roam_set else {}
#             lookupTac = {i["id"]: i for i in DeviceInfoSerializer(ImeiDetails.objects.filter(id__in=tac_set), many=True).data} if tac_set else {}
#             # endregion
#
#             # region Tower Mapping & Migrated numbers
#             towerdump_mapping_list = []
#             migrated_numbers = []
#
#             for t in towerdump_info:
#                 sdt = datetime.strptime(t.get("SDateTime"), "%Y-%m-%dT%H:%M:%SZ")
#                 t_date, t_time = sdt.date(), sdt.time()
#
#                 fcgi, lcgi = t.get("First_CGI"), t.get("Last_CGI")
#                 tower_details = lookupTower.get(fcgi) or {}
#                 roamcode = t.get("RoamCode")
#                 tower_circle = lookupRoam.get(roamcode, tower_details).get('circle') if roamcode else tower_details.get('circle')
#
#                 a_party, b_party = t.get("A_Party"), t.get("B_Party")
#                 home_circle = lookupImsi.get(t.get('IMSI_CODE'), {}).get('circle') or lookupAp.get(t.get('a_mobile_code'), {}).get('Circle')
#                 if home_circle and tower_circle and home_circle != tower_circle:
#                     migrated_numbers.append((a_party, home_circle, tower_circle))
#
#                 towerdump_mapping_list.append({
#                     "TowerID": nexus.data.get("TowerID"),
#                     "A Party": a_party,
#                     "B Party": b_party,
#                     "Date": t_date.strftime("%Y-%m-%d"),
#                     "Time": t_time.strftime("%H:%M:%S"),
#                     "Duration": t["Duration"],
#                     "Call Type": t["Call_Type"],
#                     "Tower Circle": tower_circle,
#                     "Tower Operator": lookupRoam.get(roamcode, tower_details).get('operator') if roamcode else tower_details.get('operator')
#                 })
#
#             # region Under Tower Numbers
#             under_tower_numbers = list({(x["A Party"], x["A Party Circle"], x["A Party Operator"]): x for x in towerdump_mapping_list}.values())
#             # endregion
#
#             return Response({
#                 "4. Under Tower Numbers": under_tower_numbers,
#                 "5. Migrated Numbers": set(migrated_numbers)
#             })
#         except InvalidQueryError as e:
#             return Response({'error': str(e)}, status=400)
