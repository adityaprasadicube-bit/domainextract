from rest_framework import serializers
from .models import Nexus, CellTower, ImeiDetails, MobileOperator, CallDetailRecord, MccMnc
from decimal import Decimal, InvalidOperation
from .utilities import mcc_mnc_extract

class SafeDecimalField(serializers.DecimalField):
    def to_representation(self, value):
        try:
            return super().to_representation(value)
        except (InvalidOperation, TypeError, ValueError):
            return None  # or 0 or float(value) if needed

class NexusSerializer(serializers.Serializer):
    id = serializers.CharField()
    CDRNo_Or_ImeiNo = serializers.CharField()
    CrimeID= serializers.CharField()
    UserAccessID = serializers.CharField()
    Day = serializers.IntegerField()
    Duplicate = serializers.IntegerField()
    FromDate = serializers.DateTimeField()
    Inserted = serializers.IntegerField()
    InsertedAt = serializers.DateTimeField()
    MaxDur = serializers.IntegerField()
    MinDur = serializers.IntegerField()
    Month = serializers.IntegerField()
    RecordType = serializers.CharField()
    Skipped = serializers.IntegerField()
    Tac_Or_Mobile_Code = serializers.CharField()
    ToDate = serializers.DateTimeField()
    Year = serializers.IntegerField()
    ImsiCode = serializers.CharField()
    Name = serializers.CharField()

    # Tac_Or_Mobile_Code_Info = serializers.SerializerMethodField(help_text="Detailed info of CDRNo_Or_ImeiNo")
    # ImsiCode_Info = serializers.SerializerMethodField(help_text="Detailed info of ImsiCode")

    # def get_ImsiCode_Info(self, obj):
    #     try:
    #         a_code = MccMnc.objects.get(mccmnc_temp=obj.ImsiCode)
    #         a_code_data = MccMncSerializer(a_code).data
    #         if a_code_data['operator'] == 'VODAFONE' or a_code_data['operator'] == 'IDEA':
    #             a_code_data['operator'] = 'VI'
    #         return a_code_data
    #     except MccMnc.DoesNotExist:
    #         return None


    # def get_Tac_Or_Mobile_Code_Info(self, obj):
    #     if obj.RecordType == "CDR":
    #         try:
    #             a_code = MobileOperator.objects.get(id=obj.Tac_Or_Mobile_Code)
    #             a_code_data = MobileOperatorSerializer(a_code).data
    #             if a_code_data['Operator'] == 'VODAFONE' or a_code_data['Operator'] == 'IDEA':
    #                 a_code_data['Operator'] = 'VI'
    #             return a_code_data
    #         except MobileOperator.DoesNotExist:
    #             return None
    #     else:
    #         try:
    #             imei_info = ImeiDetails.objects.get(id=obj.Tac_Or_Mobile_Code)
    #             return DeviceInfoSerializer(imei_info).data
    #         except ImeiDetails.DoesNotExist:
    #             return None





class CallDetailRecordSerializer(serializers.Serializer):
    id = serializers.CharField()
    A_Party = serializers.CharField()
    a_mobile_code = serializers.CharField()
    B_Party = serializers.CharField()
    b_mobile_code = serializers.CharField()
    SDateTime = serializers.DateTimeField()
    EDateTime = serializers.DateTimeField()
    Duration = serializers.IntegerField()
    SDate = serializers.DateTimeField()
    STime = serializers.CharField()
    FileCallType = serializers.CharField()
    Call_Type = serializers.CharField()
    LRN = serializers.CharField()
    First_CGI = serializers.CharField()
    Last_CGI = serializers.CharField()
    IMEI = serializers.CharField()
    IMSI = serializers.CharField()
    IMSI_CODE = serializers.CharField()
    Con_Type = serializers.CharField()
    First_Lat = SafeDecimalField(max_digits=9, decimal_places=5, required=False)
    First_Long = SafeDecimalField(max_digits=9, decimal_places=5, required=False)
    Last_Lat = SafeDecimalField(max_digits=9, decimal_places=5, required=False)
    Last_Long = SafeDecimalField(max_digits=9, decimal_places=5, required=False)
    FileServiceType = serializers.CharField()
    IMEI_TAC = serializers.CharField()
    seq_id = serializers.CharField()

    # A_Mobile_Code_Detail = serializers.SerializerMethodField(help_text="Detailed info of A_Party Mobile code")
    # B_Mobile_Code_Detail = serializers.SerializerMethodField(help_text="Detailed info of B_Party Mobile code")
    # LRN_Detail = serializers.SerializerMethodField(help_text="Detailed info of LRN")
    # IMEI_Detail = serializers.SerializerMethodField(help_text="Detailed info of IMEI")
    # First_CGI_Detail = serializers.SerializerMethodField(help_text="Detailed info of First_CGI")
    # Last_CGI_Detail = serializers.SerializerMethodField(help_text="Detailed info of Last_CGI")

    # def get_A_Mobile_Code_Detail(self, obj):
    #     if obj.a_mobile_code:
    #         try:
    #             a_code = MobileOperator.objects.get(id=obj.a_mobile_code)
    #             return MobileOperatorSerializer(a_code).data
    #         except MobileOperator.DoesNotExist:
    #             return None

    # def get_B_Mobile_Code_Detail(self, obj):
    #     try:
    #         b_code = MobileOperator.objects.get(id=obj.b_mobile_code)
    #         return MobileOperatorSerializer(b_code).data
    #     except MobileOperator.DoesNotExist:
    #         return None

    # def get_IMEI_Detail(self, obj):
    #     try:
    #         imei_info = ImeiDetails.objects.get(id=obj.IMEI_TAC)
    #         return DeviceInfoSerializer(imei_info).data
    #     except ImeiDetails.DoesNotExist:
    #         return None

    # def get_First_CGI_Detail(self, obj):
    #     try:
    #         tower = CellTower.objects.get(id=obj.First_CGI)
    #         return CellTowerSerializer(tower).data
    #     except CellTower.DoesNotExist:
    #         mcc,mnc= mcc_mnc_extract(obj.First_CGI)
    #         return { "Mcc" : mcc, "Mnc" : mnc }
    #
    #
    # def get_Last_CGI_Detail(self, obj):
    #     try:
    #         tower = CellTower.objects.get(id=obj.Last_CGI)
    #         return CellTowerSerializer(tower).data
    #     except CellTower.DoesNotExist:
    #         return None


