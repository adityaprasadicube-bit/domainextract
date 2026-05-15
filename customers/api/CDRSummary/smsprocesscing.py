
from ..serializers import SMSHeaderSerializer

from ..models import SMSHeader


def process_sms_details_provider_only(bp_num, lookupSms=None):
    """
    Process SMS B_Party and return only Provider and Type,
    keeping all internal SMS processing logic intact.

    Args:
        bp_num (str): SMS B_Party string like "VK-RBLCRD"
        lookupSms (dict, optional): Pre-fetched SMSHeader lookup

    Returns:
        dict: {'Provider': ..., 'Type': ...}
    """
    # Default unknown
    result = {'Provider': 'SMS-Header-Unknown', 'Type': 'SMS-Header-Unknown'}

    if isinstance(bp_num, str) and '-' in bp_num:
        parts = bp_num.split('-')
        if len(parts) > 1:
            sms_code = parts[1].upper()
            sms_type_code = parts[2].upper() if len(parts) > 2 else None

            sms_type = None
            address = None

            # First, try the lookup dictionary if provided
            if lookupSms and sms_code in lookupSms:
                address = lookupSms[sms_code].get('address')
                sms_type = lookupSms[sms_code].get('type')

            # If not in lookup, try database
            if not address or not sms_type:
                try:
                    db_record = SMSHeader.objects.get(id=sms_code)
                    address = db_record.address
                    sms_type = db_record.type
                except SMSHeader.DoesNotExist:
                    pass

            # Map type code if provided
            type_mapping = {'P': 'Promotional/Service', 'S': 'Service Implicit', 'T': 'Transactional',
                            'G': 'Government'}
            if sms_type_code:
                sms_type = type_mapping.get(sms_type_code, sms_type)

            if address or sms_type:
                result['Provider'] = address or 'SMS-Header-Unknown'
                result['Type'] = sms_type or 'SMS-Header-Unknown'

    return result


