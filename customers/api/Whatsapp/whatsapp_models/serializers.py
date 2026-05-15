from mongoengine import StringField
from rest_framework import serializers
from decimal import Decimal, InvalidOperation
from .whatsapp_models import WhatsAppNexus, WhatsAppDetailsRecord

class SafeDecimalField(serializers.DecimalField):
    def to_representation(self, value):
        try:
            return super().to_representation(value)
        except (InvalidOperation, TypeError, ValueError):
            return None

# Option 1: Use ModelSerializer (Recommended)
class WhatsappNexusSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppNexus
        fields = '__all__'

# Option 2: Keep as Serializer but fix the structure

class WhatsappNexusSerializer(serializers.Serializer):
    _id = serializers.CharField()
    CrimeName = serializers.CharField(allow_blank=True, required=False)
    FileName = serializers.CharField(allow_blank=True, required=False)
    Matched = serializers.IntegerField(default=0)
    FromDateIST = serializers.DateTimeField(source='FromDate', required=False)
    ToDateIST = serializers.DateTimeField(source='ToDate', required=False)
    Inserted = serializers.IntegerField(default=0)
    RecordType = serializers.CharField(allow_blank=True, required=False)
    CreatedAt = serializers.DateTimeField(required=False)
    Target=serializers.CharField(source='TargetNo',allow_blank=True, required=False)
    Day = serializers.IntegerField(required=False)
    Month = serializers.IntegerField(required=False)
    Year = serializers.IntegerField(required=False)

    def create(self, validated_data):
        return WhatsAppNexus(**validated_data).save()

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance

class WhatsAppDetailsRecordSerializer(serializers.Serializer):
    id = serializers.CharField(required=False)  # Added missing field
    Group = serializers.CharField(required=False, allow_blank=True)
    Target = serializers.CharField(required=False, allow_blank=True)
    Call_Creator = serializers.CharField(required=False, allow_blank=True)
    Participant = serializers.CharField(required=False, allow_blank=True)
    Participant_Code= serializers.CharField(required=False, allow_blank=True)
    Participants = serializers.CharField(required=False, allow_blank=True)
    Participants_Code = serializers.CharField(required=False, allow_blank=True)
    DateTimeUTC = serializers.DateTimeField(required=False)
    DateTimeIST = serializers.DateTimeField(required=False)
    ID = serializers.CharField(required=False, allow_blank=True)
    Target_Device = serializers.CharField(required=False, allow_blank=True)
    Participant_Device = serializers.CharField(required=False, allow_blank=True)
    Type = serializers.CharField(required=False, allow_blank=True)
    Call_Type = serializers.CharField(required=False, allow_blank=True)
    Style = serializers.CharField(required=False, allow_blank=True)
    Size = serializers.IntegerField(required=False)
    Target_IP = serializers.CharField(required=False, allow_blank=True)
    Target_Port = serializers.IntegerField(required=False)
    Participant_IP = serializers.CharField(required=False, allow_blank=True)
    Participant_Port = serializers.IntegerField(required=False)
    Status = serializers.CharField(required=False, allow_blank=True)
    Group_ID = serializers.CharField(required=False, allow_blank=True)
    HashCode = serializers.CharField(required=False, allow_blank=True)
    seq_id = serializers.CharField(required=True)  # Added required seq_id

class WhatsAppFilterSerializer(serializers.Serializer):
    """Optional filter serializer for searching WhatsApp messages."""
    seq_id = serializers.CharField()
    from_date = serializers.DateTimeField(required=False)
    to_date = serializers.DateTimeField(required=False)
    min_size = serializers.IntegerField(required=False)
    max_size = serializers.IntegerField(required=False)


from rest_framework import serializers
from .whatsapp_models import (
    WhatsAppInfoNexus,
    WhatsAppConnection,
    WhatsAppContacts,
    WhatsAppGroups
)


# ==========================================================
# 1️⃣ WhatsApp Info Nexus Serializer
# ==========================================================

class WhatsAppInfoNexusSerializer(serializers.Serializer):
    id = serializers.CharField(source="id", read_only=True)

    CrimeName = serializers.CharField(required=False, allow_blank=True)
    FileName = serializers.CharField(required=False, allow_blank=True)
    RecordType = serializers.CharField(required=False, allow_blank=True)

    TargetNo = serializers.CharField(required=False, allow_blank=True)
    Target_code = serializers.CharField(required=False, allow_blank=True)

    Emails = serializers.ListField(
        child=serializers.CharField(),
        required=False
    )

    FromDate = serializers.DateTimeField(required=False, allow_null=True)
    ToDate = serializers.DateTimeField(required=False, allow_null=True)

    ConnectionsCount = serializers.IntegerField(required=False)
    GroupsCount = serializers.IntegerField(required=False)
    SymmetricContactsCount = serializers.IntegerField(required=False)
    AsymmetricContactsCount = serializers.IntegerField(required=False)

    Day = serializers.IntegerField(required=False)
    Month = serializers.IntegerField(required=False)
    Year = serializers.IntegerField(required=False)

    CreatedAt = serializers.DateTimeField(required=False)


# ==========================================================
# 2️⃣ WhatsApp Connection Serializer
# (seq_id hidden from API response)
# ==========================================================

class WhatsAppConnectionSerializer(serializers.Serializer):
    Device_Id = serializers.CharField(required=False)
    Service_start = serializers.CharField( required=False)
    Device_Type = serializers.CharField(required=False)
    App_Version = serializers.CharField(required=False)
    Device_OS_Build_Number = serializers.CharField( required=False)
    Connection_State = serializers.CharField(required=False)
    Last_seen = serializers.CharField(required=False)
    Last_IP = serializers.CharField( required=False)

    TargetNo = serializers.CharField(required=False)

    # 🔥 seq_id intentionally NOT exposed


# ==========================================================
# 3️⃣ WhatsApp Contacts Serializer
# ==========================================================

class WhatsAppContactsSerializer(serializers.Serializer):
    id = serializers.CharField(source="_id", required=False)
    TargetNo = serializers.CharField()
    symmetric_contacts = serializers.ListField(
        child=serializers.JSONField(),
        required=False
    )
    symmetric_contact_codes = serializers.ListField(
        child=serializers.JSONField(),
        required=False
    )
    asymmetric_contacts = serializers.ListField(
        child=serializers.JSONField(),
        required=False
    )
    asymmetric_contact_codes = serializers.ListField(
        child=serializers.JSONField(),
        required=False
    )
    seq_id = serializers.CharField()

    # 🔥 seq_id hidden


# ==========================================================
# 4️⃣ WhatsApp Groups Serializer
# ==========================================================

class WhatsAppGroupsSerializer(serializers.Serializer):
    ID = serializers.CharField(required=False)
    TargetNo = serializers.CharField(required=False)
    Creation = serializers.CharField(required=False)
    Size = serializers.CharField(required=False)
    Subject = serializers.CharField(required=False)
    Description = serializers.CharField(required=False, allow_blank=True)




