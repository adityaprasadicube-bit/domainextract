# ALLOWED_FIELDS = [
#     "A_Party",
#     "B_Party",
#     "Duration",
#     "SDateTime",
#     "First_Lat",
#     "First_Long",
#     "IMEI",
#     "IMSI",
# ]
#
#
# def validate_query(query):
#
#     for key in query.keys():
#
#         if key not in ALLOWED_FIELDS:
#
#             raise ValueError(f"Invalid field: {key}")