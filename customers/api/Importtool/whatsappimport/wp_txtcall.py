"""
WhatsApp Call Processor for TXT Format Files
Handles parsing and processing of WhatsApp calls from TXT exports
Supports both normal and group calls with multiple event types
DEDICATED VERSION: Specifically designed for TXT file format
"""

import re
from datetime import datetime

# ── shared utilities (single source of truth) ────────────────────────────────
from .wp_utils import extract_phone_number, extract_mobile_code, is_metadata_line


class WhatsAppTXTCallProcessor:
    """Processor for WhatsApp call records from TXT format exports"""

    def __init__(self):
        self.current_target_no = None
        self.fromdate = None
        self.todate = None

    # ------------------------------------------------------------------
    # Delegate to module-level helpers
    # ------------------------------------------------------------------
    def extract_mobile_code(self, phone_number):
        return extract_mobile_code(phone_number)

    def extract_phone_number(self, text):
        return extract_phone_number(text)

    # ------------------------------------------------------------------
    # All original methods below – logic unchanged
    # ------------------------------------------------------------------

    def extract_call_records_from_txt(self, lines, target_no=None):
        """
        Extract call records from TXT format lines
        TXT format uses tab-separated key-value pairs
        """
        print(f"\n{'=' * 70}")
        print(f"📞 TXT CALL PROCESSOR - EXTRACTING CALL RECORDS")
        print(f"{'=' * 70}")
        print(f"📊 Input Statistics:")
        print(f"   ├─ Total lines to process: {len(lines)}")
        print(f"   └─ Target number: {target_no}")

        records = []
        i = 0
        call_counter = 0

        call_positions = []
        for idx, line in enumerate(lines):
            if line.strip() == "Call":
                call_positions.append(idx)

        print(f"\n🔍 Pre-scan Results:")
        print(
            f"   └─ Found {len(call_positions)} 'Call' markers at lines: {call_positions[:10]}{'...' if len(call_positions) > 10 else ''}")

        while i < len(lines):
            line = lines[i].strip()

            if line == "Call":
                call_counter += 1
                print(f"\n{'─' * 70}")
                print(f"🔍 Processing Call #{call_counter} (starting at line {i})")
                print(f"{'─' * 70}")

                call_events, next_index = self.parse_single_call_from_txt(lines, i, target_no, call_counter)

                if call_events:
                    print(f"✅ Successfully extracted {len(call_events)} event(s) from Call #{call_counter}")
                    for idx, event in enumerate(call_events, 1):
                        status = event.get('Status', 'unknown')
                        timestamp = event.get('DateTimeUTC', 'no timestamp')
                        from_num = event.get('Participant', 'unknown')
                        to_num = event.get('Target', 'unknown')
                        print(f"   Event {idx}: {status} | {timestamp} | {from_num} → {to_num}")
                    records.extend(call_events)
                else:
                    print(f"⚠️  No events extracted from Call #{call_counter}")

                i = next_index
            else:
                i += 1

        print(f"\n{'=' * 70}")
        print(f"✅ TXT CALL EXTRACTION COMPLETE")
        print(f"{'=' * 70}")
        print(f"📊 Final Results:")
        print(f"   ├─ Total calls processed: {call_counter}")
        print(f"   └─ Total events extracted: {len(records)}")

        if records:
            status_counts = {}
            for record in records:
                status = record.get('Status', 'unknown')
                status_counts[status] = status_counts.get(status, 0) + 1

            print(f"\n   📊 Event Type Breakdown:")
            for status, count in sorted(status_counts.items()):
                print(f"      ├─ {status}: {count}")
        else:
            print(f"\n   ⚠️  WARNING: No events were extracted!")
            print(f"   💡 Possible issues:")
            print(f"      ├─ Call data might not be present in the file")
            print(f"      ├─ File format might be different than expected")
            print(f"      └─ Check the raw file content manually")

        print(f"{'=' * 70}\n")

        return records

    def parse_single_call_from_txt(self, lines, start_index, target_no, call_number):
        """
        Parse a single call record from TXT format
        TXT format structure:
        Call
        Call Id
        <id_value>
        Call Creator
        <creator_value>
        Events
        Type
        <event_type>
        ...
        """
        events = []
        i = start_index

        print(f"   📋 Parsing call structure...")

        # Skip "Call" line
        i += 1
        if i >= len(lines):
            print(f"   ⚠️  Unexpected end of file after 'Call' marker")
            return events, i

        # Next line should be "Call Id"
        if lines[i].strip() != "Call Id":
            print(f"   ⚠️  Expected 'Call Id' at line {i}, got: '{lines[i].strip()}'")
            while i < len(lines) and lines[i].strip() != "Call Id":
                i += 1
            if i >= len(lines):
                return events, i

        i += 1
        if i >= len(lines):
            print(f"   ⚠️  Unexpected end of file after 'Call Id' label")
            return events, i

        call_id = lines[i].strip()
        print(f"   ├─ Call ID: {call_id}")
        i += 1

        call_creator = ""
        if i < len(lines) and lines[i].strip() == "Call Creator":
            i += 1
            if i < len(lines):
                call_creator = self.extract_phone_number(lines[i].strip())
                print(f"   ├─ Call Creator: {call_creator}")
                i += 1
        else:
            print(f"   ⚠️  'Call Creator' not found at expected position")

        if i < len(lines) and lines[i].strip() == "Events":
            print(f"   ├─ Found 'Events' section")
            i += 1

        event_counter = 0
        while i < len(lines):
            line = lines[i].strip()

            if line == "Call":
                print(f"   └─ End of call (next Call marker found at line {i})")
                break

            if line == "Type":
                event_counter += 1
                print(f"   ├─ Found event #{event_counter} at line {i}")
                event_data, next_index = self.parse_call_event_from_txt(
                    lines, i, call_id, call_creator, target_no, event_counter
                )
                if event_data:
                    events.append(event_data)
                    i = next_index
                else:
                    print(f"      └─ ⚠️  Event parsing failed")
                    i += 1
            else:
                i += 1

        if not events:
            print(f"   ⚠️  No events were parsed for this call")

        return events, i

    def parse_call_event_from_txt(self, lines, start_index, call_id, call_creator, target_no, event_number):
        """Parse a single call event from TXT format"""
        event_data = {
            "ID": call_id,
            "Call Creator": call_creator,
            "DateTimeUTC": "",
            "Status": "",
            "Group": "DEFAULT"
        }

        if call_creator and target_no and call_creator in target_no:
            event_data["Call_Type"] = "Call Out"
        else:
            event_data["Call_Type"] = "Call In"

        i = start_index

        # Skip "Type" label
        i += 1
        if i >= len(lines):
            return None, i

        event_type = lines[i].strip()
        event_data["Status"] = event_type
        print(f"      ├─ Type: {event_type}")
        i += 1

        timestamp = ""
        from_number = ""
        to_number = ""
        from_ip = ""
        from_port = ""
        media_type = ""
        participants = []

        while i < len(lines):
            line = lines[i].strip()

            if line in ["Type", "Call"]:
                break

            if line == "Timestamp":
                i += 1
                if i < len(lines):
                    timestamp_value = lines[i].strip()
                    timestamp_match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', timestamp_value)
                    if timestamp_match:
                        timestamp = timestamp_match.group(0)
                        event_data["DateTimeUTC"] = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                        print(f"      ├─ Timestamp: {timestamp}")
                    i += 1

            elif line == "From":
                i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line not in ["To", "From Ip", "From Port", "Media Type", "Timestamp", "Type", "Call",
                                         "Participants"]:
                        from_number = self.extract_phone_number(next_line)
                        print(f"      ├─ From: {from_number}")
                        i += 1
                    else:
                        from_number = ""

            elif line == "To":
                i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line not in ["From", "From Ip", "From Port", "Media Type", "Timestamp", "Type", "Call",
                                         "Participants"]:
                        to_number = self.extract_phone_number(next_line)
                        print(f"      ├─ To: {to_number}")
                        i += 1
                    else:
                        to_number = ""

            elif line == "From Ip":
                i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line not in ["From", "To", "From Port", "Media Type", "Timestamp", "Type", "Call",
                                         "Participants"]:
                        from_ip = next_line
                        print(f"      ├─ From IP: {from_ip}")
                        i += 1

            elif line == "From Port":
                i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line not in ["From", "To", "From Ip", "Media Type", "Timestamp", "Type", "Call",
                                         "Participants"]:
                        from_port = next_line
                        print(f"      ├─ From Port: {from_port}")
                        i += 1

            elif line == "Media Type":
                i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line not in ["From", "To", "From Ip", "From Port", "Timestamp", "Type", "Call",
                                         "Participants"]:
                        media_type = next_line
                        event_data["Type"] = media_type
                        print(f"      ├─ Media Type: {media_type}")
                        i += 1

            elif line == "Participants":
                print(f"      ├─ Parsing participants...")
                i += 1
                participants, next_i = self.parse_participants_from_txt(lines, i)
                if participants:
                    event_data["Participants"] = participants
                    participant_codes = [
                        self.extract_mobile_code(p.get("Phone Number", ""))
                        for p in participants
                    ]
                    event_data["Participants_Code"] = ", ".join(participant_codes)
                    print(f"         └─ Found {len(participants)} participant(s)")
                i = next_i

            else:
                i += 1

        # Apply target swapping logic
        self.apply_target_swapping(event_data, from_number, to_number, from_ip, from_port, target_no, call_creator)

        print(f"      └─ ✅ Event parsed successfully")
        return event_data, i

    def parse_participants_from_txt(self, lines, start_index):
        """Parse participants section from TXT format"""
        participants = []
        i = start_index
        current_participant = {}

        while i < len(lines):
            line = lines[i].strip()

            if line in ["Type", "Call", "Timestamp", "From", "To", "From Ip", "From Port", "Media Type"]:
                if current_participant and "Phone Number" in current_participant:
                    participants.append(current_participant)
                break

            if line == "Phone Number":
                if current_participant and "Phone Number" in current_participant:
                    participants.append(current_participant)
                    current_participant = {}

                i += 1
                if i < len(lines):
                    phone_line = lines[i].strip()
                    if phone_line not in ["State", "Platform", "Phone Number", "Type", "Call"]:
                        phone_number = self.extract_phone_number(phone_line)
                        current_participant = {"Phone Number": phone_number}
                        i += 1
                    else:
                        current_participant = {}
                else:
                    break

            elif line == "State":
                i += 1
                if i < len(lines):
                    state_line = lines[i].strip()
                    if state_line not in ["Platform", "Phone Number", "State", "Type", "Call"]:
                        if current_participant:
                            current_participant["State"] = state_line
                        i += 1
                else:
                    break

            elif line == "Platform":
                i += 1
                if i < len(lines):
                    platform_line = lines[i].strip()
                    if platform_line not in ["Phone Number", "State", "Platform", "Type", "Call"]:
                        if current_participant:
                            current_participant["Platform"] = platform_line
                        i += 1
                else:
                    break

            else:
                i += 1

        if current_participant and "Phone Number" in current_participant:
            participants.append(current_participant)

        return participants, i

    def apply_target_swapping(self, event_data, from_number, to_number, from_ip, from_port, target_no, call_creator):
        """Apply target swapping logic to determine participant and target"""

        if from_number:
            if target_no and from_number in target_no:
                event_data["Participant"] = to_number if to_number else ""
                event_data["Target"] = from_number
                event_data["Participant_Code"] = self.extract_mobile_code(to_number) if to_number else ""
                event_data["Target_Code"] = self.extract_mobile_code(from_number)
                if from_ip:
                    event_data["Target IP"] = from_ip
                if from_port:
                    event_data["Target Port"] = from_port
            else:
                event_data["Participant"] = from_number
                event_data["Target"] = to_number if to_number else ""
                event_data["Participant_Code"] = self.extract_mobile_code(from_number)
                event_data["Target_Code"] = self.extract_mobile_code(to_number) if to_number else ""
                if from_ip:
                    event_data["Participant IP"] = from_ip
                if from_port:
                    event_data["Participant Port"] = from_port
        else:
            if target_no and to_number and to_number not in target_no:
                event_data["Participant"] = to_number
                event_data["Target"] = target_no
                event_data["Participant_Code"] = self.extract_mobile_code(to_number)
                event_data["Target_Code"] = self.extract_mobile_code(target_no)
                if from_ip:
                    event_data["Participant IP"] = from_ip
                if from_port:
                    event_data["Participant Port"] = from_port
            else:
                event_data["Participant"] = call_creator if call_creator != target_no else ""
                event_data["Target"] = to_number if to_number else ""
                event_data["Participant_Code"] = self.extract_mobile_code(call_creator) if call_creator else ""
                event_data["Target_Code"] = self.extract_mobile_code(to_number) if to_number else ""
                if from_ip:
                    event_data["Target IP"] = from_ip
                if from_port:
                    event_data["Target Port"] = from_port