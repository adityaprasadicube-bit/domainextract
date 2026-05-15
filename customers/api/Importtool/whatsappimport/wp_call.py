"""
WhatsApp Call Processor
Handles parsing and processing of WhatsApp calls from HTML exports
Supports both normal and group calls with multiple event types
"""

import re
from datetime import datetime

# ── shared utilities (single source of truth) ────────────────────────────────
from .wp_utils import extract_phone_number, extract_mobile_code, is_metadata_line


class WhatsAppCallProcessor:
    """Processor for WhatsApp call records with group call support"""

    def __init__(self):
        self.current_target_no = None
        self.fromdate = None
        self.todate = None

    # ------------------------------------------------------------------
    # Delegate to module-level helpers so call-sites inside the class
    # can still use self.extract_phone_number / self.extract_mobile_code /
    # self.is_metadata_line without any change to the rest of the code.
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

    def extract_call_records_group_format(self, lines, target_no=None):
        """Extract call records and store in group call format"""
        records = []
        i = 0

        while i < len(lines):
            line = lines[i]

            if line.strip() == "Call":
                call_events, next_index = self.parse_single_call_group_format(lines, i, target_no)
                if call_events:
                    records.extend(call_events)
                    i = next_index
                else:
                    i += 1
            else:
                i += 1

        return records

    def parse_single_call_group_format(self, lines, start_index, target_no):
        """Parse a single call record and create multiple documents for each event"""
        events = []
        i = start_index

        # Skip "Call" line
        i += 1
        if i >= len(lines):
            return events, i

        # Next line should be "Call Id"
        if lines[i].strip() != "Call Id":
            return events, i

        i += 1
        if i >= len(lines):
            return events, i

        # Get the Call Id value
        while i < len(lines) and self.is_metadata_line(lines[i]):
            i += 1

        if i < len(lines):
            call_id = lines[i].strip()
            if self.is_metadata_line(call_id):
                id_pattern = r'[A-F0-9]{32}'
                match = re.search(id_pattern, call_id)
                call_id = match.group(0) if match else ""
            i += 1
        else:
            call_id = ""

        # Get Call Creator
        call_creator = ""
        if i < len(lines) and lines[i].strip() == "Call Creator":
            i += 1
            while i < len(lines) and self.is_metadata_line(lines[i].strip()):
                i += 1
            if i < len(lines):
                call_creator = self.extract_phone_number(lines[i].strip())
                i += 1

        # Skip "Events" line if present
        if i < len(lines) and lines[i].strip() == "Events":
            i += 1

        # Parse all events for this call
        while i < len(lines):
            line = lines[i].strip()

            if line == "Call":
                break

            if line == "Type":
                event_data, next_index = self.parse_call_event_group_format(lines, i, call_id, call_creator, target_no)
                if event_data:
                    events.append(event_data)
                    i = next_index
                else:
                    i += 1
            else:
                i += 1

        return events, i

    def parse_call_event_group_format(self, lines, start_index, call_id, call_creator, target_no):
        """Parse a single call event and format it for MongoDB storage"""
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

        while i < len(lines) and self.is_metadata_line(lines[i]):
            i += 1

        if i >= len(lines):
            return None, i

        event_type = lines[i].strip()
        event_data["Status"] = event_type
        i += 1

        if event_type in ["offer", "accept", "terminate", "av_switch", "reject"]:
            event_data, i = self.parse_basic_call_event(lines, i, event_data, target_no, call_creator)
        elif event_type == "group_update":
            event_data, i = self.parse_group_update_event(lines, i, event_data, target_no, call_creator)

        return event_data, i

    def parse_basic_call_event(self, lines, start_index, event_data, target_no, call_creator):
        """Parse basic call events like offer, accept, terminate, av_switch, reject with proper target swapping"""
        i = start_index
        timestamp = ""
        from_number = ""
        to_number = ""
        from_ip = ""
        from_port = ""
        media_type = ""
        participants_found = False
        participants = []

        while i < len(lines):
            line = lines[i].strip()

            if line in ["Type", "Call"]:
                break

            if self.is_metadata_line(line):
                i += 1
                continue

            if line == "Timestamp":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    timestamp_match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', lines[i])
                    if timestamp_match:
                        timestamp = timestamp_match.group(0)
                        event_data["DateTimeUTC"] = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                i += 1

            elif line == "From":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line in ["To", "From Ip", "From Port", "Media Type", "Timestamp", "Type", "Call",
                                     "Participants"]:
                        from_number = ""
                    else:
                        from_number = self.extract_phone_number(next_line)
                        i += 1
                else:
                    from_number = ""

            elif line == "To":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line in ["From", "From Ip", "From Port", "Media Type", "Timestamp", "Type", "Call",
                                     "Participants"]:
                        to_number = ""
                    else:
                        to_number = self.extract_phone_number(next_line)
                        i += 1
                else:
                    to_number = ""

            elif line == "From Ip":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line not in ["From", "To", "From Port", "Media Type", "Timestamp", "Type", "Call",
                                         "Participants"]:
                        from_ip = next_line
                        i += 1

            elif line == "From Port":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line not in ["From", "To", "From Ip", "Media Type", "Timestamp", "Type", "Call",
                                         "Participants"]:
                        from_port = next_line
                        i += 1

            elif line == "Media Type":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line not in ["From", "To", "From Ip", "From Port", "Timestamp", "Type", "Call",
                                         "Participants"]:
                        media_type = next_line
                        event_data["Type"] = media_type
                        i += 1

            elif line == "Participants":
                participants_found = True
                i += 1
                current_participant = {}
                participant_numbers = []

                while i < len(lines):
                    line_content = lines[i].strip()

                    if self.is_metadata_line(line_content):
                        i += 1
                        continue

                    if line_content in ["Type", "Call", "Timestamp", "From", "To", "From Ip", "From Port",
                                        "Media Type"]:
                        break

                    if line_content == "Phone Number":
                        if current_participant and "Phone Number" in current_participant:
                            participants.append(current_participant)
                            participant_numbers.append(current_participant["Phone Number"])
                            current_participant = {}

                        i += 1
                        while i < len(lines) and self.is_metadata_line(lines[i]):
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

                    elif line_content == "State":
                        i += 1
                        while i < len(lines) and self.is_metadata_line(lines[i]):
                            i += 1

                        if i < len(lines):
                            state_line = lines[i].strip()
                            if state_line not in ["Platform", "Phone Number", "State", "Type", "Call"]:
                                if current_participant:
                                    current_participant["State"] = state_line
                                i += 1
                        else:
                            break

                    elif line_content == "Platform":
                        i += 1
                        while i < len(lines) and self.is_metadata_line(lines[i]):
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
                    participant_numbers.append(current_participant["Phone Number"])

                if participants:
                    event_data["Participants"] = participants
                    participant_codes = [self.extract_mobile_code(p) for p in participant_numbers]
                    event_data["Participants_Code"] = ", ".join(participant_codes)

                continue

            else:
                i += 1

        # Apply target swapping logic
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
                event_data["Participant_Code"] = self.extract_mobile_code(
                    call_creator) if call_creator and call_creator != target_no else ""
                event_data["Target_Code"] = self.extract_mobile_code(to_number) if to_number else ""
                if from_ip:
                    event_data["Target IP"] = from_ip
                if from_port:
                    event_data["Target Port"] = from_port

        return event_data, i

    def parse_group_update_event(self, lines, start_index, event_data, target_no, call_creator):
        """Parse group_update events with participants and proper target swapping"""
        i = start_index
        timestamp = ""
        from_number = ""
        to_number = ""
        from_ip = ""
        from_port = ""

        while i < len(lines):
            line = lines[i].strip()
            if line in ["Type", "Call"]:
                break
            if self.is_metadata_line(line):
                i += 1
                continue

            if line == "Timestamp":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    timestamp_match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', lines[i])
                    if timestamp_match:
                        timestamp = timestamp_match.group(0)
                        event_data["DateTimeUTC"] = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                i += 1

            elif line == "From":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line in ["To", "Participants", "From Ip", "From Port", "Timestamp", "Type", "Call"]:
                        from_number = ""
                    else:
                        from_number = self.extract_phone_number(next_line)
                        i += 1

            elif line == "To":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line in ["From", "Participants", "From Ip", "From Port", "Timestamp", "Type", "Call"]:
                        to_number = ""
                    else:
                        to_number = self.extract_phone_number(next_line)
                        i += 1

            elif line == "From Ip":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line not in ["From", "To", "From Port", "Participants", "Timestamp", "Type", "Call"]:
                        from_ip = next_line
                        i += 1

            elif line == "From Port":
                i += 1
                while i < len(lines) and self.is_metadata_line(lines[i]):
                    i += 1
                if i < len(lines):
                    next_line = lines[i].strip()
                    if next_line not in ["From", "To", "From Ip", "Participants", "Timestamp", "Type", "Call"]:
                        from_port = next_line
                        i += 1

            elif line == "Participants":
                i += 1
                participants = []
                current_participant = {}
                participant_numbers = []

                while i < len(lines):
                    line_content = lines[i].strip()

                    if self.is_metadata_line(line_content):
                        i += 1
                        continue

                    if line_content in ["Type", "Call", "Timestamp", "From", "To", "From Ip", "From Port"]:
                        break

                    if line_content == "Phone Number":
                        if current_participant and "Phone Number" in current_participant:
                            participants.append(current_participant)
                            participant_numbers.append(current_participant["Phone Number"])
                            current_participant = {}

                        i += 1
                        while i < len(lines) and self.is_metadata_line(lines[i]):
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

                    elif line_content == "State":
                        i += 1
                        while i < len(lines) and self.is_metadata_line(lines[i]):
                            i += 1

                        if i < len(lines):
                            state_line = lines[i].strip()
                            if state_line not in ["Platform", "Phone Number", "State", "Type", "Call"]:
                                if current_participant:
                                    current_participant["State"] = state_line
                                i += 1
                        else:
                            break

                    elif line_content == "Platform":
                        i += 1
                        while i < len(lines) and self.is_metadata_line(lines[i]):
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
                    participant_numbers.append(current_participant["Phone Number"])

                if participants:
                    event_data["Participants"] = participants
                    participant_codes = [self.extract_mobile_code(p) for p in participant_numbers]
                    event_data["Participants_Code"] = ", ".join(participant_codes)

                continue

            else:
                i += 1

        # Apply target swapping logic for group_update events
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
                event_data["Participant"] = to_number if to_number == target_no else ""
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

        return event_data, i