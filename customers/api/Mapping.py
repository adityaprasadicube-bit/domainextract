# from .models import Nexus, CallDetailRecord, CellTower, MobileOperator, MccMnc, ImeiDetails, \
#     CrimeInformation, UserAccess, LRNCode, SMSHeader
# from .utilities import fetch_landline_json, fetch_isd_json
#
# def validating_number(cdr):
#     bp_num = cdr[number]
#     number_details = {}
#     if bp_num.isdigit():
#         if len(bp_num) == 10 and bp_num[0] in '6789':
#             Lrn_code = cdr["LRN"]
#             if Lrn_code and len(Lrn_code) == 4:
#                 lrn_code_numbers.add(Lrn_code)
#             else:
#                 b_mobile_code = number_details['numberdetails']
#                 bp_code_numbers.add(b_mobile_code)
#
#         elif len(bp_num) == 10 and bp_num.startswith('140'):
#             number_details['numberdetails'] = {'Provider': 'Telemarketing', 'Type': 'Telemarketing'}
#
#         elif len(bp_num) == 10 and (bp_num.startswith('1800') or bp_num.startswith('1860')):
#            number_details['numberdetails'] = {'Provider': 'Toll Free', 'Type': 'Toll Free'}
#
#         elif len(bp_num) == 10:
#             if not landline_json:
#                 landline_json = fetch_landline_json()
#                 sorted_ll_codes = sorted(landline_json.keys(), key=len, reverse=True)
#             for ll_code in sorted_ll_codes:
#                 if bp_num.startswith(ll_code):
#                     City = landline_json[ll_code]['City']
#                     State = landline_json[ll_code]['State']
#                     number_details['numberdetails'] = {'Provider': State + '-' + City, 'Type': 'Landline',
#                                              'City': City,
#                                              'State': State}
#                     break
#
#         elif len(bp_num) > 10:
#             if not isd_json:
#                 isd_json = fetch_isd_json()
#                 sorted_isd_codes = sorted(isd_json.keys(), key=len, reverse=False)
#             for isd_code in sorted_isd_codes:
#                 if bp_num.startswith(isd_code):
#                     name = isd_json[isd_code]['name']
#                     code = isd_json[isd_code]['code']
#                     number_details['numberdetails'] = {'Provider': name + '-' + code, 'Type': 'ISD', 'Country': name,
#                                              'Code': code}
#                     break
#
#         elif len(bp_num) == 5 and bp_num.startswith('5'):
#             number_details['numberdetails'] = {'Provider': 'SMS Short Code', 'Type': 'SMS Short Code'}
#
#         elif len(bp_num) in (3, 4):
#            number_details['numberdetails'] = {'Provider': 'Customer Care', 'Type': 'Customer Care'}
#     else:
#         if '-' in bp_num:
#             sms_code_ary = bp_num.split('-')
#             if len(sms_code_ary) > 1:
#                 number_details['SMS_Code'] = sms_code_ary[1]
#                 sms_headers.add(sms_code_ary[1])
#                 if len(sms_code_ary) > 2:
#                     number_details['SMS_Type'] = sms_code_ary[2]
#         elif bp_num.startswith('*') or bp_num.startswith('#'):
#             number_details['numberdetails'] = {'Provider': 'MMI/USSD', 'Type': 'MMI/USSD'}
#         elif any(c.isalpha() for c in bp_num):
#             number_details['numberdetails']= {'Provider': 'Service', 'Type': 'Service'}
#         else:
#             number_details['numberdetails']= {'Provider': 'Unknown', 'Type': 'Unknown'}
#     # endregion
#
# #