from rest_framework import serializers

class CellTowerSerializer(serializers.Serializer):
    id = serializers.CharField()
    LATITUDE = serializers.CharField(allow_null=True)
    LONGITUDE = serializers.CharField(allow_null=True)
    AZIMUTH = serializers.CharField(allow_null=True)
    ADDRESS = serializers.CharField(allow_null=True)
    MAIN_CITY = serializers.CharField(allow_null=True)
    SUB_CITY = serializers.CharField(allow_null=True)
    TYPE = serializers.CharField(allow_null=True)
    DATE_TIME = serializers.DateTimeField(allow_null=True)
    LAC = serializers.CharField(allow_null=True)
    CELLID = serializers.CharField(allow_null=True)
    CIRCLE = serializers.CharField(allow_null=True)
    OPERATOR = serializers.CharField(allow_null=True)
    MCC = serializers.CharField(allow_null=True)
    MNC = serializers.CharField(allow_null=True)
    MCCMNC = serializers.CharField(allow_null=True)

    def to_representation(self, instance):
        data = {
            "id": getattr(instance, "id", None),
            "LATITUDE": getattr(instance, "LATITUDE", None),
            "LONGITUDE": getattr(instance, "LONGITUDE", None),
            "AZIMUTH": getattr(instance, "AZIMUTH", None),
            "ADDRESS": getattr(instance, "ADDRESS", None),
            "MAIN_CITY": getattr(instance, "MAIN_CITY", None),
            "SUB_CITY": getattr(instance, "SUB_CITY", None),
            "TYPE": getattr(instance, "TYPE", None),
            "DATE_TIME": getattr(instance, "DATE_TIME", None),
            "LAC": getattr(instance, "LAC", None),
            "CELLID": getattr(instance, "CELLID", None),
            "CIRCLE": getattr(instance, "CIRCLE", None),
            "OPERATOR": getattr(instance, "OPERATOR", None),
            "MCC": getattr(instance, "MCC", None),
            "MNC": getattr(instance, "MNC", None),
            "MCCMNC": getattr(instance, "MCCMNC", None),
        }
        return data




class DeviceInfoSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Cell tower unique ID")
    brand = serializers.CharField()
    manufacturer = serializers.CharField()
    datealloted = serializers.DateTimeField(format="%d-%m-%Y %H:%M:%S", input_formats=["%d-%m-%Y %H:%M:%S"])
    os = serializers.CharField()
    devicetype = serializers.CharField()
    simslots = serializers.CharField()

class MobileOperatorSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Cell tower unique ID")
    Circle = serializers.CharField(required=True)
    Operator = serializers.CharField(required=True)

class CrimeInformationSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Cell tower unique ID")
    Crime = serializers.CharField(required=True)
    AreaLocation = serializers.CharField(required=True)

class UserAccessSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Cell tower unique ID")
    UserID = serializers.CharField(required=True)

# class ImsiInformationSerializer(serializers.Serializer):
#     IMSI = serializers.CharField(required=True)
#     IMSI_CODE = serializers.CharField(required=True)
#     seq_id = serializers.CharField(required=True)

class MccMncSerializer(serializers.Serializer):
    mcc = serializers.CharField(required=True)
    mnc = serializers.CharField(required=True)
    mccmnc = serializers.CharField(required=True)
    mccmnc_temp = serializers.CharField(required=True)
    circle = serializers.CharField(required=True)
    operator = serializers.CharField(required=True)

class LRNCodeSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="LRN code unique ID")
    circle = serializers.CharField(required=True)
    operator = serializers.CharField(required=True)

class SMSHeaderSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="SMS Header unique ID")
    address = serializers.CharField(required=True)
    type = serializers.CharField(required=True)

class CDRFilterSerializer(serializers.Serializer):
    seq_id = serializers.CharField()
    filter = serializers.BooleanField(required=False)
    from_date = serializers.DateTimeField(required=False)
    to_date = serializers.DateTimeField(required=False)
    min_duration = serializers.IntegerField(required=False)
    max_duration = serializers.IntegerField(required=False)
