# cdrapp/serializers.py
"""
Complete serializers for CDR application
Add this to your existing serializers.py file
"""
from dataclasses import asdict

from rest_framework import serializers
from bson import Decimal128
from datetime import datetime, date

from .models import IPdrNexus, IPRecord


# ============================================================
# FILE UPLOAD SERIALIZER (MISSING FROM YOUR CODE)
# ============================================================

class SimplifiedFileUploadSerializer(serializers.Serializer):
    """
    Serializer for CDR file upload endpoint

    Accepts either file paths or Base64 encoded content
    """
    arealocation = serializers.CharField(
        max_length=200,
        required=True,
        help_text="Area or location identifier (e.g., 'Hyderabad', 'Mumbai')",
        error_messages={
            'required': 'Area location is required',
            'blank': 'Area location cannot be blank'
        }
    )

    crimename = serializers.CharField(
        max_length=200,
        required=True,
        help_text="Crime name or case reference (e.g., 'Theft_Case_123')",
        error_messages={
            'required': 'Crime name is required',
            'blank': 'Crime name cannot be blank'
        }
    )

    files = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=True,
        min_length=1,
        help_text="List of file paths or Base64 encoded file content",
        error_messages={
            'required': 'At least one file must be provided',
            'min_length': 'At least one file must be provided'
        }
    )

    def validate_arealocation(self, value):
        """Validate area location"""
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Area location cannot be empty")

        # Remove special characters that might cause issues
        invalid_chars = ['<', '>', ':', '"', '|', '?', '*']
        for char in invalid_chars:
            if char in value:
                raise serializers.ValidationError(
                    f"Area location contains invalid character: {char}"
                )

        return value

    def validate_crimename(self, value):
        """Validate crime name"""
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Crime name cannot be empty")

        # Remove special characters
        invalid_chars = ['<', '>', ':', '"', '|', '?', '*']
        for char in invalid_chars:
            if char in value:
                raise serializers.ValidationError(
                    f"Crime name contains invalid character: {char}"
                )

        return value

    def validate_files(self, value):
        """Validate files list"""
        if not value:
            raise serializers.ValidationError("At least one file must be provided")

        # Check if all entries are non-empty strings
        for idx, file_data in enumerate(value):
            if not isinstance(file_data, str):
                raise serializers.ValidationError(
                    f"File {idx + 1} must be a string (path or Base64)"
                )
            if not file_data.strip():
                raise serializers.ValidationError(
                    f"File {idx + 1} cannot be empty"
                )

        return value


# ============================================================
# CUSTOM FIELD FOR BSON DECIMAL128
# ============================================================

class Decimal128Field(serializers.Field):
    """
    Custom serializer field to handle BSON Decimal128 values
    Used for latitude/longitude coordinates
    """

    def to_representation(self, value):
        """Convert Decimal128 to float for JSON serialization"""
        if isinstance(value, Decimal128):
            return float(value.to_decimal())
        return float(value) if value is not None else None

    def to_internal_value(self, data):
        """Convert input to Decimal128 for MongoDB"""
        try:
            return Decimal128(str(data))
        except Exception:
            raise serializers.ValidationError("Invalid decimal value")


# ============================================================
# CALL RECORD SERIALIZER (YOUR EXISTING CODE)
# ============================================================

class CallRecordSerializer(serializers.Serializer):
    """Serializer for CDR (Call Detail Record)"""

    _id = serializers.CharField(required=False, allow_null=True)
    A_Party = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    a_mobile_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    B_Party = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    b_mobile_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    SDateTime = serializers.DateTimeField(required=False, allow_null=True)
    EDateTime = serializers.DateTimeField(required=False, allow_null=True)
    Duration = serializers.IntegerField(required=False, allow_null=True)
    SDate = serializers.DateField(required=False, allow_null=True)
    STime = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    FileCallType = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    Call_Type = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    LRN = serializers.IntegerField(required=False, allow_null=True)
    First_CGI = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    Last_CGI = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    IMSI = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    IMSI_CODE = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    IMEI = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    Con_Type = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    First_Lat = Decimal128Field(required=False, allow_null=True)
    First_Long = Decimal128Field(required=False, allow_null=True)
    Last_Lat = Decimal128Field(required=False, allow_null=True)
    Last_Long = Decimal128Field(required=False, allow_null=True)
    FileServiceType = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    CallForward = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    IMEI_TAC = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    seq_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def create(self, validated_data):
        """Create CallRecord instance from validated data"""
        from .models import CallRecord
        return CallRecord(**validated_data)

    def update(self, instance, validated_data):
        """Update CallRecord instance"""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        return instance


