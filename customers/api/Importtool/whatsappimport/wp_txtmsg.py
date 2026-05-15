"""
TXT WhatsApp Message Parsing Utilities
Contains:
- _extract_messages_from_txt_lines
- _apply_message_target_swapping
These functions are format-specific helpers for TXT-based WhatsApp exports.
"""

# ── shared utilities (single source of truth) ────────────────────────────────
from .wp_utils import extract_phone_number, extract_mobile_code


def _extract_messages_from_txt_lines(lines, message_processor, target_no):
    """
    Extract message records from parsed TXT lines
    Applies COMPLETE target swapping logic
    """
    messages = []
    current_message = {}
    current_field = None

    field_names = [
        'Timestamp', 'Message Id', 'Sender', 'Recipients',
        'Sender Ip', 'Sender Port', 'Sender Device',
        'Type', 'Message Style', 'Message Size',
        'Encrypted Message Content', 'Group Id',
        'Target Ip', 'Target Port', 'Target Device'
    ]

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if not line:
            i += 1
            continue

        # New message marker
        if line == 'Message':
            if current_message and 'Timestamp' in current_message:
                messages.append(current_message)
            current_message = {}
            current_field = None
            i += 1
            continue

        # Field name
        if line in field_names:
            current_field = line
            i += 1
            continue

        # Field value
        if current_field:
            current_message[current_field] = line
            current_field = None

        i += 1

    # Append last message
    if current_message and 'Timestamp' in current_message:
        messages.append(current_message)

    processed_messages = []

    for msg in messages:
        if not msg.get('Timestamp') or not msg.get('Message Id'):
            continue

        # Normalize phone numbers
        if 'Sender' in msg:
            msg['Sender'] = extract_phone_number(msg['Sender'])
        if 'Recipients' in msg:
            msg['Recipients'] = extract_phone_number(msg['Recipients'])

        msg['Call_Type'] = 'Msg In'
        msg['Call Creator'] = msg.get('Sender', '')

        sender = msg.get('Sender', '')
        recipients = msg.get('Recipients', '')
        group_id = msg.get('Group Id', '')

        # TARGET SENT MESSAGE → swap
        if target_no and sender and sender in target_no:
            _apply_message_target_swapping(msg, target_no, message_processor)
            msg['Call_Type'] = 'Msg Out'
            msg['Call Creator'] = sender

        # GROUP MESSAGE where target is recipient
        elif recipients and group_id and target_no and target_no in recipients:
            parts = [p.strip() for p in recipients.split(",") if p.strip() != target_no]

            msg['Participant'] = ", ".join(parts) if parts else sender
            msg['Target'] = target_no
            msg['Call_Type'] = 'Msg In'
            msg['Call Creator'] = sender
            msg['Participant_Code'] = extract_mobile_code(msg['Participant'])

        # Default participant code
        if 'Participant_Code' not in msg and sender:
            msg['Participant_Code'] = extract_mobile_code(sender)

        # Group fallback
        msg.setdefault('Group', 'DEFAULT')

        # Normalize timestamp
        if 'Timestamp' in msg:
            msg['Timestamp'] = msg['Timestamp'].replace(' UTC', '').strip()

        processed_messages.append(msg)

    return processed_messages


def _apply_message_target_swapping(message_data, target_no, message_processor):
    """
    Swap sender/recipient fields when TARGET is the sender (TXT format)
    """
    sender = message_data.get("Sender", "")
    recipients = message_data.get("Recipients", "")

    participant_code = extract_mobile_code(sender)
    target_code = extract_mobile_code(recipients)

    target_variants = {target_no, target_no.replace('91', '')}
    target_in_sender = any(t in sender for t in target_variants)

    if target_in_sender:
        # Swap numbers
        message_data['Sender'], message_data['Recipients'] = recipients, sender
        message_data['Participant'] = recipients
        message_data['Target'] = sender

        # Swap codes
        message_data['Participant_Code'] = target_code
        message_data['Target_Code'] = participant_code

        # Swap device/network metadata
        if 'Sender Device' in message_data:
            message_data['Target Device'] = message_data.pop('Sender Device')

        if 'Sender IP' in message_data or 'Sender Ip' in message_data:
            ip_val = message_data.pop('Sender IP', None) or message_data.pop('Sender Ip', None)
            if ip_val:
                message_data['Target IP'] = ip_val

        if 'Sender Port' in message_data:
            message_data['Target Port'] = message_data.pop('Sender Port')

    else:
        # Normal (no swap)
        message_data['Participant'] = sender
        message_data['Target'] = recipients
        message_data['Participant_Code'] = participant_code
        message_data['Target_Code'] = target_code

        if 'Sender IP' in message_data or 'Sender Ip' in message_data:
            ip_val = message_data.pop('Sender IP', None) or message_data.pop('Sender Ip', None)
            if ip_val:
                message_data['Participant IP'] = ip_val

        if 'Sender Port' in message_data:
            message_data['Participant Port'] = message_data.pop('Sender Port')

        if 'Sender Device' in message_data:
            message_data['Participant Device'] = message_data.pop('Sender Device')