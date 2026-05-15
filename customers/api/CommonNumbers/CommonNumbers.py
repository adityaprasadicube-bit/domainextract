from datetime import datetime
from collections import defaultdict
from mongoengine import get_db
from ..models import Nexus, CrimeInformation


def get_group_entity_info(seq_ids, from_date, to_date, group_name, source_type='mobile', party_type='B'):
    db = get_db(alias='cdr_db')

    match_stage = {
        "seq_id": {"$in": seq_ids},
        "SDateTime": {"$gte": from_date, "$lte": to_date}
    }

    pipeline = [{"$match": match_stage}]

    if source_type == 'imei':
        match_stage["IMEI"] = {"$ne": None, "$type": "string", "$ne": ""}
        pipeline.append({"$group": {
            "_id": "$IMEI",
            "seq_ids": {"$addToSet": "$seq_id"},
            "first_datetime": {"$min": "$SDateTime"},
            "last_datetime": {"$max": "$SDateTime"}
        }})
    elif source_type == 'cellid' or source_type == 'latlong':
        match_stage["First_CGI"] = {"$ne": None, "$type": "string", "$ne": ""}
        pipeline.append({"$group": {
            "_id": "$First_CGI",
            "seq_ids": {"$addToSet": "$seq_id"},
            "first_datetime": {"$min": "$SDateTime"},
            "last_datetime": {"$max": "$SDateTime"}
        }})
    elif source_type == 'mobile':
        if party_type == 'B':
            match_stage["B_Party"] = {"$ne": None, "$type": "string"}
            pipeline.append({"$group": {
                "_id": "$B_Party",
                "seq_ids": {"$addToSet": "$seq_id"},
                "first_datetime": {"$min": "$SDateTime"},
                "last_datetime": {"$max": "$SDateTime"}
            }})
        elif party_type == 'A':
            match_stage["A_Party"] = {"$ne": None, "$type": "string"}
            pipeline.append({"$group": {
                "_id": "$A_Party",
                "seq_ids": {"$addToSet": "$seq_id"},
                "first_datetime": {"$min": "$SDateTime"},
                "last_datetime": {"$max": "$SDateTime"}
            }})
        elif party_type == 'Both':
            pipeline.append({
                "$project": {
                    "seq_id": 1, "SDateTime": 1,
                    "parties": ["$A_Party", "$B_Party"]
                }
            })
            pipeline.append({"$unwind": "$parties"})
            pipeline.append({"$match": {"parties": {"$ne": None, "$ne": ""}}})
            pipeline.append({"$group": {
                "_id": "$parties",
                "seq_ids": {"$addToSet": "$seq_id"},
                "first_datetime": {"$min": "$SDateTime"},
                "last_datetime": {"$max": "$SDateTime"}
            }})

    pipeline.append({
        "$project": {
            "_id": 0, "Entity": "$_id", "seq_ids": 1,
            "first_datetime": 1, "last_datetime": 1,
        }
    })

    results = list(db.CallDetailRecords.aggregate(pipeline))

    result_dict = {}
    for doc in results:
        entity = doc.get("Entity")
        if entity and (source_type != 'mobile' or (len(entity) >= 10 and entity.isdigit())):
            raw_seqs = doc.get("seq_ids", [])
            flat_seqs = set()
            for item in raw_seqs:
                if isinstance(item, list):
                    for sub_item in item: flat_seqs.add(str(sub_item))
                else:
                    flat_seqs.add(str(item))

            result_dict[entity] = {
                "first_datetime": doc["first_datetime"],
                "last_datetime": doc["last_datetime"],
                "common_in_groups": {group_name},
                "common_in_cdrs": flat_seqs
            }
    return result_dict


def get_common_entity_details(group_list, source_type='mobile', party_type='B', group_by_crime=False):
    if group_by_crime:
        all_seq_ids = []
        for g in group_list:
            all_seq_ids.extend(g['seq_id'])
        all_seq_ids = list(set(all_seq_ids))

        nexus_recs = Nexus.objects(id__in=all_seq_ids).only('id', 'CrimeID')
        crime_ids = [n.CrimeID for n in nexus_recs if n.CrimeID]
        crime_recs = CrimeInformation.objects(id__in=crime_ids).only('id', 'Crime')
        crime_map = {c.id: c.Crime for c in crime_recs}

        seq_date_map = {}
        for g in group_list:
            f_d = g['from_date']
            t_d = g['to_date']
            for s in g['seq_id']:
                if s not in seq_date_map: seq_date_map[s] = (f_d, t_d)

        grouped_data = defaultdict(list)
        for rec in nexus_recs:
            c_name = crime_map.get(rec.CrimeID, "Unknown Crime")
            s_id = str(rec.id)
            if s_id in seq_date_map:
                grouped_data[c_name].append(s_id)

        new_group_list = []
        for c_name, s_ids in grouped_data.items():
            if not s_ids: continue
            f_d, t_d = seq_date_map[s_ids[0]]
            new_group_list.append({
                "group_name": c_name, "seq_id": s_ids,
                "from_date": f_d, "to_date": t_d
            })
        group_list = new_group_list

    group_results = []

    for group in group_list:
        seq_ids = group["seq_id"]
        if not seq_ids: continue

        fd = group["from_date"]
        td = group["to_date"]
        if isinstance(fd, str): fd = datetime.fromisoformat(fd.replace('Z', '+00:00'))
        if isinstance(td, str): td = datetime.fromisoformat(td.replace('Z', '+00:00'))

        res = get_group_entity_info(
            seq_ids, fd, td,
            group.get("group_name", "Unknown"),
            source_type, party_type
        )
        group_results.append(res)

    all_entity_info = defaultdict(lambda: {
        "first_datetime": None, "last_datetime": None,
        "common_in_groups": set(), "common_in_cdrs": set()
    })

    for grp_res in group_results:
        for entity, info in grp_res.items():
            data = all_entity_info[entity]
            if not data["first_datetime"] or info["first_datetime"] < data["first_datetime"]:
                data["first_datetime"] = info["first_datetime"]
            if not data["last_datetime"] or info["last_datetime"] > data["last_datetime"]:
                data["last_datetime"] = info["last_datetime"]

            data["common_in_groups"].update(info["common_in_groups"])
            data["common_in_cdrs"].update(info["common_in_cdrs"])

    final_result = []
    for entity, info in all_entity_info.items():
        final_result.append({
            "Entity": entity,
            "first_datetime": info["first_datetime"],
            "last_datetime": info["last_datetime"],
            "common_in_groups": list(info["common_in_groups"]),
            "common_in_cdrs": list(info["common_in_cdrs"]),
            "group_count": len(info["common_in_groups"]),
            "cdr_count": len(info["common_in_cdrs"])
        })

    # STRICT FILTERING: Count >= 2
    if len(group_list) <= 1:
        result = [r for r in final_result if r["cdr_count"] >= 2]
    else:
        result = [r for r in final_result if r["group_count"] >= 2]

    return result