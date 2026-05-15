from collections import defaultdict
from datetime import datetime
from django.utils.dateparse import parse_datetime
from mongoengine import InvalidQueryError, get_db
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from ..Queries.NewMissingNumber import NewMissingNumber
from ..CommonNumbers.CommonNumbers import get_common_entity_details
from ..models import *
from ..CdrToCdr.CdrToCdr import get_cdr_to_cdr_counts
from ..serializers import *
from drf_yasg.utils import swagger_auto_schema


class NewOrMissingNumberView(APIView):
    @swagger_auto_schema(request_body=CDRFilterSerializer)
    def post(self, request):

        try:
            pk = request.data.get('seq_id', [])
            filtervalue = request.data.get('filter')
            from_date = parse_datetime(request.data.get('from_date'))
            to_date = parse_datetime(request.data.get('to_date'))

            # Fetch CDR numbers from datanexus
            datanexus_records = Nexus.objects(id__in=pk).only('id', 'CDRNo_Or_ImeiNo')
            seq_id_to_cdr = {str(rec.id): rec.CDRNo_Or_ImeiNo for rec in datanexus_records if rec.CDRNo_Or_ImeiNo}

            final_b_party_numbers = NewMissingNumber(pk, filtervalue, from_date, to_date)

            final_new_missing_numbers = []
            for number in final_b_party_numbers:
                # --- FIX: Flatten seq_id list to handle nested lists ---
                raw_seq_ids = number.get('seq_id', [])
                flat_seq_ids = []
                for item in raw_seq_ids:
                    if isinstance(item, list):
                        flat_seq_ids.extend([str(i) for i in item])
                    else:
                        flat_seq_ids.append(str(item))
                # -------------------------------------------------------

                cdr_list = [seq_id_to_cdr[id] for id in flat_seq_ids if id in seq_id_to_cdr]

                final_new_missing_numbers.append({**
                                                  {'Number': number['B_Party'],
                                                   'Count': len(number['seq_id']),
                                                   'CDR Nos': ' || '.join(cdr_list) if len(cdr_list) > 1 else (
                                                       cdr_list[0] if cdr_list else ''),
                                                   'First & Last Call': (
                                                       f'{datetime.strptime(str(number["first_datetime"]), "%Y-%m-%d %H:%M:%S").strftime("%d/%b/%Y %H:%M:%S")} - {datetime.strptime(str(number["last_datetime"]), "%Y-%m-%d %H:%M:%S").strftime("%d/%b/%Y %H:%M:%S")}' if
                                                       number['first_datetime'] and number['last_datetime'] else ''),
                                                   }, **{cdr: 'YES' if cdr in cdr_list else '-' for cdr in
                                                         seq_id_to_cdr.values()}})

            sorted_data = sorted([json_data for json_data in final_new_missing_numbers], key=lambda x: x['Count'],
                                 reverse=True)

            return Response({filtervalue: sorted_data}, status=200)

        except InvalidQueryError as e:
            return Response({'error': str(e)}, status=400)

class CommonBPartyView(APIView):

    def post(self, request):

        # 1. Inputs & Mapping
        group_list = request.data.get('group_list', [])

        # Source Mapping (Checks source_type OR source)
        raw_source = request.data.get('source_type') or request.data.get('source') or 'mobile'
        raw_source = raw_source.lower()

        source_map = {
            'mobile.no': 'mobile', 'mobile': 'mobile',
            'imei nos.': 'imei', 'imei': 'imei',
            'cell ids.': 'cellid', 'cell ids': 'cellid', 'cellid': 'cellid',
            'lat long': 'latlong', 'latlong': 'latlong'
        }
        source_type = source_map.get(raw_source, 'mobile')

        # Party Type (Checks party_type OR mode)
        party_type = request.data.get('party_type') or request.data.get('mode') or 'B'

        # Check Groups
        use_groups = request.data.get('use_groups', True)

        # 2. Nexus Mapping
        seq_list = set()
        for group in group_list:
            seq_list.update(id for id in group['seq_id'])
        datanexus_records = Nexus.objects(id__in=list(seq_list)).only('id', 'CDRNo_Or_ImeiNo')
        seq_id_to_cdr = {str(rec.id): rec.CDRNo_Or_ImeiNo for rec in datanexus_records if rec.CDRNo_Or_ImeiNo}
        print("the result",seq_id_to_cdr)
        # 3. Call Logic
        result = get_common_entity_details(
            group_list,
            source_type=source_type,
            party_type=party_type,
            group_by_crime=(not use_groups)
        )

        # 4. Lat/Long Lookup
        lat_long_map = {}
        if source_type == 'latlong':
            # Use 'Entity' key safely
            cell_ids = [r['Entity'] for r in result if r.get('Entity')]
            if cell_ids:
                try:
                    db_cdr = get_db(alias='cdr_db')
                    ssd_db = db_cdr.client['ssd_logs']
                    cursor = ssd_db.cellid_info.find({"_id": {"$in": cell_ids}})
                    for doc in cursor:
                        lat = doc.get('LATITUDE')
                        lon = doc.get('LONGITUDE')
                        if lat and lon:
                            # Format: Lat/Long
                            lat_long_map[doc['_id']] = f"{lat}/{lon}"
                except Exception:
                    pass

        # Determine Key Name based on Source
        key_map = {
            'mobile': 'Number',
            'imei': 'IMEI',
            'cellid': 'Cell ID',
            'latlong': 'Lattitude/Longitude'
        }
        main_key = key_map.get(source_type, 'Number')

        # 5. Format Response
        final_common_numbers_list = []
        for number in result:
            # Safe access to Entity, fallback to B_Party
            entity_val = number.get('Entity', number.get('B_Party', 'Unknown'))

            # Map CellID to LatLong if needed
            display_val = entity_val
            if source_type == 'latlong':
                display_val = lat_long_map.get(entity_val, entity_val)

            cdr_list = [seq_id_to_cdr[id] for id in number['common_in_cdrs'] if id in seq_id_to_cdr]

            final_common_numbers_list.append({**
                                              {
                                                  main_key: display_val,
                                                  'Count': len(number['common_in_cdrs']),
                                                  'Common in Groups': ' & '.join(number['common_in_groups']) if len(
                                                      number['common_in_groups']) > 1 else (
                                                      number['common_in_groups'][0] if number[
                                                          'common_in_groups'] else ''),
                                                  "Common in CDRs": ' & '.join(cdr_list) if len(cdr_list) > 1 else (
                                                      cdr_list[0] if cdr_list else ''),
                                                  'First & Last Call': (
                                                      f'{datetime.strptime(str(number["first_datetime"]), "%Y-%m-%d %H:%M:%S").strftime("%d/%b/%Y %H:%M:%S")} - {datetime.strptime(str(number["last_datetime"]), "%Y-%m-%d %H:%M:%S").strftime("%d/%b/%Y %H:%M:%S")}' if
                                                      number['first_datetime'] and number['last_datetime'] else '')
                                              },
                                              **{cdr: 'YES' if cdr in cdr_list else '-' for cdr in
                                                 seq_id_to_cdr.values()}
                                              })

        # Strict Filter: Count must be >= 2
        final_common_numbers_list = [x for x in final_common_numbers_list if x['Count'] >= 2]
        print("the final",final_common_numbers_list)
        sorted_data = sorted(final_common_numbers_list, key=lambda x: x['Count'], reverse=True)

        return Response({'common': sorted_data}, status=200)