def process_all_b_party_types(bp_num, lookupSms=None, landline_json=None, isd_json=None):
    """
    Complete B_Party processing function that handles all types including SMS

    Args:
        bp_num (str): B_Party number/string
        lookupSms (dict): SMS header lookup dictionary
        landline_json (dict): Landline codes lookup
        isd_json (dict): ISD codes lookup

    Returns:
        dict: Complete B_Party processing result
    """

    result = {
        'B_Party_Detail': None,
        'SMS_Code': None,
        'SMS_Type': None,
        'type_category': None
    }

    if not isinstance(bp_num, str):
        result['B_Party_Detail'] = {'Provider': 'Invalid', 'Type': 'Invalid'}
        return result

    # Check if it's a numeric B_Party
    if bp_num.isdigit():
        # Mobile numbers (10 digits starting with 6,7,8,9)
        if len(bp_num) == 10 and bp_num[0] in '6789':
            result['type_category'] = 'mobile'
            # Note: Mobile processing would be handled by LRN/Mobile Code lookup in main code

        # Telemarketing
        elif len(bp_num) == 10 and bp_num.startswith('140'):
            result['type_category'] = 'telemarketing'
            result['B_Party_Detail'] = {'Provider': 'Telemarketing', 'Type': 'Telemarketing'}

        # Toll Free
        elif len(bp_num) == 10 and (bp_num.startswith('1800') or bp_num.startswith('1860')):
            result['type_category'] = 'toll_free'
            result['B_Party_Detail'] = {'Provider': 'Toll Free', 'Type': 'Toll Free'}

        # Landline (10 digits, not mobile/special)
        elif len(bp_num) == 10:
            result['type_category'] = 'landline'
            if landline_json:
                sorted_ll_codes = sorted(landline_json.keys(), key=len, reverse=True)
                for ll_code in sorted_ll_codes:
                    if bp_num.startswith(ll_code):
                        city = landline_json[ll_code]['City']
                        state = landline_json[ll_code]['State']
                        result['B_Party_Detail'] = {
                            'Provider': state + '-' + city,
                            'Type': 'Landline',
                            'City': city,
                            'State': state
                        }
                        break

        # ISD (International, more than 10 digits)
        elif len(bp_num) > 10:
            result['type_category'] = 'isd'
            if isd_json:
                sorted_isd_codes = sorted(isd_json.keys(), key=len, reverse=False)
                for isd_code in sorted_isd_codes:
                    if bp_num.startswith(isd_code):
                        name = isd_json[isd_code]['name']
                        code = isd_json[isd_code]['code']
                        result['B_Party_Detail'] = {
                            'Provider': name + '-' + code,
                            'Type': 'ISD',
                            'Country': name,
                            'Code': code
                        }
                        break

        # SMS Short Code
        elif len(bp_num) == 5 and bp_num.startswith('5'):
            result['type_category'] = 'sms_short_code'
            result['B_Party_Detail'] = {'Provider': 'SMS Short Code', 'Type': 'SMS Short Code'}

        # Customer Care (3-4 digits)
        elif len(bp_num) in (3, 4):
            result['type_category'] = 'customer_care'
            result['B_Party_Detail'] = {'Provider': 'Customer Care', 'Type': 'Customer Care'}

    else:
        # Non-numeric B_Party processing

        # SMS Headers (contains '-')
        if '-' in bp_num:
            result['type_category'] = 'sms_header'
            sms_result = process_sms_details_provider_only(bp_num, lookupSms)
            result.update(sms_result)

        # USSD/MMI codes
        elif bp_num.startswith('*') or bp_num.startswith('#'):
            result['type_category'] = 'ussd_mmi'
            result['B_Party_Detail'] = {'Provider': 'MMI/USSD', 'Type': 'MMI/USSD'}

        # Service numbers (contains alphabetic characters)
        elif any(c.isalpha() for c in bp_num):
            result['type_category'] = 'service'
            result['B_Party_Detail'] = {'Provider': 'Service', 'Type': 'Service'}

        # Unknown format
        else:
            result['type_category'] = 'unknown'
            result['B_Party_Detail'] = {'Provider': 'Unknown', 'Type': 'Unknown'}

    return result


def collect_sms_headers_from_cdr_batch(cdr_info):
    """
    Collect all SMS headers from CDR batch for efficient lookup

    Args:
        cdr_info (list): List of CDR records

    Returns:
        set: Set of unique SMS header codes
    """
    sms_headers = set()

    for cdr in cdr_info:
        bp_num = cdr.get("B_Party", "")
        if isinstance(bp_num, str) and '-' in bp_num:
            sms_code_array = bp_num.split('-')
            if len(sms_code_array) > 1:
                sms_headers.add(sms_code_array[1])

    return sms_headers


def integrate_sms_processing_in_existing_code(cdr_info):
    """
    Integration function that matches your existing code structure
    This replaces the SMS processing section in your CallDetailRecordDetailView
    """

    # Collect SMS headers for batch lookup
    sms_headers = collect_sms_headers_from_cdr_batch(cdr_info)

    # Batch lookup SMS headers (this matches your existing pattern)
    lookupSms = {}
    if len(sms_headers) > 0:
        sms_codes = SMSHeader.objects.filter(id__in=sms_headers)
        sms_codesdata = SMSHeaderSerializer(sms_codes, many=True).data
        lookupSms = {item["id"]: item for item in sms_codesdata}

    # Process each CDR record
    for cdr in cdr_info:
        bp_num = cdr.get("B_Party", "")

        # Only process SMS headers here (other types handled by existing logic)
        if isinstance(bp_num, str) and '-' in bp_num:
            sms_result = process_sms_details_provider_only(bp_num, lookupSms)

            # Update CDR with SMS information
            if sms_result['SMS_Code']:
                cdr['SMS_Code'] = sms_result['SMS_Code']

            if sms_result['SMS_Type']:
                cdr['SMS_Type'] = sms_result['SMS_Type']

            # Note: B_Party_Detail will be set later in your main processing loop

    return cdr_info, lookupSms
