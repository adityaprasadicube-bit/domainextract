from rest_framework import serializers

from ..models import Nexus


class TowerDumpNexusSerializer(serializers.Serializer):
    id = serializers.CharField()
    CrimeID = serializers.CharField(allow_blank=True, required=False)
    Day = serializers.IntegerField(required=False)
    Duplicate = serializers.IntegerField(required=False)
    FromDate = serializers.DateTimeField(required=False)
    ImsiCode = serializers.CharField(allow_blank=True, required=False)
    Inserted = serializers.IntegerField(required=False)
    InsertedAt = serializers.DateTimeField(required=False)
    MaxDur = serializers.IntegerField(required=False)
    MinDur = serializers.IntegerField(required=False)
    Month = serializers.IntegerField(required=False)
    RecordType = serializers.CharField(allow_blank=True, required=False)
    Skipped = serializers.IntegerField(required=False)
    ToDate = serializers.DateTimeField(required=False)
    TowerID = serializers.CharField(allow_blank=True, required=False)
    UserAccessID = serializers.CharField(allow_blank=True, required=False)
    Year = serializers.IntegerField(required=False)

    def create(self, validated_data):

        return Nexus(**validated_data).save()

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance

from rest_framework import serializers

class TowerDumpDetailRecordSerializer(serializers.Serializer):
    id = serializers.CharField()
    A_Party = serializers.CharField(required=False, allow_blank=True)
    a_mobile_code = serializers.CharField(required=False, allow_blank=True)
    B_Party = serializers.CharField(required=False, allow_blank=True)
    b_mobile_code = serializers.CharField(required=False, allow_blank=True)
    SDateTime = serializers.DateTimeField(required=False)
    EDateTime = serializers.DateTimeField(required=False)
    Duration = serializers.IntegerField(required=False)
    SDate = serializers.DateTimeField(required=False)
    STime = serializers.CharField(required=False, allow_blank=True)
    FileCallType = serializers.CharField(required=False, allow_blank=True)
    Call_Type = serializers.CharField(required=False, allow_blank=True)
    LRN = serializers.CharField(required=False, allow_blank=True)
    First_CGI = serializers.CharField(required=False, allow_blank=True)
    Last_CGI = serializers.CharField(required=False, allow_blank=True)
    IMEI = serializers.CharField(required=False, allow_blank=True)
    IMSI = serializers.CharField(required=False, allow_blank=True)
    IMSI_CODE = serializers.CharField(required=False, allow_blank=True)
    Con_Type = serializers.CharField(required=False, allow_blank=True)
    First_Lat = serializers.DecimalField(required=False, max_digits=10, decimal_places=5)
    First_Long = serializers.DecimalField(required=False, max_digits=10, decimal_places=5)
    Last_Lat = serializers.DecimalField(required=False, max_digits=10, decimal_places=5)
    Last_Long = serializers.DecimalField(required=False, max_digits=10, decimal_places=5)
    FileServiceType = serializers.CharField(required=False, allow_blank=True)
    IMEI_TAC = serializers.CharField(required=False, allow_blank=True)
    seq_id = serializers.CharField(required=True)