# ============================================================
# CRIME RECORD SERIALIZER (YOUR EXISTING CODE)
# ============================================================

class CrimeRecordSerializer(serializers.Serializer):
    """Serializer for Crime Registry"""

    _id = serializers.CharField(required=False, allow_null=True)
    Crime = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    AreaLocation = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    seq_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def create(self, validated_data):
        """Create CrimeRecord instance"""
        from .models import CrimeRecord
        return CrimeRecord(**validated_data)

    def update(self, instance, validated_data):
        """Update CrimeRecord instance"""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        return instance


# ============================================================
# USER RECORD SERIALIZER (YOUR EXISTING CODE)
# ============================================================

class UserRecordSerializer(serializers.Serializer):
    """Serializer for User Access Mapping"""

    _id = serializers.CharField(required=False, allow_null=True)
    UserID = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    seq_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def create(self, validated_data):
        """Create UserRecord instance"""
        from .models import UserRecord
        return UserRecord(**validated_data)

    def update(self, instance, validated_data):
        """Update UserRecord instance"""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        return instance


# ============================================================
# RESPONSE SERIALIZERS (For API documentation)
# ============================================================

class UploadResponseSerializer(serializers.Serializer):
    """Serializer for upload response (for documentation)"""
    status = serializers.CharField(help_text="success or error")
    message = serializers.CharField(help_text="Human-readable message")
    files_processed = serializers.IntegerField(required=False)
    processing_time = serializers.CharField(required=False)
    crime_name = serializers.CharField(required=False)
    area_location = serializers.CharField(required=False)
    warnings = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Non-critical warnings"
    )
    errors = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Error messages"
    )


class HealthCheckResponseSerializer(serializers.Serializer):
    """Serializer for health check response"""
    status = serializers.CharField(help_text="healthy or unhealthy")
    timestamp = serializers.DateTimeField()
    checks = serializers.DictField(
        child=serializers.DictField(),
        help_text="Individual component checks"
    )

class IPdrNexusSerializer(serializers.Serializer):
    _id = serializers.CharField()
    IPDR = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    FromDate = serializers.DateTimeField(required=False, allow_null=True)
    ToDate = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    Inserted = serializers.IntegerField(required=False, allow_null=True)
    InsertedAt = serializers.DateTimeField(required=False, allow_null=True)
    MaxDur = serializers.IntegerField(required=False, allow_null=True)
    MinDur = serializers.IntegerField(required=False, allow_null=True)
    RecordType = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    Skipped = serializers.IntegerField(required=False, allow_null=True)
    Duplicate = serializers.IntegerField(required=False, allow_null=True)
    CrimeID = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    UserAccessID = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    Day = serializers.IntegerField(required=False, allow_null=True)
    Month = serializers.IntegerField(required=False, allow_null=True)
    Year = serializers.IntegerField(required=False, allow_null=True)

    def create(self, validated_data):
        """Create and return a new IPdrNexus dataclass instance."""
        return IPdrNexus(**validated_data)

    def update(self, instance, validated_data):
        """Update existing dataclass instance fields."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        return instance

    def to_representation(self, instance):
        """Convert dataclass to dict for JSON output."""
        if hasattr(instance, 'to_dict'):
            return instance.to_dict()
        return asdict(instance)

class IPRecordSerializer(serializers.Serializer):
    IPDR = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    MSISDN = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    MSISDN_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    Destination_ip = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    Destination_port = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    SDateTime = serializers.DateTimeField(required=False, allow_null=True)
    EDateTime = serializers.DateTimeField(required=False, allow_null=True)
    Duration = serializers.IntegerField(required=False, allow_null=True)
    TowerID = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    IMEI = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    IMEI_TAC = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    IMSI = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    IMSI_CODE = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    DataUpload = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    DataDownload = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    Source_ip = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    Source_port = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    Translated_ip = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    Translated_port = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    ContactNo = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    NameOfPersonOrOrganization = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    AddressOfPersonOrOrganization = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def create(self, validated_data):
        """Create and return a new IPRecord dataclass instance."""
        return IPRecord(**validated_data)

    def update(self, instance, validated_data):
        """Update an existing dataclass instance."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        return instance

    def to_representation(self, instance):
        """Convert dataclass to dictionary for API responses."""
        if hasattr(instance, 'to_dict'):
            return instance.to_dict()
        return asdict(instance)