class CdrToCdrView(APIView):

    def post(self, request):
        seq_ids = request.data.get('seq_id', [])
        seq_ids_str = [str(sid) for sid in seq_ids]

        from_date = parse_datetime(request.data.get('from_date'))
        to_date = parse_datetime(request.data.get('to_date'))

        if not from_date or not to_date:
            return Response({'error': 'Valid from_date and to_date are required'}, status=400)

        # Convert queryset to list so it can be iterated multiple times
        datanexus_records = list(
            Nexus.objects(id__in=seq_ids_str).only('id', 'CDRNo_Or_ImeiNo')
        )

        if not datanexus_records:
            return Response({'cdr_to_cdr': [], 'message': 'No matching Nexus records found'}, status=200)

        seq_id_to_cdr = {}
        cdr_to_seq_id = {}

        for rec in datanexus_records:
            sid = str(rec.id)
            number = rec.CDRNo_Or_ImeiNo
            if number:
                seq_id_to_cdr[sid] = number
                cdr_to_seq_id[number] = sid

        all_cdr_numbers = list(seq_id_to_cdr.values())
        print("all",all_cdr_numbers)

        if not all_cdr_numbers:
            return Response({'cdr_to_cdr': [], 'message': 'No CDR numbers found in Nexus records'}, status=200)

        # KEY FIX: Match by A_Party/B_Party instead of seq_id
        # seq_id is an Array in the DB and some records have no seq_id at all
        result = get_cdr_to_cdr_counts(
            cdr_numbers=all_cdr_numbers,
            from_date=from_date,
            to_date=to_date
        )
        print("result",result)
        # Index: (a_party, b_party) -> item
        result_index = {}
        for item in result:
            key = (item['A_Party'], item['B_Party'])
            result_index[key] = item

        data_list = []

        for rec in datanexus_records:
            current_number = rec.CDRNo_Or_ImeiNo
            if not current_number:
                continue

            Record_T = {"Number": current_number}
            has_count = False
            DatesList = []

            # Pre-populate all columns with '-'
            for other_rec in datanexus_records:
                if other_rec.CDRNo_Or_ImeiNo:
                    Record_T[other_rec.CDRNo_Or_ImeiNo] = '-'

            for other_rec in datanexus_records:
                b_number = other_rec.CDRNo_Or_ImeiNo
                if not b_number or b_number == current_number:
                    continue

                item = result_index.get((current_number, b_number))
                if item and item['count'] > 0:
                    Record_T[b_number] = item['count']
                    has_count = True
                    if item.get('first_call'):
                        DatesList.append(item['first_call'])
                    if item.get('last_call'):
                        DatesList.append(item['last_call'])

            if has_count:
                DatesList.sort()
                if DatesList:
                    s_str = DatesList[0].strftime("%d/%b/%Y %H:%M:%S")
                    e_str = DatesList[-1].strftime("%d/%b/%Y %H:%M:%S")
                    date_range = f"{s_str} - {e_str}" if DatesList[0] != DatesList[-1] else s_str
                else:
                    date_range = '-'

                Record_T['FirstLastCall'] = date_range
                data_list.append(Record_T)


        return Response({'cdr_to_cdr': data_list}, status=200)