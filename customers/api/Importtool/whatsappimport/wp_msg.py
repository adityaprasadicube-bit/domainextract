"""
WhatsApp Message Processor
Handles parsing and processing of WhatsApp messages from HTML exports
Supports both normal and group messages
"""

import re
import ipaddress
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

# ── shared utilities (single source of truth) ────────────────────────────────
from .wp_utils import extract_phone_number, extract_mobile_code, is_metadata_line


class WhatsAppMessageProcessor:
    """Processor for WhatsApp message records with group message support"""

    def __init__(self):
        self.current_target_no = None
        self.fromdate = None
        self.todate = None
        self.initialize_key_mapping()

    # ------------------------------------------------------------------
    # Delegate to module-level helpers
    # ------------------------------------------------------------------
    def extract_mobile_code(self, phone_number):
        return extract_mobile_code(phone_number)

    def extract_phone_number(self, text):
        return extract_phone_number(text)

    def is_metadata_line(self, line):
        return is_metadata_line(line)

    # ------------------------------------------------------------------
    # All original methods below – logic unchanged
    # ------------------------------------------------------------------

    def initialize_key_mapping(self):
        """Initialize key mapping for field standardization"""
        self.standard_mapping = {
            "Group": "Group",
            "Target": "Recipients",
            "Participant": "Sender",
            "DateTimeUTC": "Timestamp",
            "ID": "Message Id",
            "Call_Type": "Call Type",
            "Target Device": "Target Device",
            "Participant Device": "Sender Device",
            "Type": "Type",
            "Style": "Message Style",
            "Size": "Message Size",
            "Target IP": "Target IP",
            "Target Port": "Target Port",
            "Participant IP": "Sender IP",
            "Participant Port": "Sender Port",
            "Status": "Status",
            "Group ID": "Group Id",
            "HashCode": "HashCode",
            "Call Creator": "Call Creator",
            "Participants": "Participants"
        }

        self.cleaned_to_standard = {
            self.clean_key_for_matching(pdf_key): standard_key
            for standard_key, pdf_key in self.standard_mapping.items()
        }

    def clean_key_for_matching(self, key):
        """Clean keys for matching by removing special characters"""
        return re.sub(r'[^a-zA-Z0-9]', '', key).lower()

    def find_standard_key(self, original_key):
        """Find standardized key name"""
        cleaned_original = self.clean_key_for_matching(original_key)
        return self.cleaned_to_standard.get(cleaned_original, original_key)

    def convert_utc_to_ist(self, utc_datetime_str):
        """Convert UTC datetime to IST timezone"""
        try:
            if isinstance(utc_datetime_str, str):
                utc_datetime_str = utc_datetime_str.replace('UTC', '').strip()
            elif isinstance(utc_datetime_str, datetime):
                utc_datetime_str = utc_datetime_str.strftime("%Y-%m-%d %H:%M:%S")

            formats = ['%Y-%m-%d %H:%M:%S', '%d-%m-%Y %H:%M:%S', '%Y/%m/%d %H:%M:%S']

            utc_dt = None
            for fmt in formats:
                try:
                    utc_dt = datetime.strptime(utc_datetime_str, fmt)
                    break
                except ValueError:
                    continue

            if utc_dt is None:
                return utc_datetime_str

            utc_dt = utc_dt.replace(tzinfo=timezone.utc)
            ist_dt = utc_dt + timedelta(hours=5, minutes=30)
            return ist_dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return utc_datetime_str

    def standardize_message_keys(self, message):
        """Standardize all keys in message record"""
        standardized = {}
        for key, value in message.items():
            standard_key = self.find_standard_key(key)
            if standard_key == 'DateTimeUTC' and value:
                standardized[standard_key] = value
                value = self.convert_utc_to_ist(value)
                standardized["DateTimeIST"] = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            else:
                standardized[standard_key] = value

        if 'Sender' in standardized:
            standardized['Participant_Code'] = self.extract_mobile_code(standardized['Sender'])

        if 'Group' not in standardized:
            standardized['Group'] = 'DEFAULT'

        return standardized

    def extract_account_info(self, soup):
        """Extract account information from HTML - handles both continuous and scattered metadata"""
        account_info = {}

        all_text = soup.get_text(separator='\n').strip()
        lines = [line.strip() for line in all_text.split('\n') if line.strip()]

        found_account_id = False
        found_date_range = False

        i = 0
        while i < len(lines) and (not found_account_id or not found_date_range):
            line = lines[i]

            if 'Account Identifier' in line and not found_account_id:
                if i + 1 < len(lines):
                    value = lines[i + 1].strip()
                    if value and not any(keyword in value for keyword in
                                         ['Service', 'Internal', 'Account Type', 'Generated', 'Date Range',
                                          'Message Log', 'Meta Platforms', 'WhatsApp Business']):
                        account_info['Account Identifier'] = value
                        found_account_id = True

            elif 'Date Range' in line and not found_date_range:
                if i + 1 < len(lines):
                    value = lines[i + 1].strip()
                    if ' to ' in value and 'UTC' in value:
                        account_info['Date Range'] = value
                        found_date_range = True

            elif 'Service' in line and 'WhatsApp' not in account_info.get('Service', ''):
                if i + 1 < len(lines):
                    value = lines[i + 1].strip()
                    if value and value.lower() == 'whatsapp':
                        account_info['Service'] = value

            elif 'Internal Ticket Number' in line and 'Internal Ticket Number' not in account_info:
                if i + 1 < len(lines):
                    value = lines[i + 1].strip()
                    if value and value.isdigit():
                        account_info['Internal Ticket Number'] = value

            elif 'Account Type' in line and 'Account Type' not in account_info:
                if i + 1 < len(lines):
                    value = lines[i + 1].strip()
                    if value and 'WhatsApp' in value:
                        account_info['Account Type'] = value

            i += 1

        return account_info

    def extract_message_records(self, soup, target_no=None):
        """Improved method to extract message records from HTML structure"""
        records = []

        all_text = soup.get_text(separator='\n').strip()
        lines = [line.strip() for line in all_text.split('\n') if line.strip()]
        message_records = self.parse_lines_into_records(lines)

        for message_data in message_records:
            message_data["Call_Type"] = "Msg In"
            sender = message_data.get("Sender", "")
            recipients = message_data.get("Recipients")
            group_id = message_data.get("Group ID", "")
            message_data['Call Creator'] = sender

            if message_data and self.is_valid_message_record(message_data):
                if target_no and message_data.get('Sender') and message_data['Sender'] in target_no:
                    self.apply_target_swapping(message_data, target_no)
                    message_data['Call Creator'] = sender
                    message_data["Call_Type"] = "Msg Out"

                if 'Participant_Code' not in message_data and 'Sender' in message_data:
                    message_data['Participant_Code'] = self.extract_mobile_code(message_data['Sender'])

                if recipients and group_id and target_no in recipients:
                    text = recipients
                    parts = [p.strip() for p in text.split(",")]
                    parts = [p for p in parts if p != target_no]

                    new_text = ", ".join(parts)
                    message_data['Call Creator'] = sender
                    message_data['Participant'] = new_text if len(parts) > 1 else sender
                    message_data['Target'] = target_no
                    message_data["Call_Type"] = "Msg In"
                    message_data['Participant_Code'] = self.extract_mobile_code(recipients)

                records.append(message_data)

        return records

    def parse_lines_into_records(self, lines):
        """Parse lines into message records, properly separating messages from call logs"""
        records = []
        current_record = {}
        current_field = None
        field_value = []
        i = 0
        in_call_log_section = False

        while i < len(lines):
            line = lines[i]

            if line.lower() == 'call log':
                in_call_log_section = True
                if current_record:
                    if current_field and field_value:
                        processed_value = self.process_field_value(current_field, field_value)
                        if processed_value:
                            current_record[current_field] = processed_value
                    records.append(current_record)
                    current_record = {}
                    current_field = None
                    field_value = []
                i += 1
                continue

            if in_call_log_section and self.is_record_boundary(line, i, lines):
                in_call_log_section = False

            if in_call_log_section:
                i += 1
                continue

            if self.is_record_boundary(line, i, lines):
                if current_record:
                    if current_field and field_value:
                        processed_value = self.process_field_value(current_field, field_value)
                        if processed_value:
                            current_record[current_field] = processed_value
                    records.append(current_record)

                current_record = {}
                current_field = None
                field_value = []

                if line.lower() == 'message':
                    i += 1
                    continue

            if self.is_field_name(line) and not in_call_log_section:
                if current_field and field_value:
                    processed_value = self.process_field_value(current_field, field_value)
                    if processed_value:
                        current_record[current_field] = processed_value

                current_field = self.normalize_field_name(line)
                field_value = []
            elif current_field and not in_call_log_section:
                if not self.is_metadata_line(line):
                    field_value.append(line)

            i += 1

        if current_record and not in_call_log_section:
            if current_field and field_value:
                processed_value = self.process_field_value(current_field, field_value)
                if processed_value:
                    current_record[current_field] = processed_value
            records.append(current_record)

        return records

    def is_record_boundary(self, line, index, lines):
        """Check if this line marks the start of a new record"""
        line_lower = line.lower()

        if line_lower == 'message':
            if index + 1 < len(lines):
                next_line = lines[index + 1].lower()
                if 'timestamp' in next_line:
                    return True

        if 'timestamp' in line_lower and index > 0:
            return True

        return False

    def process_field_value(self, field_name, value_lines):
        """Process field value lines into a single clean value"""
        if not value_lines:
            return None

        clean_value = value_lines[0].strip()

        if field_name in ['Sender', 'Recipients']:
            clean_value = self.extract_phone_number(clean_value)
        elif field_name == 'Timestamp':
            timestamp_match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', clean_value)
            if timestamp_match:
                ts_str = timestamp_match.group(0)
                clean_value = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        return clean_value

    def is_field_name(self, line):
        """Check if a line is a field name"""
        field_names = [
            'Timestamp', 'Message', 'Message Id', 'Message ID', 'Sender',
            'Recipients', 'Sender IP', 'Sender Port', 'Sender Device',
            'Target IP', 'Target Port', 'Target Device', 'Type',
            'Message Style', 'Message Size', 'Group ID', 'Group Id',
            'Status', 'HashCode'
        ]

        line_lower = line.lower()
        for field in field_names:
            if field.lower() == line_lower:
                return True

        return False

    def normalize_field_name(self, field_name):
        """Normalize field names to standard format"""
        field_name_lower = field_name.lower()

        if 'timestamp' in field_name_lower or 'datetime' in field_name_lower:
            return 'Timestamp'
        elif 'message id' in field_name_lower:
            return 'Message Id'
        elif 'sender' in field_name_lower and 'ip' not in field_name_lower and 'port' not in field_name_lower and 'device' not in field_name_lower:
            return 'Sender'
        elif 'recipient' in field_name_lower:
            return 'Recipients'
        elif 'sender ip' in field_name_lower:
            return 'Sender IP'
        elif 'sender port' in field_name_lower:
            return 'Sender Port'
        elif 'sender device' in field_name_lower:
            return 'Sender Device'
        elif 'target ip' in field_name_lower:
            return 'Target IP'
        elif 'target port' in field_name_lower:
            return 'Target Port'
        elif 'target device' in field_name_lower:
            return 'Target Device'
        elif 'type' in field_name_lower:
            return 'Type'
        elif 'message style' in field_name_lower:
            return 'Message Style'
        elif 'message size' in field_name_lower:
            return 'Message Size'
        elif 'group id' in field_name_lower:
            return 'Group ID'
        elif 'status' in field_name_lower:
            return 'Status'
        elif 'hashcode' in field_name_lower:
            return 'HashCode'
        else:
            return field_name

    def apply_target_swapping(self, message_data, target_no):
        """Apply target number swapping logic for both single and group messages"""
        sender = message_data.get("Sender", "")
        recipients = message_data.get("Recipients", "")

        Participant_Code = self.extract_mobile_code(sender)
        Target_Code = self.extract_mobile_code(recipients)

        if ',' in recipients:
            recipient_list = [r.strip() for r in recipients.split(',')]
        else:
            recipient_list = [recipients] if recipients else []

        if target_no and sender:
            target_found_in_sender = any(target in sender for target in [target_no, target_no.replace('91', '')])

            if target_found_in_sender:
                message_data['Sender'] = recipients
                message_data['Recipients'] = sender
                message_data['Participant_Code'] = Target_Code

                if 'Sender Device' in message_data:
                    message_data['Target Device'] = message_data.pop('Sender Device')
                if 'Sender IP' in message_data:
                    message_data['Target IP'] = message_data.pop('Sender IP')
                if 'Sender Port' in message_data:
                    message_data['Target Port'] = message_data.pop('Sender Port')
            else:
                message_data['Participant_Code'] = Participant_Code
        else:
            message_data['Participant_Code'] = Participant_Code

    def is_valid_message_record(self, message):
        """Check if a message record has minimum required fields"""
        has_timestamp = 'Timestamp' in message and message['Timestamp']
        has_sender = 'Sender' in message and message['Sender']
        has_recipients = 'Recipients' in message and message['Recipients']

        return has_timestamp and has_sender and has_recipients